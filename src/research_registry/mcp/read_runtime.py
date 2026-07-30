from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context

from .. import __version__
from ..application.fetch import ResearchFetchService, ResearchGetResult
from ..application.search import ResearchSearchService
from ..backend_client import RegistryBackend
from ..config import Settings
from ..contracts.v2 import (
    NamespaceSelector,
    RefreshBacklog,
    ResearchGetRequest,
    ResearchSearchRequest,
    ResearchSearchResponse,
    ResearchStatusResponse,
    SearchScope,
)
from ..models import AuthContext
from ..persistence.read_adapter import CurrentRetrievalAdapter, ReadAccess
from ..service import RegistryService


def _local_stdio_auth() -> AuthContext:
    return AuthContext(
        actor_user_id="local-stdio",
        is_admin=False,
        scopes=["admin", "ingest", "publish", "read_private"],
        namespace_kind="user",
        namespace_id="local",
    )


def _admin_auth() -> AuthContext:
    return AuthContext(
        is_admin=True,
        scopes=["admin", "ingest", "publish", "read_private"],
        namespace_kind="user",
        namespace_id="local",
    )


class ReadMcpRuntime:
    """Thin authenticated MCP translation into v2 read application services."""

    def __init__(
        self,
        backend: RegistryBackend,
        *,
        settings: Settings | None = None,
        service: RegistryService | None = None,
        default_api_key: str | None = None,
        allow_admin_fallback: bool = True,
        capture_mode: str | None = None,
        legacy_tools_enabled: bool = False,
    ) -> None:
        self.backend = backend
        self.settings = settings
        self.service = service or (
            backend if isinstance(backend, RegistryService) else None
        )
        self.default_api_key = default_api_key
        self.allow_admin_fallback = allow_admin_fallback
        self.capture_mode = capture_mode or (
            "suggest" if allow_admin_fallback else "explicit"
        )
        self.legacy_tools_enabled = legacy_tools_enabled
        self.retrieval = (
            CurrentRetrievalAdapter(self.service.database)
            if self.service is not None
            else None
        )
        self.search_service = (
            ResearchSearchService(self.retrieval)
            if self.retrieval is not None
            else None
        )
        self.fetch_service = (
            ResearchFetchService(self.retrieval)
            if self.retrieval is not None
            else None
        )

    def research_status(
        self, *, ctx: Context | None
    ) -> ResearchStatusResponse:
        retrieval = self._require_retrieval()
        auth = self._resolve_auth(
            ctx, require_scope=None, allow_unauthenticated=True
        )
        schema_version, backlog, migration_state = retrieval.status_counts()
        namespace_kind = auth.namespace_kind if auth else "user"
        namespace_id = auth.namespace_id if auth else "public"
        return ResearchStatusResponse(
            protocol="research-status-result/v2",
            server_version=__version__,
            schema_version=schema_version,
            namespace=NamespaceSelector(
                kind=namespace_kind,
                id=namespace_id,
            ),
            database_type=retrieval.database_type,  # type: ignore[arg-type]
            capture_mode=self.capture_mode,  # type: ignore[arg-type]
            capabilities=[
                "v2-read",
                "current-retrieval-adapter",
                "full-text-retrieval",
                "explained-ranking",
                "cursor-pagination",
                "bounded-hydration",
                "deep-research-read-only",
                "review-events",
                "refresh-inspect-enqueue",
            ],
            legacy_tools_enabled=self.legacy_tools_enabled,
            embedding_status="disabled",
            refresh_backlog=RefreshBacklog(**backlog),
            migration_state=migration_state,  # type: ignore[arg-type]
        )

    def research_search(
        self,
        *,
        query: str,
        kinds: list[str],
        scope: SearchScope | dict[str, Any] | None,
        review_states: list[str],
        conflict_states: list[str],
        freshness: list[str],
        include_private: bool,
        include_rejected: bool,
        limit: int,
        cursor: str | None,
        explain: bool,
        ctx: Context | None,
    ) -> ResearchSearchResponse:
        search = self._require_search()
        auth = self._resolve_auth(
            ctx,
            require_scope="read_private" if include_private else None,
            allow_unauthenticated=not include_private,
        )
        request = ResearchSearchRequest.model_validate(
            {
                "protocol": "research-search/v2",
                "query": query,
                "kinds": kinds,
                "scope": (
                    scope.model_dump(mode="json")
                    if isinstance(scope, SearchScope)
                    else scope
                ),
                "review_states": review_states,
                "conflict_states": conflict_states,
                "freshness": freshness,
                "include_private": include_private,
                "include_rejected": include_rejected,
                "limit": limit,
                "cursor": cursor,
                "explain": explain,
            }
        )
        return search.search(
            request,
            access=self._access(auth, include_private=include_private),
        )

    def research_get(
        self,
        *,
        record_id: str,
        include: list[str],
        depth: int,
        include_private: bool,
        ctx: Context | None,
    ) -> ResearchGetResult:
        fetch = self._require_fetch()
        auth = self._resolve_auth(
            ctx,
            require_scope="read_private" if include_private else None,
            allow_unauthenticated=not include_private,
        )
        request = ResearchGetRequest.model_validate(
            {
                "protocol": "research-get/v2",
                "id": record_id,
                "include": include,
                "depth": depth,
                "include_private": include_private,
            }
        )
        return fetch.get(
            record_id=request.id,
            include=list(request.include),
            depth=request.depth,
            access=self._access(auth, include_private=include_private),
        )

    def automatic_include_private(self, ctx: Context | None) -> bool:
        if self.allow_admin_fallback or self.default_api_key:
            return True
        headers = self._request_headers(ctx)
        return bool(
            headers
            and (
                headers.get("x-api-key", "").strip()
                or headers.get("x-admin-token", "").strip()
            )
        )

    def _resolve_auth(
        self,
        ctx: Context | None,
        *,
        require_scope: str | None,
        allow_unauthenticated: bool,
    ) -> AuthContext | None:
        service = self.service
        if service is None:
            if require_scope:
                raise PermissionError(
                    "AUTH_REQUIRED: Authentication is required for private reads."
                )
            return None
        auth = self._auth_from_request(ctx)
        if auth is None and self.default_api_key:
            try:
                auth = service.authenticate_api_key(self.default_api_key)
            except PermissionError:
                auth = None
        if auth is None and self.allow_admin_fallback:
            auth = _local_stdio_auth()
        if auth is None:
            if allow_unauthenticated and require_scope is None:
                return None
            raise PermissionError(
                "AUTH_REQUIRED: Authentication is required for private reads."
            )
        if require_scope and not auth.has_scope(require_scope):  # type: ignore[arg-type]
            raise PermissionError(
                "INSUFFICIENT_SCOPE: The read_private scope is required."
            )
        return auth

    def _auth_from_request(self, ctx: Context | None) -> AuthContext | None:
        if self.service is None:
            return None
        headers = self._request_headers(ctx)
        if headers is None:
            return None
        api_key = headers.get("x-api-key", "").strip()
        if api_key:
            return self.service.authenticate_api_key(api_key)
        admin_token = headers.get("x-admin-token", "").strip()
        if (
            self.settings
            and self.settings.admin_token
            and admin_token == self.settings.admin_token
        ):
            return _admin_auth()
        return None

    @staticmethod
    def _request_headers(ctx: Context | None) -> Any | None:
        if ctx is None:
            return None
        try:
            request_context = getattr(ctx, "request_context", None)
        except ValueError:
            return None
        request = getattr(request_context, "request", None)
        return getattr(request, "headers", None)

    def _access(
        self, auth: AuthContext | None, *, include_private: bool
    ) -> ReadAccess:
        return ReadAccess(
            include_private=include_private,
            namespace_kind=auth.namespace_kind if auth else None,
            namespace_id=auth.namespace_id if auth else None,
            is_admin=bool(auth and auth.is_admin),
            local_trusted=bool(
                self.allow_admin_fallback
                and auth
                and auth.actor_user_id == "local-stdio"
            ),
        )

    def _require_retrieval(self) -> CurrentRetrievalAdapter:
        if self.retrieval is None:
            raise RuntimeError(
                "RETRIEVAL_INDEX_UNAVAILABLE: V2 reads require a registry service."
            )
        return self.retrieval

    def _require_search(self) -> ResearchSearchService:
        self._require_retrieval()
        assert self.search_service is not None
        return self.search_service

    def _require_fetch(self) -> ResearchFetchService:
        self._require_retrieval()
        assert self.fetch_service is not None
        return self.fetch_service
