from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from ..contracts.common import SnapshotPolicy
from ..contracts.v2 import (
    RefreshEntity,
    RefreshPlanItem,
    ResearchRefreshRequest,
    ResearchRefreshResult,
)
from ..db import DatabaseTarget
from ..domain.evidence import (
    EvidenceAmbiguous,
    EvidenceUnresolved,
    validate_selector,
)
from ..ingestion.git import (
    CapturedGitSource,
    GitObjectNotFound,
    GitSourceIngestor,
)
from ..ingestion.reanchor import ReanchorResult, reanchor_text
from ..ingestion.web import (
    CapturedSource,
    DoiSourceIngestor,
    WebSourceIngestor,
)
from ..persistence.repositories import ReviewRefreshRepository, canonical_json
from ..persistence.unit_of_work import UnitOfWork
from ..retrieval.projection import rebuild_search_documents


_OPERATION = "research_refresh_v2"
_MAX_RESULT_ITEMS = 500
_POLICY_ORDER = {
    "metadata_only": 0,
    "evidence_only": 1,
    "extracted_text": 2,
    "full_content": 3,
}


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


@dataclass(frozen=True)
class CapturePolicy:
    """Explicit machine authorization for network or repository capture."""

    enabled_modes: frozenset[str] = frozenset()
    default_snapshot_policy: SnapshotPolicy = "evidence_only"
    max_snapshot_policy: SnapshotPolicy = "evidence_only"
    allowed_source_kinds: frozenset[str] = frozenset({"web", "doi", "git_blob"})

    def __post_init__(self) -> None:
        if not self.enabled_modes <= {"capture"}:
            raise ValueError("capture policy contains an unsupported mode")
        if (
            self.default_snapshot_policy not in _POLICY_ORDER
            or self.max_snapshot_policy not in _POLICY_ORDER
            or _POLICY_ORDER[self.default_snapshot_policy]
            > _POLICY_ORDER[self.max_snapshot_policy]
        ):
            raise ValueError("capture snapshot policy is invalid")
        if not self.allowed_source_kinds <= {"web", "doi", "git_blob"}:
            raise ValueError("capture source-kind policy is invalid")

    def authorize(
        self,
        mode: str,
        requested: SnapshotPolicy | None,
        source_kind: str,
    ) -> SnapshotPolicy:
        if mode not in self.enabled_modes or source_kind not in self.allowed_source_kinds:
            raise RefreshModeDenied(
                "CAPTURE_POLICY_DENIED: Source capture is not enabled by machine policy."
            )
        selected = requested or self.default_snapshot_policy
        if _POLICY_ORDER[selected] > _POLICY_ORDER[self.max_snapshot_policy]:
            raise RefreshModeDenied(
                "SNAPSHOT_POLICY_DENIED: Requested retention exceeds machine policy."
            )
        return selected


class SourceCaptureCoordinator:
    """Capture source versions and append deterministic re-anchor outcomes."""

    def __init__(
        self,
        database: str | Path | DatabaseTarget,
        policy: CapturePolicy,
        *,
        web: WebSourceIngestor | None = None,
        doi: DoiSourceIngestor | None = None,
        git: GitSourceIngestor | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.policy = policy
        self.web = web
        self.doi = doi
        self.git = git
        self.clock = clock or (
            lambda: datetime.now(timezone.utc).replace(microsecond=0)
        )

    def refresh(
        self,
        command: ResearchRefreshRequest,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> ResearchRefreshResult:
        source_ids = self._source_ids(
            command.entities,
            namespace_kind=namespace_kind,
            namespace_id=namespace_id,
        )
        items: list[RefreshPlanItem] = []
        for source_id in source_ids:
            items.extend(
                self._capture_source(
                    source_id,
                    command,
                    namespace_kind=namespace_kind,
                    namespace_id=namespace_id,
                )
            )
            if len(items) > _MAX_RESULT_ITEMS:
                raise RefreshError(
                    "VALUE_OUT_OF_RANGE: Capture result exceeds the result limit."
                )
        return ResearchRefreshResult(
            protocol="research-refresh-result/v2",
            status="captured" if command.mode == "capture" else "verified",
            committed=True,
            idempotent_replay=False,
            items=items,
        )

    def _source_ids(
        self,
        entities: Iterable[RefreshEntity],
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> list[str]:
        source_ids: list[str] = []
        with UnitOfWork(self.database) as uow:
            assert uow.review_refresh is not None
            repository = uow.review_refresh
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
                if root[0] == "source":
                    source_ids.append(root[1])
                elif root[0] == "evidence":
                    source_id = repository.get_source_id_for_evidence(
                        root[1],
                        namespace_kind=namespace_kind,
                        namespace_id=namespace_id,
                    )
                    if source_id is None:
                        raise RefreshRecordNotFound(
                            "RECORD_NOT_FOUND: The refresh entity was not found."
                        )
                    source_ids.append(source_id)
                else:
                    raise RefreshModeDenied(
                        "CAPTURE_POLICY_DENIED: Capture requires a source, "
                        "source version, or evidence entity."
                    )
        return list(dict.fromkeys(source_ids))

    def _capture_source(
        self,
        source_id: str,
        command: ResearchRefreshRequest,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> list[RefreshPlanItem]:
        with UnitOfWork(self.database) as uow:
            assert uow.review_refresh is not None
            context = uow.review_refresh.get_source_capture_context(
                source_id,
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
            )
        if context is None:
            raise RefreshRecordNotFound(
                "RECORD_NOT_FOUND: The refresh source was not found."
            )
        source, previous = context
        if source["visibility"] == "public":
            raise RefreshModeDenied(
                "PUBLIC_PARENT_MUTATION_DENIED: Capture and verification are "
                "disabled for public sources until immutable-version "
                "publication is supported."
            )
        locator = source["locator"]
        previous_kind = previous["version_kind"] if previous is not None else None
        source_kind = _capture_kind(locator, previous_kind)
        snapshot_policy = self.policy.authorize(
            command.mode,
            command.snapshot_policy,
            source_kind,
        )
        relocated = False
        captured: CapturedSource | CapturedGitSource
        if source_kind == "git_blob":
            if self.git is None or previous is None:
                raise RefreshModeDenied(
                    "CAPTURE_POLICY_DENIED: Git capture context is unavailable."
                )
            metadata = _json_object(previous["metadata_json"])
            repository_id = metadata.get("repository_id")
            path = previous["path"]
            blob_sha = previous["blob_sha"]
            if not all(isinstance(value, str) and value for value in (repository_id, path, blob_sha)):
                raise RefreshModeDenied(
                    "CAPTURE_POLICY_DENIED: Git provenance is incomplete."
                )
            commit_sha = self.git.current_commit(repository_id)
            try:
                captured = self.git.capture(
                    source_id=source_id,
                    repository_id=repository_id,
                    commit_sha=commit_sha,
                    path=path,
                    snapshot_policy=snapshot_policy,
                )
            except GitObjectNotFound:
                paths = self.git.find_blob_paths(
                    repository_id=repository_id,
                    commit_sha=commit_sha,
                    blob_sha=blob_sha,
                )
                if len(paths) != 1:
                    return self._queue_unresolved_source(
                        source_id,
                        previous,
                        reason="conflict" if len(paths) > 1 else "anchor_missing",
                        outcome="ambiguous_relocation" if len(paths) > 1 else "missing",
                        priority=command.priority,
                    )
                captured = self.git.capture(
                    source_id=source_id,
                    repository_id=repository_id,
                    commit_sha=commit_sha,
                    path=paths[0],
                    snapshot_policy=snapshot_policy,
                )
                relocated = True
            new_text = _decode_git_text(captured.content)
        elif source_kind == "doi":
            if self.doi is None:
                raise RefreshModeDenied(
                    "CAPTURE_POLICY_DENIED: DOI capture is unavailable."
                )
            captured = self.doi.capture(
                source_id=source_id,
                doi=locator,
                snapshot_policy=snapshot_policy,
            )
            new_text = captured.extracted_text
        else:
            if self.web is None:
                raise RefreshModeDenied(
                    "CAPTURE_POLICY_DENIED: Web capture is unavailable."
                )
            captured = self.web.capture(
                source_id=source_id,
                url=locator,
                snapshot_policy=snapshot_policy,
            )
            new_text = captured.extracted_text

        new_version = captured.version.record
        source_item = RefreshPlanItem(
            entity=RefreshEntity(kind="source", id=source_id),
            reason="manual",
            refresh_item_id=None,
            queue_status="not_enqueued",
            created=not captured.version.reused,
            source_version_id=new_version.id,
            previous_source_version_id=(
                previous["id"] if previous is not None else None
            ),
            anchor_state="resolved",
        )
        if previous is None or previous["id"] == new_version.id:
            self._record_verification(new_version.id, previous)
            return [source_item]
        return [
            source_item,
            *self._reanchor_evidence(
                previous,
                new_version.id,
                new_version.path,
                new_version.commit_sha,
                new_version.blob_sha,
                new_text,
                relocated=relocated,
                priority=command.priority,
            ),
        ]

    def _record_verification(self, source_version_id: str, previous: Any | None) -> None:
        if previous is None:
            return
        now = self.clock().isoformat()
        with UnitOfWork(self.database, immediate_write=True) as uow:
            assert uow.review_refresh is not None
            uow.review_refresh.insert_review_event(
                {
                    "id": f"rev_{uuid4()}",
                    "entity_kind": "source_version",
                    "entity_id": source_version_id,
                    "action": "refresh_resolved",
                    "from_state": None,
                    "to_state": "verified",
                    "note": None,
                    "actor_type": "system",
                    "actor_id": None,
                    "created_at": now,
                    "metadata_json": canonical_json(
                        {"outcome": "same_immutable_version"}
                    ),
                }
            )
            uow.commit()

    def _reanchor_evidence(
        self,
        previous: Any,
        new_version_id: str,
        new_path: str | None,
        new_commit_sha: str | None,
        new_blob_sha: str | None,
        new_text: str,
        *,
        relocated: bool,
        priority: float,
    ) -> list[RefreshPlanItem]:
        now = self.clock().isoformat()
        items: list[RefreshPlanItem] = []
        with UnitOfWork(self.database, immediate_write=True) as uow:
            assert uow.review_refresh is not None
            repository = uow.review_refresh
            evidence_rows = repository.list_evidence_for_version(previous["id"])
            for row in evidence_rows:
                try:
                    selector = validate_selector(json.loads(row["selector_json"]))
                    updated_selector, _ = _reanchored_selector(
                        selector,
                        row["quote_text"],
                        new_text,
                        new_path=new_path,
                        new_commit_sha=new_commit_sha,
                        new_blob_sha=new_blob_sha,
                    )
                except (
                    json.JSONDecodeError,
                    EvidenceAmbiguous,
                    EvidenceUnresolved,
                    ValueError,
                ) as exc:
                    reason = (
                        "conflict"
                        if isinstance(exc, EvidenceAmbiguous)
                        else "anchor_missing"
                    )
                    items.extend(
                        _enqueue_affected(
                            repository,
                            row["id"],
                            reason=reason,
                            priority=priority,
                            detected_at=now,
                        )
                    )
                    repository.insert_review_event(
                        _refresh_event(
                            row,
                            now,
                            outcome=(
                                "ambiguous"
                                if isinstance(exc, EvidenceAmbiguous)
                                else "unresolved"
                            ),
                            new_version_id=new_version_id,
                            new_evidence_id=None,
                        )
                    )
                    continue
                new_evidence_id = f"evd_{uuid4()}"
                anchor_state = "relocated" if relocated else "resolved"
                metadata = {
                    **_json_object(row["metadata_json"]),
                    "reanchored_from": row["id"],
                    "source_version_changed_from": previous["id"],
                    "untrusted_content": True,
                }
                repository.insert_reanchored_evidence(
                    {
                        "id": new_evidence_id,
                        "source_version_id": new_version_id,
                        "topic_id": row["topic_id"],
                        "question_id": row["question_id"],
                        "session_id": row["session_id"],
                        "quote_text": row["quote_text"],
                        "quote_sha256": row["quote_sha256"],
                        "selector_type": updated_selector["type"],
                        "selector_json": canonical_json(updated_selector),
                        "note": row["note"],
                        "confidence": row["confidence"],
                        "anchor_state": anchor_state,
                        "review_state": "unreviewed",
                        "trust_tier": row["trust_tier"],
                        "created_by_model": None,
                        "created_at": now,
                        "last_resolved_at": now,
                        "metadata_json": canonical_json(metadata),
                    }
                )
                repository.insert_review_event(
                    _refresh_event(
                        row,
                        now,
                        outcome=anchor_state,
                        new_version_id=new_version_id,
                        new_evidence_id=new_evidence_id,
                    )
                )
                items.append(
                    RefreshPlanItem(
                        entity=RefreshEntity(kind="evidence", id=row["id"]),
                        reason="source_changed",
                        refresh_item_id=None,
                        queue_status="not_enqueued",
                        created=True,
                        source_version_id=new_version_id,
                        previous_source_version_id=previous["id"],
                        evidence_span_id=new_evidence_id,
                        previous_evidence_span_id=row["id"],
                        anchor_state=anchor_state,
                    )
                )
                for kind, entity_id in repository.expand_refresh_targets(
                    "evidence",
                    row["id"],
                ):
                    if kind not in {"claim", "report"}:
                        continue
                    queue_row, created = repository.enqueue_refresh(
                        refresh_id=f"rfr_{uuid4()}",
                        entity_kind=kind,
                        entity_id=entity_id,
                        reason="source_changed",
                        priority=priority,
                        detected_at=now,
                        details_json=canonical_json(
                            {
                                "previous_evidence_id": row["id"],
                                "new_evidence_id": new_evidence_id,
                            }
                        ),
                    )
                    items.append(
                        RefreshPlanItem(
                            entity=RefreshEntity(kind=kind, id=entity_id),
                            reason="source_changed",
                            refresh_item_id=queue_row["id"],
                            queue_status=queue_row["status"],
                            created=created,
                            evidence_span_id=new_evidence_id,
                            previous_evidence_span_id=row["id"],
                            anchor_state=anchor_state,
                        )
                    )
            uow.commit()
        return items

    def _queue_unresolved_source(
        self,
        source_id: str,
        previous: Any,
        *,
        reason: str,
        outcome: str,
        priority: float,
    ) -> list[RefreshPlanItem]:
        now = self.clock().isoformat()
        items: list[RefreshPlanItem] = []
        with UnitOfWork(self.database, immediate_write=True) as uow:
            assert uow.review_refresh is not None
            repository = uow.review_refresh
            for evidence in repository.list_evidence_for_version(previous["id"]):
                items.extend(
                    _enqueue_affected(
                        repository,
                        evidence["id"],
                        reason=reason,
                        priority=priority,
                        detected_at=now,
                    )
                )
                repository.insert_review_event(
                    _refresh_event(
                        evidence,
                        now,
                        outcome=outcome,
                        new_version_id=None,
                        new_evidence_id=None,
                    )
                )
            uow.commit()
        if not items:
            return [
                RefreshPlanItem(
                    entity=RefreshEntity(kind="source", id=source_id),
                    reason=reason,
                    refresh_item_id=None,
                    queue_status="not_enqueued",
                    created=False,
                    previous_source_version_id=previous["id"],
                    anchor_state="stale",
                )
            ]
        return items


class ResearchRefreshService:
    """Run bounded refresh work; external capture requires explicit policy."""

    def __init__(
        self,
        database: str | Path | DatabaseTarget,
        *,
        clock: Callable[[], datetime] | None = None,
        capture_coordinator: SourceCaptureCoordinator | None = None,
    ) -> None:
        self.database = database
        self.clock = clock or (
            lambda: datetime.now(timezone.utc).replace(microsecond=0)
        )
        self.capture_coordinator = capture_coordinator

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
        if command.mode in {"capture", "verify"}:
            if self.capture_coordinator is None:
                raise RefreshModeDenied(
                    "CAPTURE_POLICY_DENIED: Capture requires explicit machine policy."
                )
            return self._capture_with_idempotency(
                command,
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
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
                    namespace_kind,
                    namespace_id,
                    _OPERATION,
                    command.idempotency_key,
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

    def _capture_with_idempotency(
        self,
        command: ResearchRefreshRequest,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> ResearchRefreshResult:
        assert self.capture_coordinator is not None
        if command.idempotency_key is None:
            raise RefreshModeDenied(
                "INVALID_REQUEST: Capture and verify require an idempotency key."
            )
        request_json = canonical_json(command.model_dump(mode="json"))
        request_hash = sha256(request_json.encode("utf-8")).hexdigest()
        reservation = canonical_json({"reservation": uuid4().hex})
        now = self.clock().isoformat()
        with UnitOfWork(self.database, immediate_write=True) as uow:
            assert uow.review_refresh is not None
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
                created_at=now,
            )
            stored = repository.get_idempotency(
                namespace_kind,
                namespace_id,
                _OPERATION,
                command.idempotency_key,
            )
            assert stored is not None
            if stored["request_sha256"] != request_hash:
                raise RefreshIdempotencyConflict(
                    "IDEMPOTENCY_CONFLICT: The idempotency key was used "
                    "for a different refresh request."
                )
            if stored["response_json"] != reservation:
                return self._replay(stored, request_hash)
            uow.commit()
        try:
            result = self.capture_coordinator.refresh(
                command,
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
            )
        except Exception:
            with UnitOfWork(self.database, immediate_write=True) as uow:
                assert uow.review_refresh is not None
                uow.review_refresh.release_idempotency(
                    namespace_kind=namespace_kind,
                    namespace_id=namespace_id,
                    operation=_OPERATION,
                    key=command.idempotency_key,
                    reservation_json=reservation,
                )
                uow.commit()
            raise
        with UnitOfWork(self.database, immediate_write=True) as uow:
            assert uow.review_refresh is not None
            repository = uow.review_refresh
            stored = repository.get_idempotency(
                namespace_kind,
                namespace_id,
                _OPERATION,
                command.idempotency_key,
            )
            assert stored is not None
            if stored["request_sha256"] != request_hash:
                raise RefreshIdempotencyConflict(
                    "IDEMPOTENCY_CONFLICT: The idempotency key was used "
                    "for a different refresh request."
                )
            if stored["response_json"] != reservation:
                replay = self._replay(stored, request_hash)
                uow.commit()
                return replay
            repository.complete_idempotency(
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
                operation=_OPERATION,
                key=command.idempotency_key,
                reservation_json=reservation,
                response_json=result.model_dump_json(),
            )
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
            try:
                pending = json.loads(row["response_json"])
            except (TypeError, json.JSONDecodeError):
                pending = None
            if isinstance(pending, dict) and set(pending) == {"reservation"}:
                raise RefreshError(
                    "IDEMPOTENCY_IN_PROGRESS: Capture is already in progress."
                ) from None
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


def _capture_kind(locator: str, previous_kind: str | None) -> str:
    lowered = locator.lower()
    if previous_kind == "git_blob":
        return "git_blob"
    if previous_kind == "doi" or lowered.startswith(
        ("doi:", "https://doi.org/", "http://doi.org/")
    ):
        return "doi"
    if lowered.startswith(("https://", "http://")):
        return "web"
    raise RefreshModeDenied(
        "CAPTURE_POLICY_DENIED: Source locator has no supported capture policy."
    )


def _decode_git_text(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RefreshModeDenied(
            "CAPTURE_POLICY_DENIED: Binary Git content cannot be text re-anchored."
        ) from exc


def _reanchored_selector(
    selector: dict[str, Any],
    quote_text: str,
    text: str,
    *,
    new_path: str | None,
    new_commit_sha: str | None,
    new_blob_sha: str | None,
) -> tuple[dict[str, Any], ReanchorResult]:
    selector_type = selector["type"]
    if selector_type not in {
        "text_quote",
        "char_range",
        "line_range",
        "git_line_range",
    }:
        raise EvidenceUnresolved(
            "selector type cannot be deterministically text re-anchored"
        )
    result = reanchor_text(
        text,
        exact=quote_text,
        prefix=selector.get("prefix"),
        suffix=selector.get("suffix"),
    )
    updated = dict(selector)
    if selector_type in {"text_quote", "char_range"}:
        updated["start"] = result.start
        updated["end"] = result.end
    if selector_type in {"line_range", "git_line_range"}:
        updated["start_line"] = result.start_line
        updated["end_line"] = result.end_line
    if selector_type == "git_line_range":
        if not all((new_path, new_commit_sha, new_blob_sha)):
            raise EvidenceUnresolved(
                "Git selector cannot resolve without immutable provenance"
            )
        updated["path"] = new_path
        updated["commit_sha"] = new_commit_sha
        updated["blob_sha"] = new_blob_sha
        updated["deep_link"] = (
            f"{new_path}#L{result.start_line}-L{result.end_line}"
        )
    return validate_selector(updated), result


def _enqueue_affected(
    repository: ReviewRefreshRepository,
    evidence_id: str,
    *,
    reason: str,
    priority: float,
    detected_at: str,
) -> list[RefreshPlanItem]:
    items: list[RefreshPlanItem] = []
    for kind, entity_id in repository.expand_refresh_targets(
        "evidence",
        evidence_id,
    ):
        row, created = repository.enqueue_refresh(
            refresh_id=f"rfr_{uuid4()}",
            entity_kind=kind,
            entity_id=entity_id,
            reason=reason,
            priority=priority,
            detected_at=detected_at,
            details_json=canonical_json(
                {
                    "evidence_id": evidence_id,
                    "outcome": "manual_review_required",
                }
            ),
        )
        items.append(
            RefreshPlanItem(
                entity=RefreshEntity(kind=kind, id=entity_id),
                reason=reason,
                refresh_item_id=row["id"],
                queue_status=row["status"],
                created=created,
                previous_evidence_span_id=evidence_id,
                anchor_state="stale",
            )
        )
    return items


def _refresh_event(
    evidence: Any,
    created_at: str,
    *,
    outcome: str,
    new_version_id: str | None,
    new_evidence_id: str | None,
) -> dict[str, Any]:
    return {
        "id": f"rev_{uuid4()}",
        "entity_kind": "evidence",
        "entity_id": evidence["id"],
        "action": "refresh_requested",
        "from_state": evidence["anchor_state"],
        "to_state": "stale",
        "note": None,
        "actor_type": "system",
        "actor_id": None,
        "created_at": created_at,
        "metadata_json": canonical_json(
            {
                "outcome": outcome,
                "new_source_version_id": new_version_id,
                "new_evidence_id": new_evidence_id,
            }
        ),
    }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
