from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings, load_settings
from .models import (
    AuthContext,
    BackendStatus,
    ClaimCreate,
    ExcerptCreate,
    IndexStateRequest,
    PublishRequest,
    QuestionCreate,
    ReportCreate,
    ResearchSessionCreate,
    ReviewRequest,
    SearchResponse,
    SourceCreate,
)
from .service import RegistryService

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


class QuestionStatusUpdate(BaseModel):
    status: str


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

    @app.get("/questions/{question_id}", response_class=HTMLResponse)
    def question_detail(question_id: str, request: Request) -> HTMLResponse:
        include_private = _is_admin(request)
        question = _safe_get(lambda: service.get_question(question_id, include_private=include_private))
        claims = service.list_claims_for_question(question.id, include_private=include_private)
        reports = service.list_reports_for_question(question.id, include_private=include_private)
        fresh_reports = [report for report in reports if not report.is_stale]
        stale_reports = [report for report in reports if report.is_stale]
        child_questions = service.list_child_questions(question.id, include_private=include_private)
        sessions = service.list_sessions_for_question(question.id, include_private=include_private)
        return TEMPLATES.TemplateResponse(
            request,
            "question_detail.html",
            {
                "request": request,
                "question": question,
                "claims": claims,
                "reports": reports,
                "fresh_reports": fresh_reports,
                "stale_reports": stale_reports,
                "child_questions": child_questions,
                "sessions": sessions,
                "is_admin": include_private,
            },
        )

    @app.get("/sources/{source_id}", response_class=HTMLResponse)
    def source_detail(source_id: str, request: Request) -> HTMLResponse:
        source = _safe_get(lambda: service.get_source(source_id, include_private=_is_admin(request)))
        excerpts = service.list_excerpts_for_source(source.id, include_private=_is_admin(request))
        return TEMPLATES.TemplateResponse(
            request,
            "source_detail.html",
            {"request": request, "source": source, "excerpts": excerpts, "is_admin": _is_admin(request)},
        )

    @app.get("/excerpts/{excerpt_id}", response_class=HTMLResponse)
    def excerpt_detail(excerpt_id: str, request: Request) -> HTMLResponse:
        excerpt = _safe_get(lambda: service.get_excerpt(excerpt_id, include_private=_is_admin(request)))
        source = service.get_source(excerpt.source_id, include_private=True)
        return TEMPLATES.TemplateResponse(
            request,
            "excerpt_detail.html",
            {"request": request, "excerpt": excerpt, "source": source, "is_admin": _is_admin(request)},
        )

    @app.get("/annotations/{annotation_id}", response_class=HTMLResponse)
    def annotation_detail(annotation_id: str, request: Request) -> HTMLResponse:
        return excerpt_detail(annotation_id, request)

    @app.get("/claims/{claim_id}", response_class=HTMLResponse)
    def claim_detail(claim_id: str, request: Request) -> HTMLResponse:
        claim = _safe_get(lambda: service.get_claim(claim_id, include_private=_is_admin(request)))
        excerpts = service.list_excerpts_for_claim(claim.id, include_private=True)
        sources = {excerpt.source_id: service.get_source(excerpt.source_id, include_private=True) for excerpt in excerpts}
        question = service.get_question(claim.question_id, include_private=True)
        return TEMPLATES.TemplateResponse(
            request,
            "claim_detail.html",
            {"request": request, "claim": claim, "question": question, "excerpts": excerpts, "sources": sources, "is_admin": _is_admin(request)},
        )

    @app.get("/findings/{finding_id}", response_class=HTMLResponse)
    def finding_detail(finding_id: str, request: Request) -> HTMLResponse:
        return claim_detail(finding_id, request)

    @app.get("/reports/{report_id}", response_class=HTMLResponse)
    def report_detail(report_id: str, request: Request) -> HTMLResponse:
        include_private = _is_admin(request)
        report = _safe_get(lambda: service.get_report(report_id, include_private=include_private))
        question = service.get_question(report.question_id, include_private=True)
        claims = [service.get_claim(claim_id, include_private=True) for claim_id in report.claim_ids]
        sources = {source_id: service.get_source(source_id, include_private=True) for source_id in report.source_ids}
        follow_up_questions = []
        for question_id in report.guidance.follow_up_question_ids:
            try:
                follow_up_questions.append(service.get_question(question_id, include_private=include_private))
            except (KeyError, PermissionError):
                continue
        return TEMPLATES.TemplateResponse(
            request,
            "report_detail.html",
            {
                "request": request,
                "report": report,
                "question": question,
                "claims": claims,
                "sources": sources,
                "follow_up_questions": follow_up_questions,
                "is_admin": include_private,
            },
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

    @app.get("/api/questions/{question_id}")
    def api_get_question(question_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_question(question_id, include_private=include_private and auth is not None, auth=auth))

    @app.post("/api/questions")
    def api_create_question(payload: QuestionCreate, auth: AuthContext = Depends(_ingest_guard)):
        return service.create_question(payload, auth=auth)

    @app.post("/api/questions/{question_id}/status")
    def api_set_question_status(question_id: str, payload: QuestionStatusUpdate, auth: AuthContext = Depends(_ingest_guard)):
        service.set_question_status(question_id, payload.status)
        return {"status": "ok"}

    @app.get("/api/sessions/{session_id}")
    def api_get_session(session_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_session(session_id, include_private=include_private and auth is not None, auth=auth))

    @app.post("/api/sessions")
    def api_create_session(payload: ResearchSessionCreate, auth: AuthContext = Depends(_ingest_guard)):
        return service.create_session(payload, auth=auth)

    @app.get("/api/sources/{source_id}")
    def api_get_source(source_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_source(source_id, include_private=include_private and auth is not None, auth=auth))

    @app.post("/api/sources")
    def api_create_source(payload: SourceCreate, auth: AuthContext = Depends(_ingest_guard)):
        return service.create_source(payload, auth=auth)

    @app.get("/api/excerpts/{excerpt_id}")
    def api_get_excerpt(excerpt_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_excerpt(excerpt_id, include_private=include_private and auth is not None, auth=auth))

    @app.get("/api/annotations/{annotation_id}")
    def api_get_annotation(annotation_id: str, request: Request, include_private: bool = False):
        return api_get_excerpt(annotation_id, request, include_private=include_private)

    @app.post("/api/excerpts")
    def api_create_excerpt(payload: ExcerptCreate, auth: AuthContext = Depends(_ingest_guard)):
        return service.create_excerpt(payload, auth=auth)

    @app.get("/api/claims/{claim_id}")
    def api_get_claim(claim_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_claim(claim_id, include_private=include_private and auth is not None, auth=auth))

    @app.get("/api/findings/{finding_id}")
    def api_get_finding(finding_id: str, request: Request, include_private: bool = False):
        return api_get_claim(finding_id, request, include_private=include_private)

    @app.post("/api/claims")
    def api_create_claim(payload: ClaimCreate, auth: AuthContext = Depends(_ingest_guard)):
        return service.create_claim(payload, auth=auth)

    @app.get("/api/reports/{report_id}")
    def api_get_report(report_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_report(report_id, include_private=include_private and auth is not None, auth=auth))

    @app.post("/api/reports")
    def api_create_report(payload: ReportCreate, auth: AuthContext = Depends(_ingest_guard)):
        return service.create_report(payload, auth=auth)

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


def _ingest_guard(request: Request) -> AuthContext:
    return _require_auth(request, "ingest")


def _publish_guard(request: Request) -> AuthContext:
    return _require_auth(request, "publish")


def _admin_guard(request: Request) -> AuthContext:
    auth = _optional_auth(request)
    if auth is None:
        raise HTTPException(status_code=401, detail="admin token required")
    if not auth.is_admin:
        raise HTTPException(status_code=403, detail="admin scope required")
    return auth


app = create_app()
