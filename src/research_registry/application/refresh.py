from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from ..contracts.v2 import (
    RefreshEntity,
    RefreshPlanItem,
    ResearchRefreshRequest,
    ResearchRefreshResult,
)
from ..db import DatabaseTarget
from ..persistence.repositories import ReviewRefreshRepository, canonical_json
from ..persistence.unit_of_work import UnitOfWork
from ..retrieval.projection import rebuild_search_documents


_OPERATION = "research_refresh_v2"
_MAX_RESULT_ITEMS = 500


class RefreshError(RuntimeError):
    """Base error for a compact rejected refresh operation."""


class RefreshRecordNotFound(RefreshError):
    pass


class RefreshIdempotencyConflict(RefreshError):
    pass


class RefreshModeDenied(RefreshError):
    pass


class InvalidRefreshTransition(RefreshError):
    pass


class ResearchRefreshService:
    """Inspect or enqueue bounded refresh work without network access."""

    def __init__(
        self,
        database: str | Path | DatabaseTarget,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.clock = clock or (
            lambda: datetime.now(timezone.utc).replace(microsecond=0)
        )

    def refresh(
        self,
        request: ResearchRefreshRequest | dict[str, Any],
        *,
        namespace_kind: str = "user",
        namespace_id: str = "local",
    ) -> ResearchRefreshResult:
        command = (
            request
            if isinstance(request, ResearchRefreshRequest)
            else ResearchRefreshRequest.model_validate(request)
        )
        if command.mode not in {"inspect", "enqueue"}:
            raise RefreshModeDenied(
                "CAPTURE_POLICY_DENIED: Only inspect and enqueue are available; "
                "this operation performs no network capture."
            )
        if command.mode == "inspect":
            with UnitOfWork(self.database) as uow:
                assert uow.review_refresh is not None
                targets = resolve_refresh_targets(
                    uow.review_refresh,
                    command.entities,
                    namespace_kind=namespace_kind,
                    namespace_id=namespace_id,
                )
            return ResearchRefreshResult(
                protocol="research-refresh-result/v2",
                status="inspected",
                committed=False,
                idempotent_replay=False,
                items=[
                    RefreshPlanItem(
                        entity=RefreshEntity(kind=kind, id=entity_id),
                        reason="manual",
                        refresh_item_id=None,
                        queue_status="not_enqueued",
                        created=False,
                    )
                    for kind, entity_id in targets
                ],
            )

        request_json = canonical_json(command.model_dump(mode="json"))
        request_hash = sha256(request_json.encode("utf-8")).hexdigest()
        now_text = self.clock().isoformat()
        reservation = canonical_json({"reservation": uuid4().hex})
        with UnitOfWork(self.database, immediate_write=True) as uow:
            assert uow.review_refresh is not None
            assert uow.connection is not None
            repository = uow.review_refresh
            if command.idempotency_key is not None:
                existing = repository.get_idempotency(
                    namespace_id, _OPERATION, command.idempotency_key
                )
                if existing is not None:
                    return self._replay(existing, request_hash)

            targets = resolve_refresh_targets(
                repository,
                command.entities,
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
            )
            if command.idempotency_key is not None:
                repository.reserve_idempotency(
                    namespace_id=namespace_id,
                    operation=_OPERATION,
                    key=command.idempotency_key,
                    request_sha256=request_hash,
                    reservation_json=reservation,
                    created_at=now_text,
                )
                reserved = repository.get_idempotency(
                    namespace_id, _OPERATION, command.idempotency_key
                )
                assert reserved is not None
                if reserved["request_sha256"] != request_hash:
                    raise RefreshIdempotencyConflict(
                        "IDEMPOTENCY_CONFLICT: The idempotency key was used "
                        "for a different refresh request."
                    )
                if reserved["response_json"] != reservation:
                    return self._replay(reserved, request_hash)

            items = enqueue_refresh_targets(
                repository,
                targets,
                priority=command.priority,
                detected_at=now_text,
            )
            result = ResearchRefreshResult(
                protocol="research-refresh-result/v2",
                status="enqueued",
                committed=True,
                idempotent_replay=False,
                items=items,
            )
            if command.idempotency_key is not None:
                repository.complete_idempotency(
                    namespace_id=namespace_id,
                    operation=_OPERATION,
                    key=command.idempotency_key,
                    reservation_json=reservation,
                    response_json=result.model_dump_json(),
                )
            rebuild_search_documents(uow.connection)
            uow.commit()
            return result

    @staticmethod
    def _replay(row: Any, request_hash: str) -> ResearchRefreshResult:
        if row["request_sha256"] != request_hash:
            raise RefreshIdempotencyConflict(
                "IDEMPOTENCY_CONFLICT: The idempotency key was used "
                "for a different refresh request."
            )
        try:
            result = ResearchRefreshResult.model_validate_json(row["response_json"])
        except ValueError as exc:
            raise RefreshError(
                "DATABASE_INTEGRITY_ERROR: The stored refresh response is invalid."
            ) from exc
        return result.model_copy(update={"idempotent_replay": True})


def resolve_refresh_targets(
    repository: ReviewRefreshRepository,
    entities: Iterable[RefreshEntity],
    *,
    namespace_kind: str,
    namespace_id: str,
) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    for entity in entities:
        root = repository.resolve_refresh_root(
            kind=entity.kind,
            entity_id=entity.id,
            namespace_kind=namespace_kind,
            namespace_id=namespace_id,
        )
        if root is None:
            raise RefreshRecordNotFound(
                "RECORD_NOT_FOUND: The refresh entity was not found."
            )
        targets.extend(repository.expand_refresh_targets(*root))
        if len(dict.fromkeys(targets)) > _MAX_RESULT_ITEMS:
            raise RefreshError(
                "VALUE_OUT_OF_RANGE: Refresh expansion exceeds the result limit."
            )
    return list(dict.fromkeys(targets))


def enqueue_refresh_targets(
    repository: ReviewRefreshRepository,
    targets: Iterable[tuple[str, str]],
    *,
    priority: float,
    detected_at: str,
) -> list[RefreshPlanItem]:
    items: list[RefreshPlanItem] = []
    for kind, entity_id in targets:
        row, created = repository.enqueue_refresh(
            refresh_id=f"rfr_{uuid4()}",
            entity_kind=kind,
            entity_id=entity_id,
            reason="manual",
            priority=priority,
            detected_at=detected_at,
            details_json=canonical_json({"requested_by": "review_or_refresh"}),
        )
        items.append(
            RefreshPlanItem(
                entity=RefreshEntity(kind=kind, id=entity_id),
                reason="manual",
                refresh_item_id=row["id"],
                queue_status=row["status"],
                created=created,
            )
        )
    return items
