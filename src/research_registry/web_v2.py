from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from . import __version__
from .application.fetch import ResearchFetchService
from .application.search import ResearchSearchService
from .application.source_versions import SourceVersionService
from .config import Settings
from .contracts.common import ClosedModel
from .contracts.v2 import (
    ResearchDepositResult,
    ResearchSearchRequest,
)
from .ingestion.blobs import FilesystemBlobStore
from .legacy_feature import legacy_mcp_tools_enabled
from .persistence.read_adapter import (
    CurrentRetrievalAdapter,
    ReadAccess,
    RetrievalCandidate,
)


class BadgeView(ClosedModel):
    label: str
    tone: Literal["neutral", "positive", "warning", "danger"] = "neutral"


class SearchHitView(ClosedModel):
    id: str
    kind: str
    title: str
    summary: str
    href: str
    score: float
    score_reasons: list[str] = Field(default_factory=list)
    badges: list[BadgeView] = Field(default_factory=list)
    evidence_count: int
    updated_at: str | None


class SearchPageView(ClosedModel):
    query: str
    hits: list[SearchHitView] = Field(default_factory=list)
    next_cursor: str | None = None
    kind: str | None = None
    review_state: str | None = None
    conflict_state: str | None = None
    freshness: str | None = None
    include_private: bool


class RevisionView(ClosedModel):
    id: str
    revision_number: int
    title: str
    statement: str
    status: str
    confidence: float
    created_at: str
    supersedes_revision_id: str | None = None


class SourceVersionView(ClosedModel):
    id: str
    source_id: str
    version_key: str
    version_kind: str
    retrieved_at: str
    published_at: str | None = None
    content_sha256: str
    canonical_locator: str
    media_type: str | None = None
    byte_count: int | None = None
    parser_name: str | None = None
    parser_version: str | None = None
    repository: str | None = None
    commit_sha: str | None = None
    blob_sha: str | None = None
    path: str | None = None
    snapshot_policy: str | None = None
    snapshot_available: bool
    review_state: str
    trust_tier: str | None = None
    conflict_state: str
    change_label: str
    byte_delta: int | None = None


class EvidenceView(ClosedModel):
    id: str
    quote_text: str
    note: str | None = None
    relationship: str
    rationale: str | None = None
    confidence: float
    anchor_state: str
    review_state: str
    trust_tier: str
    source_id: str
    source_title: str
    source_version_id: str
    source_version: SourceVersionView | None = None


class ClaimDetailView(ClosedModel):
    id: str
    title: str
    statement: str
    status: str
    confidence: float
    review_state: str
    conflict_state: str
    freshness: str
    scope: dict[str, Any] = Field(default_factory=dict)
    current_revision: RevisionView
    revisions: list[RevisionView]
    evidence_groups: dict[str, list[EvidenceView]]
    reports: list[dict[str, Any]]
    reviews: list[dict[str, Any]]
    refresh: list[dict[str, Any]]
    can_review: bool
    error: str | None = None


class RelatedClaimView(ClosedModel):
    claim_id: str
    revision_id: str
    revision_number: int
    title: str
    status: str
    relationship: str
    rationale: str | None = None
    weight: float
    review_state: str
    conflict_state: str


class EvidenceDetailView(ClosedModel):
    id: str
    quote_text: str
    note: str | None = None
    selector_type: str
    selector: dict[str, Any] = Field(default_factory=dict)
    confidence: float
    anchor_state: str
    review_state: str
    trust_tier: str
    source_id: str
    source_title: str
    source_url: str | None = None
    source_version: SourceVersionView
    related_claims: list[RelatedClaimView]
    reviews: list[dict[str, Any]]
    refresh: list[dict[str, Any]]
    can_review: bool


class SourceDetailView(ClosedModel):
    id: str
    title: str
    locator: str | None
    source_type: str | None
    review_state: str
    conflict_state: str
    trust_tier: str | None
    versions: list[SourceVersionView]
    evidence: list[EvidenceView]
    reviews: list[dict[str, Any]]
    refresh: list[dict[str, Any]]
    can_review: bool


class SourceVersionDetailView(ClosedModel):
    source_title: str
    version: SourceVersionView
    evidence: list[EvidenceView]
    reviews: list[dict[str, Any]]
    refresh: list[dict[str, Any]]
    can_review: bool


class InboxItemView(ClosedModel):
    id: str
    kind: str
    title: str
    href: str
    reasons: list[str]
    badges: list[BadgeView]
    evidence_count: int
    updated_at: str | None
    severity: int


class ReviewInboxView(ClosedModel):
    items: list[InboxItemView]


class RefreshItemView(ClosedModel):
    id: str
    entity_kind: str
    entity_id: str
    title: str
    href: str
    reason: str
    status: str
    priority: float
    detected_at: str
    resolved_at: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RefreshQueueView(ClosedModel):
    items: list[RefreshItemView]


class DepositReceiptView(ClosedModel):
    key: str
    receipt: ResearchDepositResult
    review_required: bool


class StatusView(ClosedModel):
    application_version: str
    schema_version: str
    database_type: str
    database_health: str
    blob_backend: str
    blob_health: str
    blob_referenced_objects: int
    blob_missing_objects: int
    migration_state: str
    capture_mode: str
    mcp_transports: str
    legacy_tools: str
    retrieval_profile: str
    embedding_status: str
    refresh_pending: int
    refresh_running: int
    refresh_failed: int
    backup_age: str
    backup_integrity: str
    data_path: str
    database_path: str


class V2WebViewService:
    """Build bounded, presentation-only models from v2 application reads."""

    def __init__(self, database: Any, settings: Settings):
        self.database = database
        self.settings = settings
        self.retrieval = CurrentRetrievalAdapter(database)
        self.search = ResearchSearchService(self.retrieval)
        self.fetch = ResearchFetchService(self.retrieval)

    def search_page(
        self,
        query: str,
        *,
        access: ReadAccess,
        kind: str | None = None,
        review_state: str | None = None,
        conflict_state: str | None = None,
        freshness: str | None = None,
        cursor: str | None = None,
    ) -> SearchPageView:
        cleaned = query.strip()
        if not cleaned:
            return SearchPageView(
                query="",
                kind=kind,
                review_state=review_state,
                conflict_state=conflict_state,
                freshness=freshness,
                include_private=access.include_private,
            )
        request = ResearchSearchRequest(
            protocol="research-search/v2",
            query=cleaned,
            kinds=[kind] if kind else [],
            review_states=[review_state] if review_state else [],
            conflict_states=[conflict_state] if conflict_state else [],
            freshness=[freshness] if freshness else [],
            include_private=access.include_private,
            limit=50,
            cursor=cursor,
            explain=True,
        )
        result = self.search.search(request, access=access)
        return SearchPageView(
            query=result.query,
            hits=[
                SearchHitView(
                    id=hit.id,
                    kind=hit.kind,
                    title=hit.title,
                    summary=hit.summary,
                    href=self.record_href(hit.kind, hit.id),
                    score=hit.score,
                    score_reasons=list(hit.matched_by),
                    badges=self._badges(
                        kind=hit.kind,
                        review_state=hit.review_state,
                        conflict_state=hit.conflict_state,
                        freshness=hit.freshness,
                    ),
                    evidence_count=hit.evidence_count,
                    updated_at=(
                        hit.updated_at.isoformat() if hit.updated_at else None
                    ),
                )
                for hit in result.hits
            ],
            next_cursor=result.next_cursor,
            kind=kind,
            review_state=review_state,
            conflict_state=conflict_state,
            freshness=freshness,
            include_private=access.include_private,
        )

    def claim_detail(
        self,
        claim_id: str,
        *,
        access: ReadAccess,
        can_review: bool,
        error: str | None = None,
    ) -> ClaimDetailView:
        result = self.fetch.get(
            record_id=claim_id,
            include=[
                "current_revision",
                "revision_history",
                "evidence",
                "source_versions",
                "reviews",
                "reports",
                "refresh",
            ],
            depth=1,
            access=access,
        )
        if result.kind != "claim":
            raise ValueError("RECORD_NOT_FOUND: The claim was not found.")
        current_raw = result.includes.get("current_revision")
        if not isinstance(current_raw, dict):
            raise ValueError(
                "CURRENT_REVISION_NOT_FOUND: The current claim revision was not found."
            )
        revisions = [
            self._revision(item)
            for item in self._list_of_dicts(result.includes.get("revision_history"))
        ]
        versions = self._source_versions(
            self._list_of_dicts(result.includes.get("source_versions"))
        )
        version_by_id = {item.id: item for item in versions}
        groups: dict[str, list[EvidenceView]] = {
            "supports": [],
            "refutes": [],
            "qualifies": [],
            "contextualizes": [],
        }
        for item in self._list_of_dicts(result.includes.get("evidence")):
            evidence = self._evidence(item, version_by_id)
            groups.setdefault(evidence.relationship, []).append(evidence)
        return ClaimDetailView(
            id=result.id,
            title=result.title,
            statement=result.text,
            status=str(result.record.get("status") or "draft"),
            confidence=float(result.record.get("confidence") or 0),
            review_state=result.review_state or "unreviewed",
            conflict_state=result.conflict_state or "none",
            freshness=result.freshness or "unknown",
            scope=(
                result.record.get("scope")
                if isinstance(result.record.get("scope"), dict)
                else {}
            ),
            current_revision=self._revision(current_raw),
            revisions=revisions,
            evidence_groups=groups,
            reports=self._list_of_dicts(result.includes.get("reports")),
            reviews=self._list_of_dicts(result.includes.get("reviews")),
            refresh=self._list_of_dicts(result.includes.get("refresh")),
            can_review=can_review,
            error=error,
        )

    def evidence_detail(
        self,
        evidence_id: str,
        *,
        access: ReadAccess,
        can_review: bool,
    ) -> EvidenceDetailView:
        result = self.fetch.get(
            record_id=evidence_id,
            include=["evidence", "source_versions", "reviews", "refresh"],
            depth=1,
            access=access,
        )
        if result.kind != "evidence":
            raise ValueError("RECORD_NOT_FOUND: The evidence span was not found.")
        versions = self._source_versions(
            self._list_of_dicts(result.includes.get("source_versions"))
        )
        if not versions:
            raise ValueError(
                "SOURCE_VERSION_NOT_FOUND: The evidence source version was not found."
            )
        related = self.retrieval.list_claim_revisions_for_evidence(
            evidence_id,
            access=access,
        )
        selector = result.record.get("selector")
        return EvidenceDetailView(
            id=result.id,
            quote_text=result.text,
            note=self._optional_string(result.record.get("note")),
            selector_type=str(result.record.get("selector_type") or "unknown"),
            selector=selector if isinstance(selector, dict) else {},
            confidence=float(result.record.get("confidence") or 0),
            anchor_state=str(result.record.get("anchor_state") or "unverified"),
            review_state=result.review_state or "unreviewed",
            trust_tier=str(result.record.get("trust_tier") or "low"),
            source_id=str(result.record.get("source_id") or ""),
            source_title=result.title,
            source_url=result.url,
            source_version=versions[0],
            related_claims=[RelatedClaimView.model_validate(item) for item in related],
            reviews=self._list_of_dicts(result.includes.get("reviews")),
            refresh=self._list_of_dicts(result.includes.get("refresh")),
            can_review=can_review,
        )

    def source_detail(
        self,
        source_id: str,
        *,
        access: ReadAccess,
        can_review: bool,
    ) -> SourceDetailView:
        result = self.fetch.get(
            record_id=source_id,
            include=["evidence", "source_versions", "reviews", "refresh"],
            depth=1,
            access=access,
        )
        if result.kind != "source":
            raise ValueError("RECORD_NOT_FOUND: The source was not found.")
        versions = self._source_versions(
            self._list_of_dicts(result.includes.get("source_versions"))
        )
        version_by_id = {item.id: item for item in versions}
        return SourceDetailView(
            id=result.id,
            title=result.title,
            locator=result.url,
            source_type=self._optional_string(result.record.get("source_type")),
            review_state=result.review_state or "unreviewed",
            conflict_state=result.conflict_state or "none",
            trust_tier=self._optional_string(result.record.get("trust_tier")),
            versions=versions,
            evidence=[
                self._evidence(item, version_by_id)
                for item in self._list_of_dicts(result.includes.get("evidence"))
            ],
            reviews=self._list_of_dicts(result.includes.get("reviews")),
            refresh=self._list_of_dicts(result.includes.get("refresh")),
            can_review=can_review,
        )

    def source_version_detail(
        self,
        version_id: str,
        *,
        access: ReadAccess,
        can_review: bool,
    ) -> SourceVersionDetailView:
        result = self.fetch.get(
            record_id=version_id,
            include=["evidence", "source_versions", "reviews", "refresh"],
            depth=1,
            access=access,
        )
        if result.kind != "source_version":
            raise ValueError("SOURCE_VERSION_NOT_FOUND: The source version was not found.")
        versions = self._source_versions(
            self._list_of_dicts(result.includes.get("source_versions"))
        )
        if not versions:
            raise ValueError("SOURCE_VERSION_NOT_FOUND: The source version was not found.")
        version_by_id = {item.id: item for item in versions}
        return SourceVersionDetailView(
            source_title=result.title,
            version=versions[0],
            evidence=[
                self._evidence(item, version_by_id)
                for item in self._list_of_dicts(result.includes.get("evidence"))
            ],
            reviews=self._list_of_dicts(result.includes.get("reviews")),
            refresh=self._list_of_dicts(result.includes.get("refresh")),
            can_review=can_review,
        )

    def review_inbox(self, *, access: ReadAccess) -> ReviewInboxView:
        candidates = self.retrieval.list_candidates(
            kinds=["claim", "evidence", "source", "source_version", "report"],
            access=access,
            max_per_kind=200,
        )
        items: list[InboxItemView] = []
        for candidate in candidates:
            reasons, severity = self._review_reasons(candidate)
            if not reasons:
                continue
            items.append(
                InboxItemView(
                    id=candidate.id,
                    kind=candidate.kind,
                    title=candidate.title,
                    href=self.record_href(candidate.kind, candidate.id),
                    reasons=reasons,
                    badges=self._badges(
                        kind=candidate.kind,
                        review_state=candidate.review_state,
                        conflict_state=candidate.conflict_state,
                        freshness=candidate.freshness,
                    ),
                    evidence_count=candidate.evidence_count,
                    updated_at=candidate.updated_at,
                    severity=severity,
                )
            )
        items.sort(
            key=lambda item: (
                item.severity,
                item.evidence_count,
                item.updated_at or "",
                item.id,
            ),
            reverse=True,
        )
        return ReviewInboxView(items=items[:200])

    def refresh_queue(self, *, access: ReadAccess) -> RefreshQueueView:
        rows = self.retrieval.list_refresh_queue(access=access)
        return RefreshQueueView(
            items=[
                RefreshItemView(
                    **item,
                    href=self.record_href(
                        str(item["entity_kind"]), str(item["entity_id"])
                    ),
                )
                for item in rows
            ]
        )

    def deposit_receipt(
        self,
        key: str,
        *,
        namespace_kind: str = "user",
        namespace_id: str,
    ) -> DepositReceiptView:
        raw = self.retrieval.get_deposit_receipt(
            key,
            namespace_kind=namespace_kind,
            namespace_id=namespace_id,
        )
        if raw is None:
            raise ValueError("RECORD_NOT_FOUND: The deposit receipt was not found.")
        receipt = ResearchDepositResult.model_validate(raw)
        return DepositReceiptView(
            key=key,
            receipt=receipt,
            review_required=receipt.committed,
        )

    def status(self) -> StatusView:
        schema_version, backlog, migration_state = self.retrieval.status_counts()
        database_health = "healthy"
        try:
            blob = SourceVersionService(
                self.database,
                FilesystemBlobStore(self.settings.data_dir / "blobs"),
            ).inspect_blob_health()
            blob_health = "healthy" if blob.healthy else "attention required"
            referenced = blob.referenced_objects
            missing = len(blob.missing_keys) + len(blob.corrupt_keys)
        except (OSError, RuntimeError, ValueError):
            blob_health = "unavailable"
            referenced = 0
            missing = 0
        database_path = (
            str(Path(self.settings.db_path).expanduser())
            if self.retrieval.database_type == "sqlite"
            else "managed by the Postgres deployment"
        )
        return StatusView(
            application_version=__version__,
            schema_version=schema_version,
            database_type=self.retrieval.database_type,
            database_health=database_health,
            blob_backend="filesystem",
            blob_health=blob_health,
            blob_referenced_objects=referenced,
            blob_missing_objects=missing,
            migration_state=migration_state,
            capture_mode="explicit",
            mcp_transports="Streamable HTTP and Deep Research read-only HTTP",
            legacy_tools="enabled" if legacy_mcp_tools_enabled() else "disabled",
            retrieval_profile="full-text plus relationship-aware ranking",
            embedding_status="disabled",
            refresh_pending=backlog["pending"],
            refresh_running=backlog["running"],
            refresh_failed=backlog["failed"],
            backup_age="not recorded",
            backup_integrity="run backup verification to establish status",
            data_path=str(self.settings.data_dir.expanduser()),
            database_path=database_path,
        )

    @staticmethod
    def record_href(kind: str, record_id: str) -> str:
        prefix = {
            "claim": "claims",
            "evidence": "evidence",
            "source": "sources",
            "source_version": "source-versions",
            "report": "reports",
            "question": "questions",
        }.get(kind)
        return f"/v2/{prefix}/{record_id}" if prefix else "/v2/search"

    @classmethod
    def _badges(
        cls,
        *,
        kind: str,
        review_state: str | None,
        conflict_state: str | None,
        freshness: str | None,
    ) -> list[BadgeView]:
        badges = [BadgeView(label=kind.replace("_", " "))]
        if review_state:
            badges.append(
                BadgeView(
                    label=review_state.replace("_", " "),
                    tone="positive" if review_state == "reviewed" else "warning",
                )
            )
        if conflict_state and conflict_state != "none":
            badges.append(
                BadgeView(label=conflict_state.replace("_", " "), tone="danger")
            )
        if freshness and freshness not in {"fresh", "unknown"}:
            badges.append(
                BadgeView(label=freshness.replace("_", " "), tone="warning")
            )
        return badges

    @staticmethod
    def _revision(item: dict[str, Any]) -> RevisionView:
        return RevisionView(
            id=str(item["id"]),
            revision_number=int(item["revision_number"]),
            title=str(item["title"]),
            statement=str(item["statement"]),
            status=str(item["status"]),
            confidence=float(item["confidence"]),
            created_at=str(item["created_at"]),
            supersedes_revision_id=(
                str(item["supersedes_revision_id"])
                if item.get("supersedes_revision_id")
                else None
            ),
        )

    @classmethod
    def _source_versions(
        cls, items: list[dict[str, Any]]
    ) -> list[SourceVersionView]:
        views: list[SourceVersionView] = []
        for index, item in enumerate(items):
            older = items[index + 1] if index + 1 < len(items) else None
            if older is None:
                change_label = "Initial observation"
                byte_delta = None
            elif older.get("content_sha256") == item.get("content_sha256"):
                change_label = "Content hash unchanged"
                byte_delta = cls._byte_delta(item, older)
            else:
                change_label = "Content changed"
                byte_delta = cls._byte_delta(item, older)
            views.append(
                SourceVersionView(
                    id=str(item["id"]),
                    source_id=str(item["source_id"]),
                    version_key=str(item["version_key"]),
                    version_kind=str(item["version_kind"]),
                    retrieved_at=str(item["retrieved_at"]),
                    published_at=cls._optional_string(item.get("published_at")),
                    content_sha256=str(item["content_sha256"]),
                    canonical_locator=str(item["canonical_locator"]),
                    media_type=cls._optional_string(item.get("media_type")),
                    byte_count=(
                        int(item["byte_count"])
                        if item.get("byte_count") is not None
                        else None
                    ),
                    parser_name=cls._optional_string(item.get("parser_name")),
                    parser_version=cls._optional_string(item.get("parser_version")),
                    repository=cls._optional_string(item.get("repository")),
                    commit_sha=cls._optional_string(item.get("commit_sha")),
                    blob_sha=cls._optional_string(item.get("blob_sha")),
                    path=cls._optional_string(item.get("path")),
                    snapshot_policy=cls._optional_string(
                        item.get("snapshot_policy")
                    ),
                    snapshot_available=bool(item.get("snapshot_available")),
                    review_state=str(item.get("review_state") or "unreviewed"),
                    trust_tier=cls._optional_string(item.get("trust_tier")),
                    conflict_state=str(item.get("conflict_state") or "none"),
                    change_label=change_label,
                    byte_delta=byte_delta,
                )
            )
        return views

    @classmethod
    def _evidence(
        cls,
        item: dict[str, Any],
        version_by_id: dict[str, SourceVersionView],
    ) -> EvidenceView:
        relationship = str(item.get("relationship") or "contextualizes")
        return EvidenceView(
            id=str(item["id"]),
            quote_text=str(item["quote_text"]),
            note=cls._optional_string(item.get("note")),
            relationship=relationship,
            rationale=cls._optional_string(item.get("rationale")),
            confidence=float(item.get("confidence") or 0),
            anchor_state=str(item.get("anchor_state") or "unverified"),
            review_state=str(item.get("review_state") or "unreviewed"),
            trust_tier=str(item.get("trust_tier") or "low"),
            source_id=str(item["source_id"]),
            source_title=str(item["source_title"]),
            source_version_id=str(item["source_version_id"]),
            source_version=version_by_id.get(str(item["source_version_id"])),
        )

    @staticmethod
    def _review_reasons(
        candidate: RetrievalCandidate,
    ) -> tuple[list[str], int]:
        reasons: list[str] = []
        severity = 0
        if candidate.conflict_state == "conflicted":
            reasons.append("Conflicting evidence needs review")
            severity = max(severity, 5)
        if candidate.freshness == "stale":
            reasons.append("Stale evidence or dependency")
            severity = max(severity, 5)
        elif candidate.freshness == "needs_refresh":
            reasons.append("Refresh is pending")
            severity = max(severity, 4)
        if candidate.review_state == "flagged":
            reasons.append("Flagged review state")
            severity = max(severity, 4)
        elif candidate.review_state == "unreviewed":
            reasons.append("Needs review")
            severity = max(severity, 3)
        return reasons, severity

    @staticmethod
    def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return str(value) if value is not None else None

    @staticmethod
    def _byte_delta(item: dict[str, Any], older: dict[str, Any]) -> int | None:
        current = item.get("byte_count")
        previous = older.get("byte_count")
        if current is None or previous is None:
            return None
        return int(current) - int(previous)
