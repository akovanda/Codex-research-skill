from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context

from ..application.refresh import ResearchRefreshService
from ..application.review import ResearchReviewService
from ..backend_client import RegistryBackend
from ..config import Settings
from ..contracts.v2 import (
    RefreshEntity,
    ResearchRefreshRequest,
    ResearchRefreshResult,
    ResearchReviewRequest,
    ResearchReviewResult,
    ReviewEntity,
    ReviewNewRevision,
)
from ..models import AuthContext
from ..service import RegistryService


def _local_auth() -> AuthContext:
    return AuthContext(
        is_admin=True,
        scopes=["admin", "ingest", "publish", "read_private"],
        namespace_kind="user",
        namespace_id="local",
    )


class WriteMcpRuntime:
    """Authenticated MCP translation for review and offline refresh queueing."""

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
        self.service = service or (
            backend if isinstance(backend, RegistryService) else None
        )
        self.default_api_key = default_api_key
        self.allow_admin_fallback = allow_admin_fallback
        self.reviews = (
            ResearchReviewService(self.service.database)
            if self.service is not None
            else None
        )
        self.refreshes = (
            ResearchRefreshService(self.service.database)
            if self.service is not None
            else None
        )

    def research_review(
        self,
        *,
        idempotency_key: str,
        entity: ReviewEntity | dict[str, Any],
        action: str,
        expected_revision_id: str | None,
        expected_state: str | None,
        note: str | None,
        new_revision: ReviewNewRevision | dict[str, Any] | None,
        ctx: Context | None,
    ) -> ResearchReviewResult:
        auth = self._resolve_admin(ctx)
        service = self._require_reviews()
        request = ResearchReviewRequest.model_validate(
            {
                "protocol": "research-review/v2",
                "idempotency_key": idempotency_key,
                "entity": (
                    entity.model_dump(mode="json")
                    if isinstance(entity, ReviewEntity)
                    else entity
                ),
                "action": action,
                "expected_revision_id": expected_revision_id,
                "expected_state": expected_state,
                "note": note,
                "new_revision": (
                    new_revision.model_dump(mode="json")
                    if isinstance(new_revision, ReviewNewRevision)
                    else new_revision
                ),
            }
        )
        return service.review(
            request,
            namespace_kind=auth.namespace_kind,
            namespace_id=auth.namespace_id,
            actor_type="agent",
            actor_id=auth.actor_user_id or auth.api_key_id,
        )

    def research_refresh(
        self,
        *,
        mode: str,
        idempotency_key: str | None,
        entities: list[RefreshEntity | dict[str, Any]],
        snapshot_policy: str | None,
        priority: float,
        ctx: Context | None,
    ) -> ResearchRefreshResult:
        auth = self._resolve_admin(ctx)
        service = self._require_refreshes()
        request = ResearchRefreshRequest.model_validate(
            {
                "protocol": "research-refresh/v2",
                "mode": mode,
                "idempotency_key": idempotency_key,
                "entities": [
                    item.model_dump(mode="json")
                    if isinstance(item, RefreshEntity)
                    else item
                    for item in entities
                ],
                "snapshot_policy": snapshot_policy,
                "priority": priority,
            }
        )
        return service.refresh(
            request,
            namespace_kind=auth.namespace_kind,
            namespace_id=auth.namespace_id,
        )

    def _resolve_admin(self, ctx: Context | None) -> AuthContext:
        service = self.service
        if service is None:
            raise RuntimeError(
                "DATABASE_INTEGRITY_ERROR: V2 writes require a local registry service."
            )
        auth = self._auth_from_request(ctx)
        if auth is None and self.default_api_key:
            try:
                auth = service.authenticate_api_key(self.default_api_key)
            except PermissionError:
                auth = None
        if auth is None and self.allow_admin_fallback:
            auth = _local_auth()
        if auth is None:
            raise PermissionError(
                "AUTH_REQUIRED: Authentication is required for review writes."
            )
        if not auth.has_scope("admin"):
            raise PermissionError(
                "INSUFFICIENT_SCOPE: The admin scope is required for review writes."
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
            return _local_auth()
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

    def _require_reviews(self) -> ResearchReviewService:
        if self.reviews is None:
            raise RuntimeError(
                "DATABASE_INTEGRITY_ERROR: V2 review requires a local registry service."
            )
        return self.reviews

    def _require_refreshes(self) -> ResearchRefreshService:
        if self.refreshes is None:
            raise RuntimeError(
                "DATABASE_INTEGRITY_ERROR: V2 refresh requires a local registry service."
            )
        return self.refreshes
