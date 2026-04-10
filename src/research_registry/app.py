from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings, load_settings
from .models import (
    AnnotationCreate,
    AuthContext,
    BackendStatus,
    FindingCreate,
    IndexStateRequest,
    PublishRequest,
    ReportCreate,
    ReportCompileCreate,
    ReviewRequest,
    RunCreate,
    SearchResponse,
    SourceCreate,
)
from .service import RegistryService

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    service = RegistryService(settings.db_path)
    service.initialize()
    service.set_backend_status(
        BackendStatus(
            name="registry-server",
            kind="hosted_default" if settings.default_backend_url and settings.default_backend_url.rstrip("/") == settings.public_base_url.rstrip("/") else "server",
            selection_source="server_runtime",
            url=settings.public_base_url,
            namespace_kind="user",
            namespace_id="local",
            api_key_present=False,
            org=settings.backend_org,
        )
    )

    app = FastAPI(title="Research Registry")
    app.state.settings = settings
    app.state.service = service
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, q: str = "") -> HTMLResponse:
        dashboard = service.dashboard(include_private=False, public_index_only=True)
        results = service.search(q, include_private=False, public_index_only=True) if q.strip() else SearchResponse(query="", hits=[])
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "results": results,
                "dashboard": dashboard,
                "is_admin": _is_admin(request),
            },
        )

    @app.get("/public/{namespace_slug}", response_class=HTMLResponse)
    def public_namespace(request: Request, namespace_slug: str, q: str = "") -> HTMLResponse:
        dashboard = service.dashboard(include_private=False, namespace_slug=namespace_slug)
        results = service.search(q, include_private=False, namespace_slug=namespace_slug) if q.strip() else SearchResponse(query="", hits=[])
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "results": results,
                "dashboard": dashboard,
                "is_admin": _is_admin(request),
                "namespace_slug": namespace_slug,
            },
        )

    @app.get("/sources/{source_id}", response_class=HTMLResponse)
    def source_detail(source_id: str, request: Request) -> HTMLResponse:
        source = _safe_get(lambda: service.get_source(source_id, include_private=_is_admin(request)))
        linked_annotations = [annotation for annotation in service.dashboard(include_private=_is_admin(request), limit=100).annotations if annotation.source_id == source.id]
        return TEMPLATES.TemplateResponse(
            request,
            "source_detail.html",
            {"request": request, "source": source, "annotations": linked_annotations, "is_admin": _is_admin(request)},
        )

    @app.get("/annotations/{annotation_id}", response_class=HTMLResponse)
    def annotation_detail(annotation_id: str, request: Request) -> HTMLResponse:
        annotation = _safe_get(lambda: service.get_annotation(annotation_id, include_private=_is_admin(request)))
        source = service.get_source(annotation.source_id, include_private=True)
        return TEMPLATES.TemplateResponse(
            request,
            "annotation_detail.html",
            {"request": request, "annotation": annotation, "source": source, "is_admin": _is_admin(request)},
        )

    @app.get("/findings/{finding_id}", response_class=HTMLResponse)
    def finding_detail(finding_id: str, request: Request) -> HTMLResponse:
        finding = _safe_get(lambda: service.get_finding(finding_id, include_private=_is_admin(request)))
        annotations = [service.get_annotation(annotation_id, include_private=True) for annotation_id in finding.annotation_ids]
        sources = {annotation.source_id: service.get_source(annotation.source_id, include_private=True) for annotation in annotations}
        return TEMPLATES.TemplateResponse(
            request,
            "finding_detail.html",
            {"request": request, "finding": finding, "annotations": annotations, "sources": sources, "is_admin": _is_admin(request)},
        )

    @app.get("/reports/{report_id}", response_class=HTMLResponse)
    def report_detail(report_id: str, request: Request) -> HTMLResponse:
        report = _safe_get(lambda: service.get_report(report_id, include_private=_is_admin(request)))
        findings = [service.get_finding(finding_id, include_private=True) for finding_id in report.finding_ids]
        sources = {source_id: service.get_source(source_id, include_private=True) for source_id in report.source_ids}
        return TEMPLATES.TemplateResponse(
            request,
            "report_detail.html",
            {"request": request, "report": report, "findings": findings, "sources": sources, "is_admin": _is_admin(request)},
        )

    @app.get("/admin/login", response_class=HTMLResponse)
    def admin_login(request: Request) -> HTMLResponse:
        if _is_admin(request):
            return RedirectResponse("/admin", status_code=303)
        return TEMPLATES.TemplateResponse(request, "admin_login.html", {"request": request, "error": None})

    @app.post("/admin/login", response_class=HTMLResponse)
    async def admin_login_submit(request: Request, token: str = Form(default="")) -> HTMLResponse:
        if settings.admin_token and token != settings.admin_token:
            return TEMPLATES.TemplateResponse(request, "admin_login.html", {"request": request, "error": "Token mismatch"}, status_code=401)
        request.session["is_admin"] = True
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/logout")
    async def admin_logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    @app.get("/admin", response_class=HTMLResponse)
    def admin_dashboard(request: Request, q: str = "") -> HTMLResponse:
        _require_admin(request)
        dashboard = service.dashboard(include_private=True, auth=_admin_auth())
        results = service.search(q, include_private=True, auth=_admin_auth()) if q.strip() else SearchResponse(query="", hits=[])
        return TEMPLATES.TemplateResponse(
            request,
            "admin_dashboard.html",
            {"request": request, "dashboard": dashboard, "results": results},
        )

    @app.post("/admin/{kind}/{record_id}/publish")
    def admin_publish(kind: str, record_id: str, request: Request) -> RedirectResponse:
        _require_admin(request)
        service.publish(PublishRequest(kind=kind, record_id=record_id, include_in_global_index=True), auth=_admin_auth())
        return RedirectResponse(request.headers.get("referer", "/admin"), status_code=303)

    @app.post("/admin/{kind}/{record_id}/review")
    def admin_review(kind: str, record_id: str, request: Request) -> RedirectResponse:
        _require_admin(request)
        service.review(ReviewRequest(kind=kind, record_id=record_id), auth=_admin_auth())
        return RedirectResponse(request.headers.get("referer", "/admin"), status_code=303)

    @app.get("/api/search")
    def api_search(
        request: Request,
        q: str = "",
        kind: str | None = None,
        include_private: bool = False,
        namespace_slug: str | None = None,
        global_index_only: bool | None = None,
    ) -> SearchResponse:
        auth = _optional_auth(request)
        if global_index_only is None:
            global_index_only = auth is None and namespace_slug is None and not include_private
        return service.search(
            q,
            kind=kind,
            include_private=include_private and auth is not None,
            auth=auth,
            public_index_only=global_index_only,
            namespace_slug=namespace_slug,
        )

    @app.get("/api/backend/status")
    def api_backend_status(request: Request):
        auth = _optional_auth(request)
        status = service.backend_status()
        if auth is None:
            return status
        return status.model_copy(
            update={
                "namespace_kind": auth.namespace_kind,
                "namespace_id": auth.namespace_id,
                "api_key_present": auth.api_key_id is not None,
                "org": auth.actor_org_id,
            }
        )

    @app.get("/api/sources/{source_id}")
    def api_get_source(source_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_source(source_id, include_private=include_private and auth is not None, auth=auth))

    @app.get("/api/annotations/{annotation_id}")
    def api_get_annotation(annotation_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_annotation(annotation_id, include_private=include_private and auth is not None, auth=auth))

    @app.get("/api/findings/{finding_id}")
    def api_get_finding(finding_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_finding(finding_id, include_private=include_private and auth is not None, auth=auth))

    @app.get("/api/reports/{report_id}")
    def api_get_report(report_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_report(report_id, include_private=include_private and auth is not None, auth=auth))

    @app.post("/api/runs")
    def api_create_run(payload: RunCreate, auth: AuthContext = Depends(_ingest_guard)):
        return service.create_run(payload, auth=auth)

    @app.post("/api/sources")
    def api_create_source(payload: SourceCreate, auth: AuthContext = Depends(_ingest_guard)):
        return service.create_source(payload, auth=auth)

    @app.post("/api/annotations")
    def api_create_annotation(payload: AnnotationCreate, auth: AuthContext = Depends(_ingest_guard)):
        return service.create_annotation(payload, auth=auth)

    @app.post("/api/findings")
    def api_create_finding(payload: FindingCreate, auth: AuthContext = Depends(_ingest_guard)):
        return service.create_finding(payload, auth=auth)

    @app.post("/api/reports")
    def api_create_report(payload: ReportCreate, auth: AuthContext = Depends(_ingest_guard)):
        return service.create_report(payload, auth=auth)

    @app.post("/api/reports/compile")
    def api_compile_report(payload: ReportCompileCreate, auth: AuthContext = Depends(_ingest_guard)):
        return service.compile_report(payload, auth=auth)

    @app.post("/api/publish")
    def api_publish(payload: PublishRequest, auth: AuthContext = Depends(_publish_guard)):
        service.publish(payload, auth=auth)
        return {"status": "ok"}

    @app.post("/api/review")
    def api_review(payload: ReviewRequest, auth: AuthContext = Depends(_admin_guard)):
        service.review(payload, auth=auth)
        return {"status": "ok"}

    @app.post("/api/index-state")
    def api_index_state(payload: IndexStateRequest, auth: AuthContext = Depends(_admin_guard)):
        service.set_index_state(payload, auth=auth)
        return {"status": "ok"}

    return app


def _safe_get(operation):
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _is_admin(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    header_token = request.headers.get("x-admin-token")
    session_admin = bool(request.session.get("is_admin"))
    if settings.admin_token is None:
        return True
    return session_admin or header_token == settings.admin_token


def _require_admin(request: Request) -> None:
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="admin token required")


def _admin_auth() -> AuthContext:
    return AuthContext(
        is_admin=True,
        scopes=["admin", "ingest", "publish", "read_private"],
        namespace_kind="user",
        namespace_id="local",
    )


def _optional_auth(request: Request) -> AuthContext | None:
    if _is_admin(request):
        return _admin_auth()
    token = request.headers.get("x-api-key", "").strip()
    if not token:
        return None
    service: RegistryService = request.app.state.service
    try:
        return service.authenticate_api_key(token)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _require_auth(request: Request, scope: str | None = None) -> AuthContext:
    auth = _optional_auth(request)
    if auth is None:
        raise HTTPException(status_code=401, detail="api key required")
    if scope and not auth.has_scope(scope):  # type: ignore[arg-type]
        raise HTTPException(status_code=403, detail=f"{scope} scope required")
    return auth


def _admin_guard(request: Request) -> AuthContext:
    _require_admin(request)
    return _admin_auth()


def _ingest_guard(request: Request) -> AuthContext:
    return _require_auth(request, "ingest")


def _publish_guard(request: Request) -> AuthContext:
    return _require_auth(request, "publish")


app = create_app()
