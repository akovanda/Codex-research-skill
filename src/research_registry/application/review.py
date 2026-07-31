from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..contracts.common import ClaimRevisionStatus
from ..contracts.v2 import (
    ClaimCurrentState,
    RefreshEntity,
    ResearchReviewRequest,
    ResearchReviewResult,
)
from ..db import DatabaseTarget
from ..domain.claims import V1_CLAIM_STATUS, plan_claim_review
from ..domain.review import review_state_after, validate_review_action
from ..persistence.repositories import ReviewRefreshRepository, canonical_json
from ..persistence.unit_of_work import UnitOfWork
from ..retrieval.projection import rebuild_search_documents
from .refresh import (
    InvalidRefreshTransition,
    enqueue_refresh_targets,
    resolve_refresh_targets,
)


_OPERATION = "research_review_v2"


class ReviewError(RuntimeError):
    """Base error for a compact rejected review operation."""


class ReviewRecordNotFound(ReviewError):
    pass


class IdempotencyConflict(ReviewError):
    pass


class ExpectedRevisionMismatch(ReviewError):
    pass


class ExpectedStateMismatch(ReviewError):
    pass


class InvalidClaimTransition(ReviewError):
    pass


class ClaimRevisionService:
    """Create one immutable revision and move the compatibility pointer."""

    def apply(
        self,
        repository: ReviewRefreshRepository,
        *,
        row: Any,
        action: str,
        request: ResearchReviewRequest,
        now_text: str,
    ) -> tuple[str, str, str]:
        replacement = request.new_revision
        replacement_status = (
            replacement.status if replacement is not None else None
        )
        try:
            change = plan_claim_review(
                row["revision_status"],
                action,  # type: ignore[arg-type]
                replacement_status=replacement_status,
            )
        except ValueError as exc:
            raise InvalidClaimTransition(
                f"INVALID_CLAIM_TRANSITION: {exc}"
            ) from None
        if not change.creates_revision:
            conflict = self._conflict_state(repository, row["claim_id"], row)
            repository.update_claim_review_state(
                claim_id=row["claim_id"],
                expected_revision_id=row["current_revision_id"],
                review_state=change.review_state,
                conflict_state=conflict,
                updated_at=now_text,
            )
            return row["current_revision_id"], change.review_state, conflict

        revision_id = f"clmr_{uuid4()}"
        title = replacement.title if replacement is not None else row["title"]
        statement = (
            replacement.statement if replacement is not None else row["statement"]
        )
        confidence = (
            replacement.confidence
            if replacement is not None
            else float(row["confidence"])
        )
        repository.insert_claim_revision(
            {
                "id": revision_id,
                "claim_id": row["claim_id"],
                "revision_number": repository.next_claim_revision_number(
                    row["claim_id"]
                ),
                "title": title,
                "statement": statement,
                "status": change.status,
                "confidence": confidence,
                "valid_from": row["valid_from"],
                "valid_until": row["valid_until"],
                "supersedes_revision_id": row["current_revision_id"],
                "created_by_model": row["created_by_model"],
                "created_at": now_text,
                "metadata_json": canonical_json(
                    {
                        **self._metadata(row["metadata_json"]),
                        "review_action": action,
                        "reviewed_revision_id": row["current_revision_id"],
                        "evidence_mode": self._evidence_mode(action, replacement),
                    }
                ),
            }
        )
        if self._evidence_mode(action, replacement) == "inherit":
            repository.copy_claim_evidence(
                from_revision_id=row["current_revision_id"],
                to_revision_id=revision_id,
            )
        conflict = (
            "conflicted"
            if change.status == "contested"
            or repository.claim_revision_has_refuting_evidence(revision_id)
            else "none"
        )
        repository.update_claim_current(
            claim_id=row["claim_id"],
            expected_revision_id=row["current_revision_id"],
            revision_id=revision_id,
            title=title,
            statement=statement,
            legacy_status=V1_CLAIM_STATUS[change.status],
            confidence=confidence,
            review_state=change.review_state,
            conflict_state=conflict,
            updated_at=now_text,
        )
        return revision_id, change.review_state, conflict

    @staticmethod
    def _evidence_mode(action: str, replacement: Any | None) -> str:
        if action in {"contest", "reject"}:
            return "inherit"
        if replacement is not None:
            return replacement.evidence_mode
        return "none"

    @staticmethod
    def _metadata(value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _conflict_state(
        repository: ReviewRefreshRepository,
        claim_id: str,
        row: Any,
    ) -> str:
        if row["revision_status"] == "contested":
            return "conflicted"
        if repository.current_claim_has_refuting_evidence(claim_id):
            return "conflicted"
        return "none"


class ResearchReviewService:
    """Apply one authorized, optimistic, append-only review decision."""

    def __init__(
        self,
        database: str | Path | DatabaseTarget,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.revisions = ClaimRevisionService()

    def review(
        self,
        request: ResearchReviewRequest | dict[str, Any],
        *,
        namespace_kind: str = "user",
        namespace_id: str = "local",
        actor_type: str = "agent",
        actor_id: str | None = None,
    ) -> ResearchReviewResult:
        command = (
            request
            if isinstance(request, ResearchReviewRequest)
            else ResearchReviewRequest.model_validate(request)
        )
        if command.entity.kind != "claim_revision" and command.new_revision is not None:
            raise InvalidClaimTransition(
                "INVALID_CLAIM_TRANSITION: new_revision is only valid "
                "for claim revisions."
            )
        if command.new_revision is not None and command.action != "supersede":
            raise InvalidClaimTransition(
                "INVALID_CLAIM_TRANSITION: new_revision is only valid "
                "for supersede."
            )
        if command.action in {"request_refresh", "dismiss_refresh"} and (
            command.new_revision is not None
        ):
            raise InvalidClaimTransition(
                "INVALID_CLAIM_TRANSITION: Refresh actions cannot create "
                "claim revisions."
            )
        try:
            validate_review_action(command.entity.kind, command.action)
        except ValueError as exc:
            raise InvalidClaimTransition(
                f"INVALID_CLAIM_TRANSITION: {exc}"
            ) from None
        request_json = canonical_json(command.model_dump(mode="json"))
        request_hash = sha256(request_json.encode("utf-8")).hexdigest()
        now_text = self.clock().isoformat()
        reservation = canonical_json({"reservation": uuid4().hex})

        with UnitOfWork(self.database, immediate_write=True) as uow:
            assert uow.review_refresh is not None
            assert uow.connection is not None
            repository = uow.review_refresh
            existing = repository.get_idempotency(
                namespace_kind,
                namespace_id,
                _OPERATION,
                command.idempotency_key,
            )
            if existing is not None:
                return self._replay(existing, request_hash)

            repository.reserve_idempotency(
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
                operation=_OPERATION,
                key=command.idempotency_key,
                request_sha256=request_hash,
                reservation_json=reservation,
                created_at=now_text,
            )
            reserved = repository.get_idempotency(
                namespace_kind,
                namespace_id,
                _OPERATION,
                command.idempotency_key,
            )
            assert reserved is not None
            if reserved["request_sha256"] != request_hash:
                raise IdempotencyConflict(
                    "IDEMPOTENCY_CONFLICT: The idempotency key was used "
                    "for a different review request."
                )
            if reserved["response_json"] != reservation:
                return self._replay(reserved, request_hash)

            if command.entity.kind == "claim_revision":
                result = self._review_claim(
                    repository,
                    command,
                    namespace_kind=namespace_kind,
                    namespace_id=namespace_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    now_text=now_text,
                )
            elif command.entity.kind == "refresh_item":
                result = self._dismiss_refresh(
                    repository,
                    command,
                    namespace_kind=namespace_kind,
                    namespace_id=namespace_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    now_text=now_text,
                )
            else:
                result = self._review_entity(
                    repository,
                    command,
                    namespace_kind=namespace_kind,
                    namespace_id=namespace_id,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    now_text=now_text,
                )

            repository.complete_idempotency(
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
                operation=_OPERATION,
                key=command.idempotency_key,
                reservation_json=reservation,
                response_json=result.model_dump_json(),
            )
            rebuild_search_documents(uow.connection)
            uow.commit()
            return result

    def current_claim_state(
        self,
        claim_id: str,
        *,
        namespace_kind: str = "user",
        namespace_id: str = "local",
    ) -> ClaimCurrentState:
        with UnitOfWork(self.database) as uow:
            assert uow.review_refresh is not None
            row = uow.review_refresh.get_claim(
                claim_id,
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
            )
            if row is None:
                raise ReviewRecordNotFound(
                    "RECORD_NOT_FOUND: The claim was not found."
                )
            return self._claim_state(uow.review_refresh, row)

    def _review_claim(
        self,
        repository: ReviewRefreshRepository,
        command: ResearchReviewRequest,
        *,
        namespace_kind: str,
        namespace_id: str,
        actor_type: str,
        actor_id: str | None,
        now_text: str,
    ) -> ResearchReviewResult:
        if command.expected_revision_id is None:
            raise ExpectedRevisionMismatch(
                "EXPECTED_REVISION_MISMATCH: expected_revision_id is required."
            )
        if command.expected_state is None:
            raise ExpectedStateMismatch(
                "EXPECTED_STATE_MISMATCH: expected_state is required."
            )
        row = repository.get_claim_for_revision(
            command.entity.id,
            namespace_kind=namespace_kind,
            namespace_id=namespace_id,
        )
        if row is None:
            raise ReviewRecordNotFound(
                "RECORD_NOT_FOUND: The claim revision was not found."
            )
        if (
            row["current_revision_id"] != command.expected_revision_id
            or command.entity.id != command.expected_revision_id
        ):
            raise ExpectedRevisionMismatch(
                "EXPECTED_REVISION_MISMATCH: The current claim revision changed."
            )
        if row["review_state"] != command.expected_state:
            raise ExpectedStateMismatch(
                "EXPECTED_STATE_MISMATCH: The current review state changed."
            )

        refresh_ids: list[str] = []
        revision_created = False
        current_revision_id = row["current_revision_id"]
        to_state = row["review_state"]
        event_action = command.action
        if command.action == "request_refresh":
            targets = resolve_refresh_targets(
                repository,
                [RefreshEntity(kind="claim", id=row["claim_id"])],
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
            )
            refresh_ids = [
                item.refresh_item_id
                for item in enqueue_refresh_targets(
                    repository,
                    targets,
                    priority=0.5,
                    detected_at=now_text,
                )
                if item.refresh_item_id is not None
            ]
            event_action = "refresh_requested"
        else:
            current_revision_id, to_state, _ = self.revisions.apply(
                repository,
                row=row,
                action=command.action,
                request=command,
                now_text=now_text,
            )
            revision_created = current_revision_id != row["current_revision_id"]

        event_id = f"rev_{uuid4()}"
        repository.insert_review_event(
            {
                "id": event_id,
                "entity_kind": "claim_revision",
                "entity_id": current_revision_id,
                "action": event_action,
                "from_state": row["review_state"],
                "to_state": to_state,
                "note": command.note,
                "actor_type": actor_type,
                "actor_id": actor_id,
                "created_at": now_text,
                "metadata_json": canonical_json(
                    {
                        "previous_revision_id": row["current_revision_id"],
                        "refresh_item_ids": refresh_ids,
                        "evidence_mode": (
                            command.new_revision.evidence_mode
                            if command.new_revision is not None
                            else (
                                "inherit"
                                if command.action in {"contest", "reject"}
                                else "none"
                            )
                        ),
                    }
                ),
            }
        )
        current = repository.get_claim(
            row["claim_id"],
            namespace_kind=namespace_kind,
            namespace_id=namespace_id,
        )
        assert current is not None
        return ResearchReviewResult(
            protocol="research-review-result/v2",
            status="applied",
            idempotent_replay=False,
            event_id=event_id,
            entity=command.entity,
            current_revision_id=current_revision_id,
            revision_created=revision_created,
            current_state=self._claim_state(repository, current),
            refresh_item_ids=refresh_ids,
        )

    def _review_entity(
        self,
        repository: ReviewRefreshRepository,
        command: ResearchReviewRequest,
        *,
        namespace_kind: str,
        namespace_id: str,
        actor_type: str,
        actor_id: str | None,
        now_text: str,
    ) -> ResearchReviewResult:
        if command.expected_state is None:
            raise ExpectedStateMismatch(
                "EXPECTED_STATE_MISMATCH: expected_state is required."
            )
        entity = repository.get_reviewable_entity(
            kind=command.entity.kind,
            entity_id=command.entity.id,
            namespace_kind=namespace_kind,
            namespace_id=namespace_id,
        )
        if entity is None:
            raise ReviewRecordNotFound(
                "RECORD_NOT_FOUND: The review entity was not found."
            )
        if entity["review_state"] != command.expected_state:
            raise ExpectedStateMismatch(
                "EXPECTED_STATE_MISMATCH: The current review state changed."
            )
        refresh_ids: list[str] = []
        to_state = entity["review_state"]
        event_action = command.action
        conflict_state = "none"
        if command.action == "request_refresh":
            targets = resolve_refresh_targets(
                repository,
                [
                    RefreshEntity(
                        kind=entity["queue_kind"],
                        id=entity["queue_id"],
                    )
                ],
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
            )
            refresh_ids = [
                item.refresh_item_id
                for item in enqueue_refresh_targets(
                    repository,
                    targets,
                    priority=0.5,
                    detected_at=now_text,
                )
                if item.refresh_item_id is not None
            ]
            event_action = "refresh_requested"
        else:
            try:
                to_state = review_state_after(
                    entity["review_state"], command.action
                )
            except ValueError as exc:
                raise InvalidClaimTransition(
                    f"INVALID_CLAIM_TRANSITION: {exc}"
                ) from None
            conflict_state = (
                "conflicted" if command.action == "contest" else "none"
            )
            repository.update_legacy_review_mirror(
                table=entity["legacy_table"],
                record_id=entity["legacy_id"],
                review_state=to_state,
                conflict_state=conflict_state,
            )
        event_id = f"rev_{uuid4()}"
        repository.insert_review_event(
            {
                "id": event_id,
                "entity_kind": entity["event_kind"],
                "entity_id": entity["event_id"],
                "action": event_action,
                "from_state": entity["review_state"],
                "to_state": to_state,
                "note": command.note,
                "actor_type": actor_type,
                "actor_id": actor_id,
                "created_at": now_text,
                "metadata_json": canonical_json(
                    {"refresh_item_ids": refresh_ids}
                ),
            }
        )
        return ResearchReviewResult(
            protocol="research-review-result/v2",
            status="applied",
            idempotent_replay=False,
            event_id=event_id,
            entity=command.entity,
            current_revision_id=None,
            revision_created=False,
            current_state=None,
            refresh_item_ids=refresh_ids,
        )

    def _dismiss_refresh(
        self,
        repository: ReviewRefreshRepository,
        command: ResearchReviewRequest,
        *,
        namespace_kind: str,
        namespace_id: str,
        actor_type: str,
        actor_id: str | None,
        now_text: str,
    ) -> ResearchReviewResult:
        if command.expected_state is None:
            raise ExpectedStateMismatch(
                "EXPECTED_STATE_MISMATCH: expected_state is required."
            )
        item = repository.get_refresh_item(
            command.entity.id,
            namespace_kind=namespace_kind,
            namespace_id=namespace_id,
        )
        if item is None:
            raise ReviewRecordNotFound(
                "RECORD_NOT_FOUND: The refresh item was not found."
            )
        if item["status"] != command.expected_state:
            raise ExpectedStateMismatch(
                "EXPECTED_STATE_MISMATCH: The refresh item state changed."
            )
        if item["status"] != "pending":
            raise InvalidRefreshTransition(
                "INVALID_CLAIM_TRANSITION: Only pending refresh items "
                "can be dismissed."
            )
        dismissed = repository.dismiss_refresh(
            refresh_id=command.entity.id,
            expected_state=command.expected_state,
            resolved_at=now_text,
        )
        event_target = repository.review_event_target_for_refresh(
            dismissed["entity_kind"], dismissed["entity_id"]
        )
        if event_target is None:
            raise ReviewRecordNotFound(
                "RECORD_NOT_FOUND: The refresh item owner was not found."
            )
        event_id = f"rev_{uuid4()}"
        repository.insert_review_event(
            {
                "id": event_id,
                "entity_kind": event_target[0],
                "entity_id": event_target[1],
                "action": "refresh_resolved",
                "from_state": item["status"],
                "to_state": "dismissed",
                "note": command.note,
                "actor_type": actor_type,
                "actor_id": actor_id,
                "created_at": now_text,
                "metadata_json": canonical_json(
                    {"refresh_item_id": command.entity.id}
                ),
            }
        )
        return ResearchReviewResult(
            protocol="research-review-result/v2",
            status="applied",
            idempotent_replay=False,
            event_id=event_id,
            entity=command.entity,
            current_revision_id=None,
            revision_created=False,
            current_state=None,
            refresh_item_ids=[command.entity.id],
        )

    @staticmethod
    def _claim_state(
        repository: ReviewRefreshRepository,
        row: Any,
    ) -> ClaimCurrentState:
        status: ClaimRevisionStatus = row["revision_status"]
        conflict = (
            "conflicted"
            if status == "contested"
            or repository.current_claim_has_refuting_evidence(row["claim_id"])
            else "none"
        )
        return ClaimCurrentState(
            claim_id=row["claim_id"],
            current_revision_id=row["current_revision_id"],
            revision_number=int(row["revision_number"]),
            status=status,
            review_state=row["review_state"],
            conflict_state=conflict,
            freshness=repository.claim_freshness(row["claim_id"]),
        )

    @staticmethod
    def _replay(row: Any, request_hash: str) -> ResearchReviewResult:
        if row["request_sha256"] != request_hash:
            raise IdempotencyConflict(
                "IDEMPOTENCY_CONFLICT: The idempotency key was used "
                "for a different review request."
            )
        try:
            result = ResearchReviewResult.model_validate_json(row["response_json"])
        except ValueError as exc:
            raise ReviewError(
                "DATABASE_INTEGRITY_ERROR: The stored review response is invalid."
            ) from exc
        return result.model_copy(update={"idempotent_replay": True})
