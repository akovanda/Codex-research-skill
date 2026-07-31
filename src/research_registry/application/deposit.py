from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from ..contracts.v2 import (
    ClientReference,
    DepositClaim,
    DepositRecordIds,
    IdReference,
    ResearchDepositRequest,
    ResearchDepositResult,
)
from ..db import DatabaseTarget
from ..domain.sources import SourceVersionSpec
from ..ingestion.blobs import (
    BlobStore,
    BlobValidationError,
    FinalizedBlob,
    StagedBlob,
)
from ..models import slugify
from ..models import AuthContext
from ..persistence.repositories import V2BackfillRepository, canonical_json
from ..persistence.unit_of_work import UnitOfWork
from ..retrieval.projection import rebuild_search_documents
from .evidence_anchors import (
    EvidenceAnchorRejected,
    EvidenceAnchorStorageError,
    build_anchor_context,
    validate_anchor,
)
from .source_versions import SourceVersionConflict, SourceVersionService


_OPERATION = "research_deposit_v2"
_DEPOSIT_EVIDENCE_TRUST_TIER = "low"
_V1_STATUS = {
    "supported": "supported",
    "partial": "partial",
    "contested": "conflicted",
    "draft": "insufficient_evidence",
    "rejected": "insufficient_evidence",
    "superseded": "insufficient_evidence",
}
_RUN_MODE = {
    "research": "live_research",
    "reuse": "reuse",
    "refresh": "live_research",
    "import": "synthesis",
    "migration": "synthesis",
    "manual": "synthesis",
}


class DepositError(RuntimeError):
    """Base error for a rejected v2 deposit."""


class DepositReferenceNotFound(DepositError):
    """A referenced record was absent or outside the deposit namespace."""


class IdempotencyConflict(DepositError):
    """An idempotency key was already used for another normalized request."""


class ExpectedRevisionMismatch(DepositError):
    """A claim no longer has the revision expected by the caller."""


@dataclass
class _ClaimPlan:
    request: DepositClaim
    claim_id: str
    existing: Any | None
    revision_id: str
    revision_number: int = 1


class ResearchDepositService:
    """Validate and atomically commit one closed research-deposit/v2 bundle."""

    def __init__(
        self,
        database: str | Path | DatabaseTarget,
        blob_store: BlobStore,
        *,
        max_snapshot_policy: str = "full_content",
        fault_injector: Callable[[str], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.database = database
        self.blob_store = blob_store
        self.source_versions = SourceVersionService(
            database,
            blob_store,
            max_snapshot_policy=max_snapshot_policy,  # type: ignore[arg-type]
        )
        self.fault_injector = fault_injector
        self.clock = clock or (
            lambda: datetime.now(timezone.utc).replace(microsecond=0)
        )

    def deposit(
        self,
        request: ResearchDepositRequest | dict[str, Any],
        *,
        auth: AuthContext | None = None,
    ) -> ResearchDepositResult:
        bundle = (
            request
            if isinstance(request, ResearchDepositRequest)
            else ResearchDepositRequest.model_validate(request)
        )
        normalized = bundle.model_dump(mode="json")
        request_json = canonical_json(normalized)
        request_hash = sha256(request_json.encode("utf-8")).hexdigest()
        namespace_kind = bundle.namespace.kind if bundle.namespace else "user"
        namespace_id = bundle.namespace.id if bundle.namespace else "local"
        if auth is not None and (
            namespace_kind != auth.namespace_kind
            or namespace_id != auth.namespace_id
        ):
            raise DepositError(
                "NAMESPACE_ACCESS_DENIED: The deposit namespace must match "
                "the authenticated namespace."
            )
        actor = {
            "actor_user_id": auth.actor_user_id if auth else None,
            "actor_org_id": auth.actor_org_id if auth else None,
            "api_key_id": auth.api_key_id if auth else None,
        }
        self._validate_bundle_shape(bundle)

        staged = self._stage_snapshots(bundle)
        try:
            self._fault("after_staged_blobs")
            try:
                return self._deposit_transaction(
                    bundle,
                    request_hash=request_hash,
                    namespace_kind=namespace_kind,
                    namespace_id=namespace_id,
                    actor=actor,
                    staged=staged,
                )
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                raise DepositError(
                    "CONCURRENT_WRITE_CONFLICT: The deposit write boundary is busy."
                ) from exc
            except sqlite3.IntegrityError as exc:
                raise DepositError(
                    "CONCURRENT_WRITE_CONFLICT: A concurrent deposit changed "
                    "the target records."
                ) from exc
            except Exception as exc:
                if exc.__class__.__module__.startswith("psycopg"):
                    raise DepositError(
                        "CONCURRENT_WRITE_CONFLICT: A concurrent deposit changed "
                        "the target records."
                    ) from exc
                raise
        finally:
            for item in staged.values():
                if item is not None:
                    self.blob_store.discard(item)

    def _deposit_transaction(
        self,
        bundle: ResearchDepositRequest,
        *,
        request_hash: str,
        namespace_kind: str,
        namespace_id: str,
        actor: dict[str, str | None],
        staged: dict[str, StagedBlob | None],
    ) -> ResearchDepositResult:
        finalized: list[FinalizedBlob] = []
        commit_started = [False]
        try:
            return self._deposit_transaction_core(
                bundle,
                request_hash=request_hash,
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
                actor=actor,
                staged=staged,
                finalized=finalized,
                commit_started=commit_started,
            )
        except Exception as original:
            if not commit_started[0]:
                try:
                    for item in reversed(finalized):
                        self.blob_store.rollback_finalize(item)
                except Exception as cleanup_error:
                    raise cleanup_error from original
            raise

    def _deposit_transaction_core(
        self,
        bundle: ResearchDepositRequest,
        *,
        request_hash: str,
        namespace_kind: str,
        namespace_id: str,
        actor: dict[str, str | None],
        staged: dict[str, StagedBlob | None],
        finalized: list[FinalizedBlob],
        commit_started: list[bool],
    ) -> ResearchDepositResult:
        now = self.clock()
        now_text = now.isoformat()
        reservation = canonical_json({"reservation": uuid4().hex})
        with UnitOfWork(self.database, immediate_write=True) as uow:
            assert uow.deposit is not None
            assert uow.source_versions is not None
            assert uow.connection is not None
            repository = uow.deposit
            projection_repository = V2BackfillRepository(
                uow.connection,
                now_text=now_text,
            )

            self._fault("before_idempotency")
            existing_key = repository.get_idempotency(
                namespace_kind,
                namespace_id,
                _OPERATION,
                bundle.idempotency_key,
            )
            if existing_key is not None:
                return self._existing_idempotency_result(
                    existing_key,
                    request_hash=request_hash,
                    validate_only=bundle.validate_only,
                )
            if not bundle.validate_only:
                repository.reserve_idempotency(
                    namespace_kind=namespace_kind,
                    namespace_id=namespace_id,
                    operation=_OPERATION,
                    key=bundle.idempotency_key,
                    request_sha256=request_hash,
                    reservation_json=reservation,
                    created_at=now_text,
                )
                reserved = repository.get_idempotency(
                    namespace_kind,
                    namespace_id,
                    _OPERATION,
                    bundle.idempotency_key,
                )
                assert reserved is not None
                if reserved["response_json"] != reservation:
                    return self._existing_idempotency_result(
                        reserved,
                        request_hash=request_hash,
                        validate_only=False,
                    )
            self._fault("after_idempotency")

            external_versions, external_evidence = self._resolve_external_records(
                repository,
                bundle,
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
            )
            claim_plans = self._plan_claims(
                repository,
                bundle,
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
            )

            question_id, run_id, topic_id, focal_label = self._write_inquiry_run(
                repository,
                bundle,
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
                actor=actor,
                now=now,
            )

            source_ids: dict[str, str] = {}
            for source in bundle.sources:
                source_ids[source.client_ref] = self._write_source_identity(
                    repository,
                    source,
                    namespace_kind=namespace_kind,
                    namespace_id=namespace_id,
                    actor=actor,
                    visibility=bundle.visibility,
                    now_text=now_text,
                )
            self._fault("after_source_identity")

            version_ids: dict[str, str] = {}
            version_records: dict[str, Any] = dict(external_versions)
            version_snapshots: dict[str, tuple[str | None, str | None]] = {}
            pending_hashes: set[str] = set()
            finalize: list[StagedBlob] = []
            for source in bundle.sources:
                source_id = source_ids[source.client_ref]
                spec = self._source_version_spec(source, source_id)
                prepared = self.source_versions.prepare_in_transaction(
                    uow.source_versions,
                    spec,
                    staged[source.client_ref],
                    pending_content_hashes=pending_hashes,
                )
                version_ids[source.client_ref] = prepared.result.record.id
                version_records[prepared.result.record.id] = prepared.result.record
                version_snapshots[prepared.result.record.id] = (
                    source.version.snapshot.text,
                    source.version.snapshot.policy,
                )
                projection_repository.record_projection_identity(
                    "source",
                    source_id,
                    "source_version",
                    prepared.result.record.id,
                    update_existing=False,
                )
                if prepared.needs_finalize:
                    item = staged[source.client_ref]
                    assert item is not None
                    pending_hashes.add(item.sha256)
                    finalize.append(item)
                elif staged[source.client_ref] is not None:
                    self.blob_store.discard(staged[source.client_ref])  # type: ignore[arg-type]
            self._fault("after_source_version")

            evidence_ids: dict[str, str] = {}
            legacy_excerpt_ids: dict[str, str] = {}
            anchor_contexts: dict[str, Any] = {}
            anchor_warning_counts: dict[str, int] = {}
            for evidence in bundle.evidence:
                source_version_id = (
                    version_ids[evidence.source_version.ref]
                    if isinstance(evidence.source_version, ClientReference)
                    else evidence.source_version.id
                )
                source_id = (
                    source_ids[evidence.source_version.ref]
                    if isinstance(evidence.source_version, ClientReference)
                    else external_versions[source_version_id]["source_id"]
                )
                self._require_private_source(repository, source_id)
                context = anchor_contexts.get(source_version_id)
                if context is None:
                    snapshot_text, snapshot_policy = version_snapshots.get(
                        source_version_id,
                        (None, None),
                    )
                    try:
                        context = build_anchor_context(
                            source_version=version_records[source_version_id],
                            snapshot_text=snapshot_text,
                            snapshot_policy=snapshot_policy,
                            source_version_repository=uow.source_versions,
                            blob_store=self.blob_store,
                        )
                    except EvidenceAnchorStorageError as exc:
                        raise DepositError(str(exc)) from exc
                    anchor_contexts[source_version_id] = context
                try:
                    anchor = validate_anchor(
                        client_ref=evidence.client_ref,
                        selector=evidence.selector.model_dump(
                            mode="python",
                            exclude_none=True,
                        ),
                        quote_text=evidence.quote_text,
                        context=context,
                        resolved_at=now_text,
                    )
                except EvidenceAnchorRejected as exc:
                    raise DepositError(str(exc)) from exc
                if anchor.warning_key is not None:
                    anchor_warning_counts[anchor.warning_key] = (
                        anchor_warning_counts.get(anchor.warning_key, 0) + 1
                    )
                evidence_id = self._new_id("evd")
                excerpt_id = self._new_id("ex")
                evidence_ids[evidence.client_ref] = evidence_id
                legacy_excerpt_ids[evidence_id] = excerpt_id
                selector_json = canonical_json(
                    evidence.selector.model_dump(mode="json", exclude_none=True)
                )
                metadata = {
                    **evidence.metadata,
                    "v1_excerpt_id": excerpt_id,
                    "anchor_validation": anchor.metadata,
                }
                repository.insert_legacy_excerpt(
                    {
                        "id": excerpt_id,
                        "source_id": source_id,
                        "question_id": question_id,
                        "session_id": run_id,
                        "topic_id": topic_id,
                        "focal_label": focal_label,
                        "note": evidence.note or "",
                        "selector_json": selector_json,
                        "quote_text": evidence.quote_text,
                        "confidence": evidence.confidence,
                        "review_state": "unreviewed",
                        "trust_tier": _DEPOSIT_EVIDENCE_TRUST_TIER,
                        "visibility": bundle.visibility,
                        "author_type": self._author_type(bundle),
                        "model_name": bundle.run.provenance.model,
                        "model_version": bundle.run.provenance.model_version,
                        "namespace_kind": namespace_kind,
                        "namespace_id": namespace_id,
                        **actor,
                        "public_index_state": self._index_state(bundle.visibility),
                        "dedupe_key": (
                            f"v2:{namespace_kind}:{namespace_id}:"
                            f"{bundle.idempotency_key}:evidence:{evidence.client_ref}"
                        ),
                        "human_reviewed": 0,
                        "created_at": now_text,
                    }
                )
                repository.insert_evidence(
                    {
                        "id": evidence_id,
                        "source_version_id": source_version_id,
                        "topic_id": topic_id,
                        "question_id": question_id,
                        "session_id": run_id,
                        "quote_text": evidence.quote_text,
                        "quote_sha256": sha256(
                            evidence.quote_text.encode("utf-8")
                        ).hexdigest(),
                        "selector_type": evidence.selector.type,
                        "selector_json": selector_json,
                        "note": evidence.note,
                        "confidence": evidence.confidence,
                        "anchor_state": anchor.anchor_state,
                        "last_resolved_at": anchor.last_resolved_at,
                        "review_state": "unreviewed",
                        "trust_tier": _DEPOSIT_EVIDENCE_TRUST_TIER,
                        "created_by_model": bundle.run.provenance.model,
                        "created_at": now_text,
                        "metadata_json": canonical_json(metadata),
                    }
                )
                projection_repository.record_projection_identity(
                    "excerpt",
                    excerpt_id,
                    "evidence",
                    evidence_id,
                    update_existing=False,
                )
            self._fault("after_evidence")

            for evidence_id, row in external_evidence.items():
                legacy_excerpt_ids[evidence_id] = self._legacy_excerpt_id(row)

            claim_ids: dict[str, str] = {}
            revision_ids: dict[str, str] = {}
            claim_links: dict[str, list[tuple[str, str | None, float]]] = {}
            for plan in claim_plans:
                claim = plan.request
                plan.revision_number = (
                    repository.next_claim_revision_number(plan.claim_id)
                    if plan.existing is not None
                    else 1
                )
                claim_ids[claim.client_ref] = plan.claim_id
                revision_ids[claim.client_ref] = plan.revision_id
                if plan.existing is None:
                    repository.insert_claim(
                        self._legacy_claim_values(
                            plan,
                            bundle,
                            question_id=question_id,
                            run_id=run_id,
                            topic_id=topic_id,
                            focal_label=focal_label,
                            namespace_kind=namespace_kind,
                            namespace_id=namespace_id,
                            actor=actor,
                            now_text=now_text,
                        )
                    )
                repository.insert_claim_revision(
                    {
                        "id": plan.revision_id,
                        "claim_id": plan.claim_id,
                        "revision_number": plan.revision_number,
                        "title": claim.title,
                        "statement": claim.statement,
                        "status": claim.status,
                        "confidence": claim.confidence,
                        "supersedes_revision_id": (
                            plan.existing["current_revision_id"]
                            if plan.existing is not None
                            else None
                        ),
                        "created_by_model": bundle.run.provenance.model,
                        "created_at": now_text,
                        "metadata_json": canonical_json(claim.metadata),
                    }
                )
                projection_repository.record_projection_identity(
                    "claim",
                    plan.claim_id,
                    "claim_revision",
                    plan.revision_id,
                    update_existing=True,
                )
            self._fault("after_claim")

            for plan in claim_plans:
                links: list[tuple[str, str | None, float]] = []
                seen_evidence: set[str] = set()
                for link in plan.request.evidence:
                    evidence_id = (
                        evidence_ids[link.evidence.ref]
                        if isinstance(link.evidence, ClientReference)
                        else link.evidence.id
                    )
                    if evidence_id in seen_evidence:
                        raise DepositError(
                            "a claim revision cannot link the same evidence twice"
                        )
                    seen_evidence.add(evidence_id)
                    repository.insert_claim_evidence(
                        {
                            "claim_revision_id": plan.revision_id,
                            "evidence_span_id": evidence_id,
                            "relationship": link.relationship,
                            "rationale": link.rationale,
                            "weight": link.weight,
                            "created_at": now_text,
                        }
                    )
                    if link.relationship in {"supports", "qualifies"}:
                        links.append(
                            (
                                legacy_excerpt_ids[evidence_id],
                                link.rationale,
                                link.weight,
                            )
                        )
                claim_links[plan.claim_id] = links
            self._fault("after_claim_relationship")

            report_id = None
            if bundle.report is not None:
                report_id = self._new_id("rpt")
                guidance = dict(bundle.report.guidance or {})
                if bundle.report.metadata:
                    guidance["_v2_metadata"] = bundle.report.metadata
                repository.insert_report(
                    {
                        "id": report_id,
                        "question_id": question_id,
                        "session_id": run_id,
                        "title": bundle.report.title,
                        "focal_label": focal_label,
                        "summary_md": bundle.report.summary_md,
                        "report_kind": bundle.report.report_kind,
                        "guidance_json": canonical_json(guidance),
                        "visibility": bundle.visibility,
                        "author_type": self._author_type(bundle),
                        "model_name": bundle.run.provenance.model,
                        "model_version": bundle.run.provenance.model_version,
                        "namespace_kind": namespace_kind,
                        "namespace_id": namespace_id,
                        **actor,
                        "public_index_state": self._index_state(bundle.visibility),
                        "dedupe_key": (
                            f"v2:{namespace_kind}:{namespace_id}:"
                            f"{bundle.idempotency_key}:report"
                        ),
                        "created_at": now_text,
                    }
                )
                report_claim_ids: set[str] = set()
                for reference in bundle.report.claims:
                    claim_id = (
                        claim_ids[reference.ref]
                        if isinstance(reference, ClientReference)
                        else reference.id
                    )
                    if claim_id in report_claim_ids:
                        raise DepositError(
                            "a report cannot reference the same claim twice"
                        )
                    report_claim_ids.add(claim_id)
                    repository.insert_report_claim(report_id, claim_id)
                repository.mark_question_answered(question_id)
                projection_repository.record_projection_identity(
                    "report",
                    report_id,
                    "report",
                    report_id,
                    update_existing=False,
                )
            self._fault("after_report")

            self._fault("before_current_pointer")
            for plan in claim_plans:
                claim = plan.request
                repository.update_claim_pointer(
                    {
                        "claim_id": plan.claim_id,
                        "revision_id": plan.revision_id,
                        "title": claim.title,
                        "statement": claim.statement,
                        "legacy_status": _V1_STATUS[claim.status],
                        "confidence": claim.confidence,
                        "canonical_key": (
                            claim.canonical_key
                            or (
                                plan.existing["canonical_key"]
                                if plan.existing is not None
                                else None
                            )
                        ),
                        "scope_json": (
                            canonical_json(claim.scope)
                            if claim.scope is not None
                            else None
                        ),
                        "session_id": run_id,
                        "topic_id": topic_id,
                        "updated_at": now_text,
                    }
                )
                repository.replace_claim_excerpts(
                    plan.claim_id, claim_links[plan.claim_id]
                )
            self._fault("after_current_pointer")

            if not bundle.validate_only:
                assert uow.connection is not None
                rebuild_search_documents(uow.connection)

            committed_records = DepositRecordIds(
                question_id=question_id,
                run_id=run_id,
                source_ids=source_ids,
                source_version_ids=version_ids,
                evidence_ids=evidence_ids,
                claim_ids=claim_ids,
                claim_revision_ids=revision_ids,
                report_id=report_id,
            )
            if not bundle.validate_only:
                repository.insert_deposit_audit(
                    {
                        "id": self._new_id("audit"),
                        "record_id": bundle.idempotency_key,
                        **actor,
                        "details_json": canonical_json(
                            {
                                "namespace_kind": namespace_kind,
                                "namespace_id": namespace_id,
                                "request_sha256": request_hash,
                                "claim_revision_ids": sorted(
                                    revision_ids.values()
                                ),
                            }
                        ),
                        "created_at": now_text,
                    }
                )
            result = ResearchDepositResult(
                protocol="research-deposit-result/v2",
                status="validated" if bundle.validate_only else "committed",
                committed=not bundle.validate_only,
                idempotent_replay=False,
                request_sha256=request_hash,
                records=(
                    DepositRecordIds() if bundle.validate_only else committed_records
                ),
                warnings=[
                    f"{warning_key}:{count}"
                    for warning_key, count in sorted(
                        anchor_warning_counts.items()
                    )
                ],
            )
            response_json = canonical_json(result.model_dump(mode="json"))
            self._fault("after_response_serialization")

            if bundle.validate_only:
                return result

            repository.complete_idempotency(
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
                operation=_OPERATION,
                key=bundle.idempotency_key,
                reservation_json=reservation,
                response_json=response_json,
            )
            completed = repository.get_idempotency(
                namespace_kind,
                namespace_id,
                _OPERATION,
                bundle.idempotency_key,
            )
            if completed is None or completed["response_json"] != response_json:
                raise IdempotencyConflict(
                    "IDEMPOTENCY_CONFLICT: The idempotency reservation was "
                    "not owned by this transaction."
                )

            self._fault("before_blob_finalize")
            for item in finalize:
                finalized.append(self.blob_store.finalize(item))
            self._fault("after_blob_finalize")
            self._fault("before_commit")
            commit_started[0] = True
            uow.commit()
            return result

    def _resolve_external_records(
        self,
        repository: Any,
        bundle: ResearchDepositRequest,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        versions: dict[str, Any] = {}
        evidence_rows: dict[str, Any] = {}
        for evidence in bundle.evidence:
            if isinstance(evidence.source_version, IdReference):
                record_id = evidence.source_version.id
                row = repository.get_source_version_scoped(
                    record_id,
                    namespace_kind=namespace_kind,
                    namespace_id=namespace_id,
                )
                if row is None:
                    raise DepositReferenceNotFound(
                        f"source version reference not found: {record_id}"
                    )
                versions[record_id] = row
        for claim in bundle.claims:
            for link in claim.evidence:
                if isinstance(link.evidence, IdReference):
                    record_id = link.evidence.id
                    if record_id in evidence_rows:
                        continue
                    row = repository.get_evidence_scoped(
                        record_id,
                        namespace_kind=namespace_kind,
                        namespace_id=namespace_id,
                    )
                    if row is None:
                        raise DepositReferenceNotFound(
                            f"evidence reference not found: {record_id}"
                        )
                    evidence_rows[record_id] = row
        for reference in bundle.report.claims if bundle.report else []:
            if isinstance(reference, IdReference):
                row = repository.get_claim_scoped(
                    reference.id,
                    namespace_kind=namespace_kind,
                    namespace_id=namespace_id,
                )
                if row is None:
                    raise DepositReferenceNotFound(
                        f"claim reference not found: {reference.id}"
                    )
        return versions, evidence_rows

    def _plan_claims(
        self,
        repository: Any,
        bundle: ResearchDepositRequest,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> list[_ClaimPlan]:
        plans: list[_ClaimPlan] = []
        targets: set[str] = set()
        canonical_keys: set[str] = set()
        for claim in bundle.claims:
            existing = None
            if claim.claim_id is not None:
                existing = repository.get_claim_scoped(
                    claim.claim_id,
                    namespace_kind=namespace_kind,
                    namespace_id=namespace_id,
                )
                if existing is None:
                    raise DepositReferenceNotFound(
                        f"claim reference not found: {claim.claim_id}"
                    )
            elif claim.canonical_key is not None:
                if claim.canonical_key in canonical_keys:
                    raise DepositError(
                        "claim canonical keys must be unique within a deposit"
                    )
                canonical_keys.add(claim.canonical_key)
                existing = repository.find_claim_by_canonical(
                    claim.canonical_key,
                    namespace_kind=namespace_kind,
                    namespace_id=namespace_id,
                )
            if existing is not None:
                if existing["visibility"] == "public":
                    raise DepositError(
                        "PUBLIC_PARENT_MUTATION_DENIED: A private deposit "
                        "cannot revise a public claim."
                    )
                if existing["current_revision_id"] is None:
                    raise DepositError("existing claim has no current revision")
                if (
                    claim.canonical_key is not None
                    and existing["canonical_key"] not in {None, claim.canonical_key}
                ):
                    raise SourceVersionConflict(
                        "claim canonical key identifies a different claim"
                    )
                if (
                    claim.expected_revision_id is not None
                    and claim.expected_revision_id
                    != existing["current_revision_id"]
                ):
                    raise ExpectedRevisionMismatch(
                        "claim current revision does not match expected revision"
                    )
                claim_id = existing["id"]
            else:
                if claim.expected_revision_id is not None:
                    raise ExpectedRevisionMismatch(
                        "expected revision requires an existing claim"
                    )
                claim_id = self._new_id("clm")
            if claim_id in targets:
                raise DepositError(
                    "a deposit cannot revise the same claim more than once"
                )
            targets.add(claim_id)
            plans.append(
                _ClaimPlan(
                    request=claim,
                    claim_id=claim_id,
                    existing=existing,
                    revision_id=self._new_id("clmr"),
                )
            )
        return plans

    def _write_inquiry_run(
        self,
        repository: Any,
        bundle: ResearchDepositRequest,
        *,
        namespace_kind: str,
        namespace_id: str,
        actor: dict[str, str | None],
        now: datetime,
    ) -> tuple[str | None, str | None, str | None, str]:
        inquiry = bundle.inquiry
        if inquiry is None:
            return None, None, None, "research"
        label = inquiry.topic_label or self._short_label(inquiry.prompt)
        focus = {"label": label, **dict(inquiry.focus or {})}
        slug = slugify(label)
        topic = repository.find_topic(
            slug=slug,
            namespace_kind=namespace_kind,
            namespace_id=namespace_id,
        )
        if topic is None:
            topic_id = self._new_id("topic")
            repository.insert_topic(
                {
                    "id": topic_id,
                    "label": label,
                    "slug": slug,
                    "focus_json": canonical_json(focus),
                    "namespace_kind": namespace_kind,
                    "namespace_id": namespace_id,
                    **actor,
                    "dedupe_key": (
                        f"v2:topic:{namespace_kind}:{namespace_id}:{slug}"
                    ),
                    "created_at": now.isoformat(),
                }
            )
        else:
            topic_id = topic["id"]
        normalized_prompt = " ".join(inquiry.prompt.strip().lower().split())
        question = repository.find_question(
            topic_id=topic_id,
            normalized_prompt=normalized_prompt,
            namespace_kind=namespace_kind,
            namespace_id=namespace_id,
        )
        if question is None:
            question_id = self._new_id("q")
            repository.insert_question(
                {
                    "id": question_id,
                    "topic_id": topic_id,
                    "prompt": inquiry.prompt,
                    "normalized_prompt": normalized_prompt,
                    "focus_json": canonical_json(focus),
                    "visibility": bundle.visibility,
                    "author_type": self._author_type(bundle),
                    "namespace_kind": namespace_kind,
                    "namespace_id": namespace_id,
                    **actor,
                    "public_index_state": self._index_state(bundle.visibility),
                    "dedupe_key": (
                        f"v2:question:{namespace_kind}:{namespace_id}:"
                        f"{sha256((topic_id + ':' + normalized_prompt).encode()).hexdigest()}"
                    ),
                    "created_at": now.isoformat(),
                }
            )
        else:
            if question["visibility"] == "public":
                raise DepositError(
                    "PUBLIC_PARENT_MUTATION_DENIED: A private deposit cannot "
                    "attach a private run or report to a public question."
                )
            question_id = question["id"]
        run_id = self._new_id("sess")
        started = bundle.run.started_at or now
        finished = bundle.run.finished_at or now
        repository.insert_session(
            {
                "id": run_id,
                "question_id": question_id,
                "prompt": inquiry.prompt,
                "model_name": bundle.run.provenance.model or "unspecified",
                "model_version": (
                    bundle.run.provenance.model_version or "unspecified"
                ),
                "mode": _RUN_MODE[bundle.run.mode],
                "source_signals_json": canonical_json(
                    self._provenance_signals(bundle)
                ),
                "notes": bundle.run.notes,
                "visibility": bundle.visibility,
                "author_type": self._author_type(bundle),
                "namespace_kind": namespace_kind,
                "namespace_id": namespace_id,
                **actor,
                "public_index_state": self._index_state(bundle.visibility),
                "dedupe_key": (
                    f"v2:run:{namespace_kind}:{namespace_id}:"
                    f"{bundle.idempotency_key}"
                ),
                "expires_at": (now + timedelta(days=30)).isoformat(),
                "created_at": now.isoformat(),
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
            }
        )
        return question_id, run_id, topic_id, label

    def _write_source_identity(
        self,
        repository: Any,
        source: Any,
        *,
        namespace_kind: str,
        namespace_id: str,
        actor: dict[str, str | None],
        visibility: str,
        now_text: str,
    ) -> str:
        identity = source.identity
        dedupe_key = (
            f"v2:source:{namespace_kind}:{namespace_id}:{identity.canonical_key}"
            if identity.canonical_key is not None
            else None
        )
        existing = repository.find_source(
            dedupe_key=dedupe_key,
            locator=identity.locator,
            namespace_kind=namespace_kind,
            namespace_id=namespace_id,
        )
        if existing is not None:
            if (
                existing["namespace_kind"] != namespace_kind
                or existing["namespace_id"] != namespace_id
                or existing["locator"] != identity.locator
                or existing["source_type"] != identity.source_type
            ):
                raise SourceVersionConflict(
                    "source identity key already identifies another source"
                )
            if existing["visibility"] == "public":
                raise DepositError(
                    "PUBLIC_PARENT_MUTATION_DENIED: A private deposit cannot "
                    "add a version or evidence to a public source."
                )
            return existing["id"]
        source_id = self._new_id("src")
        version = source.version
        repository.insert_source(
            {
                "id": source_id,
                "locator": identity.locator,
                "title": identity.title,
                "source_type": identity.source_type,
                "site_name": identity.site_name,
                "published_at": (
                    version.published_at.isoformat()
                    if version.published_at is not None
                    else None
                ),
                "accessed_at": version.retrieved_at.isoformat(),
                "author": identity.author,
                "content_sha256": version.content_sha256,
                "snapshot_present": int(
                    version.snapshot.policy in {"extracted_text", "full_content"}
                ),
                "last_verified_at": version.retrieved_at.isoformat(),
                "trust_tier": self._source_trust(identity.source_type),
                "visibility": visibility,
                "namespace_kind": namespace_kind,
                "namespace_id": namespace_id,
                **actor,
                "public_index_state": self._index_state(visibility),
                "dedupe_key": dedupe_key,
                "created_at": now_text,
            }
        )
        return source_id

    @staticmethod
    def _require_private_source(repository: Any, source_id: str) -> None:
        source = repository.conn.execute(
            "SELECT visibility FROM sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        if source is None:
            raise DepositReferenceNotFound(
                f"source reference not found: {source_id}"
            )
        if source["visibility"] == "public":
            raise DepositError(
                "PUBLIC_PARENT_MUTATION_DENIED: A private deposit cannot add "
                "evidence to a public source version."
            )

    def _source_version_spec(self, source: Any, source_id: str) -> SourceVersionSpec:
        version = source.version
        repository = version.repository
        snapshot_bytes = (
            version.snapshot.text.encode("utf-8")
            if version.snapshot.policy in {"extracted_text", "full_content"}
            and version.snapshot.text is not None
            else None
        )
        metadata = {
            **version.metadata,
            "source_identity_metadata": source.identity.metadata,
        }
        if repository is not None:
            metadata["repository"] = {
                "remote_fingerprint": repository.remote_fingerprint,
                "file_mode": repository.file_mode,
            }
        return SourceVersionSpec(
            source_id=source_id,
            version_key=version.version_key,
            version_kind=version.version_kind,
            retrieved_at=version.retrieved_at,
            published_at=version.published_at,
            content_sha256=version.content_sha256,
            canonical_locator=version.canonical_locator,
            snapshot_policy=version.snapshot.policy,
            snapshot_bytes=snapshot_bytes,
            media_type=version.snapshot.media_type,
            byte_count=version.snapshot.byte_count,
            parser_name=version.parser_name,
            parser_version=version.parser_version,
            repository_locator=(
                repository.repository_id if repository is not None else None
            ),
            commit_sha=repository.commit_sha if repository is not None else None,
            blob_sha=repository.blob_sha if repository is not None else None,
            path=repository.path if repository is not None else None,
            metadata=metadata,
        )

    def _stage_snapshots(
        self, bundle: ResearchDepositRequest
    ) -> dict[str, StagedBlob | None]:
        staged: dict[str, StagedBlob | None] = {}
        try:
            for source in bundle.sources:
                snapshot = source.version.snapshot
                text = snapshot.text
                if snapshot.policy == "metadata_only" and text is not None:
                    raise BlobValidationError(
                        "metadata_only source versions must not include snapshot text"
                    )
                if snapshot.policy == "evidence_only":
                    if text is not None:
                        self._validate_declared_text(
                            text,
                            content_sha256=source.version.content_sha256,
                            byte_count=snapshot.byte_count,
                        )
                    staged[source.client_ref] = None
                    continue
                if text is None:
                    staged[source.client_ref] = None
                    continue
                staged[source.client_ref] = self.blob_store.stage_bytes(
                    text.encode("utf-8"),
                    expected_sha256=source.version.content_sha256,
                    expected_byte_count=snapshot.byte_count,
                    media_type=snapshot.media_type,
                )
            return staged
        except Exception:
            for item in staged.values():
                if item is not None:
                    self.blob_store.discard(item)
            raise

    @staticmethod
    def _validate_declared_text(
        text: str, *, content_sha256: str, byte_count: int | None
    ) -> None:
        encoded = text.encode("utf-8")
        if sha256(encoded).hexdigest() != content_sha256:
            raise BlobValidationError("SHA-256 does not match snapshot text")
        if byte_count is not None and byte_count != len(encoded):
            raise BlobValidationError("byte count does not match snapshot text")

    @staticmethod
    def _validate_bundle_shape(bundle: ResearchDepositRequest) -> None:
        if bundle.visibility != "private":
            raise DepositError(
                "deposit cannot publish; visibility must be private"
            )
        if (
            bundle.inquiry is None
            and (bundle.evidence or bundle.claims or bundle.report is not None)
        ):
            raise DepositError(
                "inquiry is required when depositing evidence, claims, or a report"
            )

    def _legacy_claim_values(
        self,
        plan: _ClaimPlan,
        bundle: ResearchDepositRequest,
        *,
        question_id: str | None,
        run_id: str | None,
        topic_id: str | None,
        focal_label: str,
        namespace_kind: str,
        namespace_id: str,
        actor: dict[str, str | None],
        now_text: str,
    ) -> dict[str, Any]:
        claim = plan.request
        assert question_id is not None
        return {
            "id": plan.claim_id,
            "question_id": question_id,
            "session_id": run_id,
            "topic_id": topic_id,
            "title": claim.title,
            "focal_label": focal_label,
            "statement": claim.statement,
            "legacy_status": _V1_STATUS[claim.status],
            "confidence": claim.confidence,
            "visibility": bundle.visibility,
            "author_type": self._author_type(bundle),
            "model_name": bundle.run.provenance.model,
            "model_version": bundle.run.provenance.model_version,
            "namespace_kind": namespace_kind,
            "namespace_id": namespace_id,
            **actor,
            "public_index_state": self._index_state(bundle.visibility),
            "dedupe_key": (
                f"v2:claim:{namespace_kind}:{namespace_id}:{claim.canonical_key}"
                if claim.canonical_key is not None
                else None
            ),
            "created_at": now_text,
            "canonical_key": claim.canonical_key,
            "scope_json": (
                canonical_json(claim.scope) if claim.scope is not None else None
            ),
        }

    @staticmethod
    def _legacy_excerpt_id(row: Any) -> str:
        metadata = json.loads(row["metadata_json"])
        excerpt_id = metadata.get("v1_excerpt_id") or metadata.get(
            "legacy_excerpt_id"
        )
        if not isinstance(excerpt_id, str) or not excerpt_id:
            raise DepositError("referenced evidence has no v1 compatibility mirror")
        return excerpt_id

    @staticmethod
    def _existing_idempotency_result(
        row: Any, *, request_hash: str, validate_only: bool
    ) -> ResearchDepositResult:
        if row["request_sha256"] != request_hash:
            raise IdempotencyConflict(
                "IDEMPOTENCY_CONFLICT: The idempotency key was already used "
                "for a different request."
            )
        if validate_only:
            return ResearchDepositResult(
                protocol="research-deposit-result/v2",
                status="validated",
                committed=False,
                idempotent_replay=False,
                request_sha256=request_hash,
                records=DepositRecordIds(),
                warnings=[],
            )
        try:
            original = ResearchDepositResult.model_validate_json(
                row["response_json"]
            )
        except Exception as exc:
            raise DepositError("stored idempotency receipt is invalid") from exc
        return original.model_copy(update={"idempotent_replay": True})

    def _fault(self, step: str) -> None:
        if self.fault_injector is not None:
            self.fault_injector(step)

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex[:12]}"

    @staticmethod
    def _author_type(bundle: ResearchDepositRequest) -> str:
        return (
            "human"
            if bundle.run.provenance.actor_type == "human"
            else "agent"
        )

    @staticmethod
    def _index_state(visibility: str) -> str:
        return "private" if visibility == "private" else "namespace_only"

    @staticmethod
    def _source_trust(source_type: str) -> str:
        if source_type in {
            "official-docs",
            "paper",
            "report",
            "dataset",
            "code",
            "test",
            "documentation",
            "script",
        }:
            return "medium"
        return "low"

    @staticmethod
    def _provenance_signals(bundle: ResearchDepositRequest) -> list[str]:
        provenance = bundle.run.provenance
        values = {
            "actor_id": provenance.actor_id,
            "client_name": provenance.client_name,
            "client_version": provenance.client_version,
            "provider": provenance.provider,
            "plugin_version": provenance.plugin_version,
            "prompt_sha256": provenance.prompt_sha256,
        }
        return [
            "research-deposit/v2",
            f"idempotency:{bundle.idempotency_key}",
            *[
                f"{name}:{value}"
                for name, value in values.items()
                if value is not None
            ],
        ]

    @staticmethod
    def _short_label(prompt: str) -> str:
        cleaned = " ".join(prompt.split())
        return cleaned if len(cleaned) <= 96 else cleaned[:95].rstrip() + "…"
