from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from .backend_client import RegistryBackend
from .config import Settings
from .models import AuthContext, ClaimCreate, ExcerptCreate, PublishRequest, QuestionCreate, ReportCreate, ResearchSessionCreate, SourceCreate
from .service import RegistryService


def _admin_auth() -> AuthContext:
    return AuthContext(
        is_admin=True,
        scopes=["admin", "ingest", "publish", "read_private"],
        namespace_kind="user",
        namespace_id="local",
    )


class McpToolRuntime:
    def __init__(
        self,
        backend: RegistryBackend,
        *,
        settings: Settings | None = None,
        service: RegistryService | None = None,
        default_api_key: str | None = None,
        allow_admin_fallback: bool = True,
    ) -> None:
        self.backend = backend
        self.settings = settings
        self.service = service
        self.default_api_key = default_api_key
        self.allow_admin_fallback = allow_admin_fallback

    def search(self, query: str, *, kind: str | None, include_private: bool, limit: int, ctx: Context) -> dict[str, Any]:
        auth = self._resolve_auth(ctx, require_scope="read_private" if include_private else None)
        if self.service is None:
            return self.backend.search(query, kind=kind, include_private=include_private, limit=limit).model_dump(mode="json")
        return self.service.search(
            query,
            kind=kind,
            include_private=include_private and auth is not None,
            limit=limit,
            auth=auth,
            public_index_only=auth is None and not include_private,
        ).model_dump(mode="json")

    def backend_status(self, ctx: Context) -> dict[str, Any]:
        status = self.backend.backend_status()
        auth = self._resolve_auth(ctx, require_scope=None, allow_unauthenticated=True)
        if auth is None:
            return status.model_dump(mode="json")
        return status.model_copy(
            update={
                "namespace_kind": auth.namespace_kind,
                "namespace_id": auth.namespace_id,
                "api_key_present": auth.api_key_id is not None,
                "org": auth.actor_org_id,
            }
        ).model_dump(mode="json")

    def get_question(self, question_id: str, *, include_private: bool, ctx: Context) -> dict[str, Any]:
        auth = self._resolve_auth(ctx, require_scope="read_private" if include_private else None)
        return self._get_record("question", question_id, include_private=include_private, auth=auth)

    def get_source(self, source_id: str, *, include_private: bool, ctx: Context) -> dict[str, Any]:
        auth = self._resolve_auth(ctx, require_scope="read_private" if include_private else None)
        return self._get_record("source", source_id, include_private=include_private, auth=auth)

    def get_excerpt(self, excerpt_id: str, *, include_private: bool, ctx: Context) -> dict[str, Any]:
        auth = self._resolve_auth(ctx, require_scope="read_private" if include_private else None)
        return self._get_record("excerpt", excerpt_id, include_private=include_private, auth=auth)

    def get_claim(self, claim_id: str, *, include_private: bool, ctx: Context) -> dict[str, Any]:
        auth = self._resolve_auth(ctx, require_scope="read_private" if include_private else None)
        return self._get_record("claim", claim_id, include_private=include_private, auth=auth)

    def get_report(self, report_id: str, *, include_private: bool, ctx: Context) -> dict[str, Any]:
        auth = self._resolve_auth(ctx, require_scope="read_private" if include_private else None)
        return self._get_record("report", report_id, include_private=include_private, auth=auth)

    def create_question(self, payload: dict[str, Any], ctx: Context) -> dict[str, Any]:
        auth = self._resolve_auth(ctx, require_scope="ingest")
        model = QuestionCreate.model_validate(payload)
        return self._create_record("question", model, auth=auth)

    def create_session(self, payload: dict[str, Any], ctx: Context) -> dict[str, Any]:
        auth = self._resolve_auth(ctx, require_scope="ingest")
        model = ResearchSessionCreate.model_validate(payload)
        return self._create_record("session", model, auth=auth)

    def create_source(self, payload: dict[str, Any], ctx: Context) -> dict[str, Any]:
        auth = self._resolve_auth(ctx, require_scope="ingest")
        model = SourceCreate.model_validate(payload)
        return self._create_record("source", model, auth=auth)

    def add_excerpt(self, payload: dict[str, Any], ctx: Context) -> dict[str, Any]:
        auth = self._resolve_auth(ctx, require_scope="ingest")
        model = ExcerptCreate.model_validate(payload)
        return self._create_record("excerpt", model, auth=auth)

    def create_claim(self, payload: dict[str, Any], ctx: Context) -> dict[str, Any]:
        auth = self._resolve_auth(ctx, require_scope="ingest")
        model = ClaimCreate.model_validate(payload)
        return self._create_record("claim", model, auth=auth)

    def create_report(self, payload: dict[str, Any], ctx: Context) -> dict[str, Any]:
        auth = self._resolve_auth(ctx, require_scope="ingest")
        model = ReportCreate.model_validate(payload)
        return self._create_record("report", model, auth=auth)

    def publish(self, kind: str, record_id: str, *, cascade_linked_sources: bool, ctx: Context) -> dict[str, Any]:
        auth = self._resolve_auth(ctx, require_scope="publish")
        payload = PublishRequest(kind=kind, record_id=record_id, cascade_linked_sources=cascade_linked_sources)
        if self.service is None:
            self.backend.publish(payload)
        else:
            self.service.publish(payload, auth=auth)
        return {"status": "ok", "kind": kind, "record_id": record_id}

    def _resolve_auth(
        self,
        ctx: Context,
        *,
        require_scope: str | None,
        allow_unauthenticated: bool = False,
    ) -> AuthContext | None:
        if self.service is None:
            return None

        auth = self._auth_from_request(ctx)
        if auth is None and self.default_api_key:
            try:
                auth = self.service.authenticate_api_key(self.default_api_key)
            except PermissionError:
                auth = None
        if auth is None and self.allow_admin_fallback:
            auth = _admin_auth()

        if auth is None:
            if allow_unauthenticated and require_scope is None:
                return None
            if require_scope == "read_private":
                raise PermissionError("x-api-key required for private MCP reads")
            if require_scope:
                raise PermissionError(f"x-api-key required for MCP scope {require_scope}")
            return None
        if require_scope and not auth.has_scope(require_scope):  # type: ignore[arg-type]
            raise PermissionError(f"{require_scope} scope required")
        return auth

    def _auth_from_request(self, ctx: Context) -> AuthContext | None:
        request = getattr(ctx.request_context, "request", None)
        headers = getattr(request, "headers", None)
        if headers is None:
            return None

        api_key = headers.get("x-api-key", "").strip()
        if api_key:
            return self.service.authenticate_api_key(api_key)

        admin_token = headers.get("x-admin-token", "").strip()
        if self.settings and self.settings.admin_token and admin_token == self.settings.admin_token:
            return _admin_auth()
        return None

    def _get_record(
        self,
        kind: str,
        record_id: str,
        *,
        include_private: bool,
        auth: AuthContext | None,
    ) -> dict[str, Any]:
        if kind == "question":
            if self.service is None:
                record = self.backend.get_question(record_id, include_private=include_private)
            else:
                record = self.service.get_question(
                    record_id,
                    include_private=include_private and auth is not None,
                    auth=auth,
                    public_index_only=auth is None and not include_private,
                )
        elif kind == "source":
            if self.service is None:
                record = self.backend.get_source(record_id, include_private=include_private)
            else:
                record = self.service.get_source(
                    record_id,
                    include_private=include_private and auth is not None,
                    auth=auth,
                    public_index_only=auth is None and not include_private,
                )
        elif kind == "excerpt":
            if self.service is None:
                record = self.backend.get_excerpt(record_id, include_private=include_private)
            else:
                record = self.service.get_excerpt(
                    record_id,
                    include_private=include_private and auth is not None,
                    auth=auth,
                    public_index_only=auth is None and not include_private,
                )
        elif kind == "claim":
            if self.service is None:
                record = self.backend.get_claim(record_id, include_private=include_private)
            else:
                record = self.service.get_claim(
                    record_id,
                    include_private=include_private and auth is not None,
                    auth=auth,
                    public_index_only=auth is None and not include_private,
                )
        else:
            if self.service is None:
                record = self.backend.get_report(record_id, include_private=include_private)
            else:
                record = self.service.get_report(
                    record_id,
                    include_private=include_private and auth is not None,
                    auth=auth,
                    public_index_only=auth is None and not include_private,
                )
        return record.model_dump(mode="json")

    def _create_record(self, kind: str, model: Any, *, auth: AuthContext | None) -> dict[str, Any]:
        if kind == "question":
            record = self.backend.create_question(model) if self.service is None else self.service.create_question(model, auth=auth)
        elif kind == "session":
            record = self.backend.create_session(model) if self.service is None else self.service.create_session(model, auth=auth)
        elif kind == "source":
            record = self.backend.create_source(model) if self.service is None else self.service.create_source(model, auth=auth)
        elif kind == "excerpt":
            record = self.backend.create_excerpt(model) if self.service is None else self.service.create_excerpt(model, auth=auth)
        elif kind == "claim":
            record = self.backend.create_claim(model) if self.service is None else self.service.create_claim(model, auth=auth)
        else:
            record = self.backend.create_report(model) if self.service is None else self.service.create_report(model, auth=auth)
        return record.model_dump(mode="json")


def create_mcp_server(
    backend: RegistryBackend,
    *,
    settings: Settings | None = None,
    service: RegistryService | None = None,
    default_api_key: str | None = None,
    allow_admin_fallback: bool = True,
    streamable_http_path: str = "/mcp",
) -> FastMCP:
    runtime = McpToolRuntime(
        backend,
        settings=settings,
        service=service,
        default_api_key=default_api_key,
        allow_admin_fallback=allow_admin_fallback,
    )

    mcp = FastMCP(
        "Research Registry",
        instructions="Question-led research memory with excerpt-backed evidence, reusable claims, reports, and publication controls.",
        json_response=True,
        streamable_http_path=streamable_http_path,
    )

    @mcp.tool()
    def search(
        query: str,
        kind: str | None = None,
        include_private: bool = True,
        limit: int = 10,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> dict:
        """Search questions, excerpts, claims, reports, and sources."""
        return runtime.search(query, kind=kind, include_private=include_private, limit=limit, ctx=ctx)

    @mcp.tool()
    def backend_status(ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Return the selected backend URL, namespace, and selection source."""
        return runtime.backend_status(ctx)

    @mcp.tool()
    def create_question(payload: dict, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Create or reuse a research question and its focus label."""
        return runtime.create_question(payload, ctx)

    @mcp.tool()
    def create_session(payload: dict, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Create a research session for a question."""
        return runtime.create_session(payload, ctx)

    @mcp.tool()
    def get_question(question_id: str, include_private: bool = True, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Fetch a single question by id."""
        return runtime.get_question(question_id, include_private=include_private, ctx=ctx)

    @mcp.tool()
    def get_source(source_id: str, include_private: bool = True, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Fetch a single source by id."""
        return runtime.get_source(source_id, include_private=include_private, ctx=ctx)

    @mcp.tool()
    def get_excerpt(excerpt_id: str, include_private: bool = True, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Fetch a single excerpt by id."""
        return runtime.get_excerpt(excerpt_id, include_private=include_private, ctx=ctx)

    @mcp.tool()
    def get_annotation(annotation_id: str, include_private: bool = True, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Compatibility alias for fetching an excerpt by id."""
        return runtime.get_excerpt(annotation_id, include_private=include_private, ctx=ctx)

    @mcp.tool()
    def get_claim(claim_id: str, include_private: bool = True, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Fetch a single claim by id."""
        return runtime.get_claim(claim_id, include_private=include_private, ctx=ctx)

    @mcp.tool()
    def get_finding(finding_id: str, include_private: bool = True, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Compatibility alias for fetching a claim by id."""
        return runtime.get_claim(finding_id, include_private=include_private, ctx=ctx)

    @mcp.tool()
    def get_report(report_id: str, include_private: bool = True, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Fetch a single report by id."""
        return runtime.get_report(report_id, include_private=include_private, ctx=ctx)

    @mcp.tool()
    def create_source(payload: dict, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Create or reuse a source record."""
        return runtime.create_source(payload, ctx)

    @mcp.tool()
    def add_excerpt(payload: dict, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Create a source-backed evidence excerpt."""
        return runtime.add_excerpt(payload, ctx)

    @mcp.tool()
    def add_annotation(payload: dict, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Compatibility alias for creating an evidence excerpt."""
        return runtime.add_excerpt(payload, ctx)

    @mcp.tool()
    def create_claim(payload: dict, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Create a claim from one or more excerpt ids."""
        return runtime.create_claim(payload, ctx)

    @mcp.tool()
    def create_finding(payload: dict, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Compatibility alias for creating a claim from excerpt ids."""
        return runtime.create_claim(payload, ctx)

    @mcp.tool()
    def create_report(payload: dict, ctx: Context = None) -> dict:  # type: ignore[assignment]
        """Create a report with explicit summary markdown from one or more claim ids."""
        return runtime.create_report(payload, ctx)

    @mcp.tool()
    def publish(
        kind: str,
        record_id: str,
        cascade_linked_sources: bool = True,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> dict:
        """Publish a source, question, excerpt, claim, or report."""
        return runtime.publish(kind, record_id, cascade_linked_sources=cascade_linked_sources, ctx=ctx)

    return mcp
