from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings, load_settings
from .application.review import (
    ExpectedRevisionMismatch,
    ExpectedStateMismatch,
    ReviewError,
    ReviewRecordNotFound,
    ResearchReviewService,
)
from .application.refresh import InvalidRefreshTransition
from .contracts.v2 import ResearchReviewRequest
from .mcp_tools import create_mcp_server
from .mcp.deep_research import create_deep_research_server
from .models import (
    ApiKeyCreate,
    AuthContext,
    BackendStatus,
    BriefResolveRequest,
    ClaimCreate,
    ExcerptCreate,
    FollowUpStatusUpdate,
    ImportBibtexRequest,
    ImportDoiRequest,
    ImportUrlRequest,
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
from .persistence.read_adapter import ReadAccess
from .web_v2 import V2WebViewService

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


class QuestionStatusUpdate(BaseModel):
    status: str


class OrganizationBootstrapRequest(BaseModel):
    org_id: str
    display_name: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    service = RegistryService(settings.database_url)
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

    mcp = create_mcp_server(
        service,
        settings=settings,
        service=service,
        default_api_key=settings.backend_api_key,
        allow_admin_fallback=False,
        streamable_http_path="/",
    )
    mcp_app = mcp.streamable_http_app()
    deep_research_mcp = create_deep_research_server(
        service,
        settings=settings,
        service=service,
        default_api_key=settings.backend_api_key,
        allow_admin_fallback=False,
        streamable_http_path="/",
    )
    deep_research_mcp_app = deep_research_mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with mcp.session_manager.run():
            async with deep_research_mcp.session_manager.run():
                yield

    # The retained v1 OpenAPI document is a compatibility contract independent
    # of the package/plugin release version.
    app = FastAPI(title="Research Registry", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.service = service
    app.state.mcp = mcp
    app.state.deep_research_mcp = deep_research_mcp
    app.state.web_v2 = V2WebViewService(service.database, settings)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
    app.mount("/mcp", mcp_app)
    app.mount("/deep-research-mcp", deep_research_mcp_app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        try:
            service.check_ready()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"storage unavailable: {exc}") from exc
        return {"status": "ready"}

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
        include_private = _is_admin(request)
        source = _safe_get(
            lambda: service.get_source(source_id, include_private=include_private)
        )
        excerpts = service.list_excerpts_for_source(
            source.id, include_private=include_private
        )
        return TEMPLATES.TemplateResponse(
            request,
            "source_detail.html",
            {"request": request, "source": source, "excerpts": excerpts, "is_admin": _is_admin(request)},
        )

    @app.get("/excerpts/{excerpt_id}", response_class=HTMLResponse)
    def excerpt_detail(excerpt_id: str, request: Request) -> HTMLResponse:
        include_private = _is_admin(request)
        excerpt = _safe_get(
            lambda: service.get_excerpt(
                excerpt_id, include_private=include_private
            )
        )
        source = _safe_get(
            lambda: service.get_source(
                excerpt.source_id, include_private=include_private
            )
        )
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
        include_private = _is_admin(request)
        claim = _safe_get(
            lambda: service.get_claim(claim_id, include_private=include_private)
        )
        excerpts = service.list_excerpts_for_claim(
            claim.id, include_private=include_private
        )
        sources = {
            excerpt.source_id: _safe_get(
                lambda source_id=excerpt.source_id: service.get_source(
                    source_id, include_private=include_private
                )
            )
            for excerpt in excerpts
        }
        question = _safe_get(
            lambda: service.get_question(
                claim.question_id, include_private=include_private
            )
        )
        return TEMPLATES.TemplateResponse(
            request,
            "claim_detail.html",
            {"request": request, "claim": claim, "question": question, "excerpts": excerpts, "sources": sources, "is_admin": include_private},
        )

    @app.get("/findings/{finding_id}", response_class=HTMLResponse)
    def finding_detail(finding_id: str, request: Request) -> HTMLResponse:
        return claim_detail(finding_id, request)

    @app.get("/reports/{report_id}", response_class=HTMLResponse)
    def report_detail(report_id: str, request: Request) -> HTMLResponse:
        include_private = _is_admin(request)
        report = _safe_get(lambda: service.get_report(report_id, include_private=include_private))
        question = _safe_get(
            lambda: service.get_question(
                report.question_id, include_private=include_private
            )
        )
        claims = [
            _safe_get(
                lambda claim_id=claim_id: service.get_claim(
                    claim_id, include_private=include_private
                )
            )
            for claim_id in report.claim_ids
        ]
        sources = {
            source_id: _safe_get(
                lambda source_id=source_id: service.get_source(
                    source_id, include_private=include_private
                )
            )
            for source_id in report.source_ids
        }
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
            return RedirectResponse("/v2/search", status_code=303)
        return TEMPLATES.TemplateResponse(request, "admin_login.html", {"request": request, "error": None})

    @app.post("/admin/login", response_class=HTMLResponse)
    async def admin_login_submit(request: Request, token: str = Form(default="")) -> HTMLResponse:
        if settings.admin_token and token != settings.admin_token:
            return TEMPLATES.TemplateResponse(request, "admin_login.html", {"request": request, "error": "Token mismatch"}, status_code=401)
        request.session["is_admin"] = True
        return RedirectResponse("/v2/search", status_code=303)

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

    @app.get("/v2", include_in_schema=False)
    def v2_home() -> RedirectResponse:
        return RedirectResponse("/v2/search", status_code=303)

    @app.get("/v2/search", response_class=HTMLResponse, include_in_schema=False)
    def v2_search(
        request: Request,
        q: str = "",
        kind: str | None = None,
        review_state: str | None = None,
        conflict_state: str | None = None,
        freshness: str | None = None,
        cursor: str | None = None,
    ) -> HTMLResponse:
        auth, access = _web_read_access(request)
        try:
            page = app.state.web_v2.search_page(
                q,
                access=access,
                kind=kind or None,
                review_state=review_state or None,
                conflict_state=conflict_state or None,
                freshness=freshness or None,
                cursor=cursor or None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_safe_error_message(exc)) from exc
        return TEMPLATES.TemplateResponse(
            request,
            "v2_search.html",
            {
                "request": request,
                "page": page,
                "is_admin": bool(auth and auth.is_admin),
            },
        )

    @app.get(
        "/v2/claims/{claim_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def v2_claim_detail(claim_id: str, request: Request) -> HTMLResponse:
        auth, access = _web_read_access(request)
        try:
            claim = app.state.web_v2.claim_detail(
                claim_id,
                access=access,
                can_review=bool(auth and auth.is_admin),
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=_safe_error_message(exc)) from exc
        return TEMPLATES.TemplateResponse(
            request,
            "v2_claim_detail.html",
            {
                "request": request,
                "claim": claim,
                "is_admin": bool(auth and auth.is_admin),
            },
        )

    @app.get(
        "/v2/evidence/{evidence_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def v2_evidence_detail(evidence_id: str, request: Request) -> HTMLResponse:
        auth, access = _web_read_access(request)
        try:
            evidence = app.state.web_v2.evidence_detail(
                evidence_id,
                access=access,
                can_review=bool(auth and auth.is_admin),
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=_safe_error_message(exc)) from exc
        return TEMPLATES.TemplateResponse(
            request,
            "v2_evidence_detail.html",
            {
                "request": request,
                "evidence": evidence,
                "is_admin": bool(auth and auth.is_admin),
            },
        )

    @app.get(
        "/v2/sources/{source_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def v2_source_detail(source_id: str, request: Request) -> HTMLResponse:
        auth, access = _web_read_access(request)
        try:
            source = app.state.web_v2.source_detail(
                source_id,
                access=access,
                can_review=bool(auth and auth.is_admin),
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=_safe_error_message(exc)) from exc
        return TEMPLATES.TemplateResponse(
            request,
            "v2_source_detail.html",
            {
                "request": request,
                "source": source,
                "is_admin": bool(auth and auth.is_admin),
            },
        )

    @app.get(
        "/v2/source-versions/{version_id}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def v2_source_version_detail(version_id: str, request: Request) -> HTMLResponse:
        auth, access = _web_read_access(request)
        try:
            detail = app.state.web_v2.source_version_detail(
                version_id,
                access=access,
                can_review=bool(auth and auth.is_admin),
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=_safe_error_message(exc)) from exc
        return TEMPLATES.TemplateResponse(
            request,
            "v2_source_version_detail.html",
            {
                "request": request,
                "detail": detail,
                "is_admin": bool(auth and auth.is_admin),
            },
        )

    @app.get("/v2/reports/{report_id}", include_in_schema=False)
    def v2_report_compatibility(report_id: str) -> RedirectResponse:
        return RedirectResponse(f"/reports/{report_id}", status_code=307)

    @app.get("/v2/questions/{question_id}", include_in_schema=False)
    def v2_question_compatibility(question_id: str) -> RedirectResponse:
        return RedirectResponse(f"/questions/{question_id}", status_code=307)

    @app.get("/v2/review", response_class=HTMLResponse, include_in_schema=False)
    def v2_review_inbox(request: Request) -> HTMLResponse:
        auth = _admin_guard(request)
        access = _access_for_auth(auth)
        inbox = app.state.web_v2.review_inbox(access=access)
        return TEMPLATES.TemplateResponse(
            request,
            "v2_review_inbox.html",
            {"request": request, "inbox": inbox, "is_admin": True},
        )

    @app.post("/v2/review", response_class=HTMLResponse, include_in_schema=False)
    def v2_apply_review(
        request: Request,
        entity_kind: str = Form(...),
        entity_id: str = Form(...),
        action: str = Form(...),
        expected_revision_id: str | None = Form(default=None),
        expected_state: str | None = Form(default=None),
        note: str | None = Form(default=None),
        confirm: str | None = Form(default=None),
        new_title: str | None = Form(default=None),
        new_statement: str | None = Form(default=None),
        new_status: str | None = Form(default=None),
        new_confidence: float | None = Form(default=None),
    ) -> HTMLResponse:
        auth = _admin_guard(request)
        if action in {"contest", "reject", "supersede", "dismiss_refresh"} and confirm != "yes":
            return _v2_error_response(
                request,
                title="Confirmation required",
                message="Confirm this state-changing action before submitting it.",
                status_code=400,
                return_href=_review_return_href(entity_kind, entity_id),
            )
        new_revision = None
        if action == "supersede":
            if (
                new_title is None
                or new_statement is None
                or new_status is None
                or new_confidence is None
            ):
                return _v2_error_response(
                    request,
                    title="Replacement revision required",
                    message="A superseding action requires complete replacement claim fields.",
                    status_code=400,
                    return_href=_review_return_href(entity_kind, entity_id),
                )
            new_revision = {
                "title": new_title,
                "statement": new_statement,
                "status": new_status,
                "confidence": new_confidence,
            }
        try:
            command = ResearchReviewRequest.model_validate(
                {
                    "protocol": "research-review/v2",
                    "idempotency_key": f"web-review-{uuid4().hex}",
                    "entity": {"kind": entity_kind, "id": entity_id},
                    "action": action,
                    "expected_revision_id": expected_revision_id,
                    "expected_state": expected_state,
                    "note": note or None,
                    "new_revision": new_revision,
                }
            )
            result = ResearchReviewService(service.database).review(
                command,
                namespace_kind=auth.namespace_kind,
                namespace_id=auth.namespace_id,
                actor_type="human",
                actor_id=auth.actor_user_id or auth.api_key_id,
            )
        except (ExpectedRevisionMismatch, ExpectedStateMismatch):
            return _v2_error_response(
                request,
                title="Another reviewer changed this record",
                message=(
                    "Another reviewer changed the current revision or review state "
                    "before this action was applied."
                ),
                status_code=409,
                return_href=_review_return_href(entity_kind, entity_id),
                retry=True,
            )
        except ReviewRecordNotFound:
            return _v2_error_response(
                request,
                title="Review target not found",
                message="The accessible review target could not be found.",
                status_code=404,
                return_href="/v2/review",
            )
        except (ReviewError, InvalidRefreshTransition, ValueError):
            return _v2_error_response(
                request,
                title="Review action is not valid",
                message="The requested review transition is not valid for the current state.",
                status_code=400,
                return_href=_review_return_href(entity_kind, entity_id),
            )
        if result.current_state is not None:
            location = f"/v2/claims/{result.current_state.claim_id}"
        elif entity_kind == "refresh_item":
            location = "/v2/refresh"
        else:
            location = app.state.web_v2.record_href(entity_kind, entity_id)
        return RedirectResponse(location, status_code=303)

    @app.get("/v2/refresh", response_class=HTMLResponse, include_in_schema=False)
    def v2_refresh_queue(request: Request) -> HTMLResponse:
        auth = _admin_guard(request)
        queue = app.state.web_v2.refresh_queue(access=_access_for_auth(auth))
        return TEMPLATES.TemplateResponse(
            request,
            "v2_refresh_queue.html",
            {"request": request, "queue": queue, "is_admin": True},
        )

    @app.get(
        "/v2/deposits/{receipt_key}",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def v2_deposit_receipt(receipt_key: str, request: Request) -> HTMLResponse:
        auth = _admin_guard(request)
        if len(receipt_key) > 200:
            raise HTTPException(status_code=404, detail="deposit receipt not found")
        try:
            receipt = app.state.web_v2.deposit_receipt(
                receipt_key,
                namespace_kind=auth.namespace_kind,
                namespace_id=auth.namespace_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=_safe_error_message(exc)) from exc
        return TEMPLATES.TemplateResponse(
            request,
            "v2_deposit_receipt.html",
            {"request": request, "receipt": receipt, "is_admin": True},
        )

    @app.get("/v2/status", response_class=HTMLResponse, include_in_schema=False)
    def v2_status(request: Request) -> HTMLResponse:
        _admin_guard(request)
        try:
            service.check_ready()
            status = app.state.web_v2.status()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="registry health is unavailable") from exc
        return TEMPLATES.TemplateResponse(
            request,
            "v2_status.html",
            {"request": request, "status": status, "is_admin": True},
        )

    @app.post("/admin/{kind}/{record_id}/publish")
    async def admin_publish(
        kind: str,
        record_id: str,
        request: Request,
    ) -> RedirectResponse:
        _require_admin(request)
        form = await request.form()
        if form.get("confirm") != "yes":
            raise HTTPException(status_code=400, detail="publish confirmation required")
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
        limit: int = 20,
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
            limit=limit,
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
        return _safe_mutation(lambda: service.create_question(payload, auth=auth))

    @app.post("/api/questions/{question_id}/status")
    def api_set_question_status(question_id: str, payload: QuestionStatusUpdate, auth: AuthContext = Depends(_ingest_guard)):
        return _safe_mutation(lambda: _status_ok(service.set_question_status(question_id, payload.status)))

    @app.post("/api/follow-ups/{question_id}/status")
    def api_set_follow_up_status(question_id: str, payload: FollowUpStatusUpdate, auth: AuthContext = Depends(_ingest_guard)):
        return _safe_mutation(lambda: _status_ok(service.set_follow_up_status(question_id, payload.follow_up_status)))

    @app.get("/api/sessions/{session_id}")
    def api_get_session(session_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_session(session_id, include_private=include_private and auth is not None, auth=auth))

    @app.post("/api/sessions")
    def api_create_session(payload: ResearchSessionCreate, auth: AuthContext = Depends(_ingest_guard)):
        return _safe_mutation(lambda: service.create_session(payload, auth=auth))

    @app.get("/api/sources/{source_id}")
    def api_get_source(source_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_source(source_id, include_private=include_private and auth is not None, auth=auth))

    @app.post("/api/sources")
    def api_create_source(payload: SourceCreate, auth: AuthContext = Depends(_ingest_guard)):
        return _safe_mutation(lambda: service.create_source(payload, auth=auth))

    @app.post("/api/import/url")
    def api_import_url(payload: ImportUrlRequest, auth: AuthContext = Depends(_ingest_guard)):
        return _safe_mutation(lambda: service.import_url(payload, auth=auth))

    @app.post("/api/import/doi")
    def api_import_doi(payload: ImportDoiRequest, auth: AuthContext = Depends(_ingest_guard)):
        return _safe_mutation(lambda: service.import_doi(payload, auth=auth))

    @app.post("/api/import/bibtex")
    def api_import_bibtex(payload: ImportBibtexRequest, auth: AuthContext = Depends(_ingest_guard)):
        return _safe_mutation(lambda: service.import_bibtex(payload, auth=auth))

    @app.get("/api/excerpts/{excerpt_id}")
    def api_get_excerpt(excerpt_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_excerpt(excerpt_id, include_private=include_private and auth is not None, auth=auth))

    @app.get("/api/annotations/{annotation_id}")
    def api_get_annotation(annotation_id: str, request: Request, include_private: bool = False):
        return api_get_excerpt(annotation_id, request, include_private=include_private)

    @app.post("/api/excerpts")
    def api_create_excerpt(payload: ExcerptCreate, auth: AuthContext = Depends(_ingest_guard)):
        return _safe_mutation(lambda: service.create_excerpt(payload, auth=auth))

    @app.get("/api/claims/{claim_id}")
    def api_get_claim(claim_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_claim(claim_id, include_private=include_private and auth is not None, auth=auth))

    @app.get("/api/findings/{finding_id}")
    def api_get_finding(finding_id: str, request: Request, include_private: bool = False):
        return api_get_claim(finding_id, request, include_private=include_private)

    @app.post("/api/claims")
    def api_create_claim(payload: ClaimCreate, auth: AuthContext = Depends(_ingest_guard)):
        return _safe_mutation(lambda: service.create_claim(payload, auth=auth))

    @app.get("/api/reports/{report_id}")
    def api_get_report(report_id: str, request: Request, include_private: bool = False):
        auth = _optional_auth(request)
        return _safe_get(lambda: service.get_report(report_id, include_private=include_private and auth is not None, auth=auth))

    @app.post("/api/reports")
    def api_create_report(payload: ReportCreate, auth: AuthContext = Depends(_ingest_guard)):
        return _safe_mutation(lambda: service.create_report(payload, auth=auth))

    @app.post("/api/reports/{report_id}/refresh")
    def api_refresh_report(report_id: str, auth: AuthContext = Depends(_ingest_guard)):
        return _safe_mutation(lambda: service.refresh_report(report_id, auth=auth))

    @app.post("/api/briefs/resolve")
    def api_resolve_brief(payload: BriefResolveRequest, request: Request):
        auth = _optional_auth(request)
        return _safe_mutation(lambda: service.resolve_brief(payload, auth=auth))

    @app.post("/api/publish")
    def api_publish(payload: PublishRequest, auth: AuthContext = Depends(_publish_guard)):
        return _safe_mutation(lambda: _status_ok(service.publish(payload, auth=auth)))

    @app.post("/api/review")
    def api_review(payload: ReviewRequest, auth: AuthContext = Depends(_admin_guard)):
        return _safe_mutation(lambda: _status_ok(service.review(payload, auth=auth)))

    @app.post("/api/index-state")
    def api_index_state(payload: IndexStateRequest, auth: AuthContext = Depends(_admin_guard)):
        return _safe_mutation(lambda: _status_ok(service.set_index_state(payload, auth=auth)))

    @app.post("/api/admin/organizations")
    def api_admin_ensure_org(payload: OrganizationBootstrapRequest, auth: AuthContext = Depends(_admin_guard)):
        return _safe_mutation(lambda: service.ensure_organization(payload.org_id, payload.display_name).model_dump(mode="json"))

    @app.post("/api/admin/api-keys")
    def api_admin_issue_key(payload: ApiKeyCreate, auth: AuthContext = Depends(_admin_guard)):
        return _safe_mutation(lambda: service.issue_api_key(payload).model_dump(mode="json"))

    return app


def _safe_get(operation):
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _safe_mutation(operation):
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _status_ok(_: object = None) -> dict[str, str]:
    return {"status": "ok"}


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


def _web_read_access(request: Request) -> tuple[AuthContext | None, ReadAccess]:
    auth = _optional_auth(request)
    include_private = bool(auth and auth.has_scope("read_private"))
    return auth, _access_for_auth(auth, include_private=include_private)


def _access_for_auth(
    auth: AuthContext | None,
    *,
    include_private: bool = True,
) -> ReadAccess:
    return ReadAccess(
        include_private=include_private and auth is not None,
        namespace_kind=auth.namespace_kind if auth else None,
        namespace_id=auth.namespace_id if auth else None,
        is_admin=bool(auth and auth.is_admin),
        local_trusted=False,
    )


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


def _safe_error_message(error: Exception) -> str:
    message = str(error)
    if ": " in message:
        message = message.split(": ", 1)[1]
    return message[:500] or "The requested record could not be found."


def _review_return_href(entity_kind: str, entity_id: str) -> str:
    if entity_kind == "refresh_item":
        return "/v2/refresh"
    if entity_kind == "evidence":
        return f"/v2/evidence/{entity_id}"
    if entity_kind == "source_version":
        return f"/v2/source-versions/{entity_id}"
    if entity_kind == "report":
        return f"/reports/{entity_id}"
    return "/v2/review"


def _v2_error_response(
    request: Request,
    *,
    title: str,
    message: str,
    status_code: int,
    return_href: str,
    retry: bool = False,
) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "v2_error.html",
        {
            "request": request,
            "title": title,
            "message": message,
            "return_href": return_href,
            "retry": retry,
            "is_admin": True,
        },
        status_code=status_code,
    )


app = create_app()
