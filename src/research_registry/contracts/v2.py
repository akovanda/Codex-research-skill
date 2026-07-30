from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

from .common import (
    ClaimRevisionStatus,
    ClosedModel,
    ConflictState,
    EvidenceRelationship,
    FreshnessState,
    GitObjectId,
    JsonObject20,
    JsonObject50,
    JsonObject100,
    Locator,
    NamespaceKind,
    NonEmptyString100,
    NonEmptyString200,
    RecordId,
    ReviewState,
    Sha256,
    SnapshotPolicy,
    TrustTier,
    Visibility,
)


def _ensure_unique(values: list[Any], field_name: str) -> list[Any]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must contain unique items")
    return values


def _validate_order(start: int | None, end: int | None, label: str) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError(f"{label} end must be greater than or equal to start")


def _remove_json_schema_default(schema: dict[str, Any]) -> None:
    """Represent optional-but-non-null packet properties exactly."""
    schema.pop("default", None)


class TextQuoteSelector(ClosedModel):
    type: Literal["text_quote"]
    exact: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    prefix: Annotated[str, StringConstraints(max_length=2_000)] | None = None
    suffix: Annotated[str, StringConstraints(max_length=2_000)] | None = None
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    deep_link: Annotated[str, StringConstraints(max_length=4_096)] | None = None

    @model_validator(mode="after")
    def validate_offsets(self) -> TextQuoteSelector:
        _validate_order(self.start, self.end, "text quote")
        return self


class LineRangeSelector(ClosedModel):
    type: Literal["line_range"]
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    exact: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    prefix: Annotated[str, StringConstraints(max_length=2_000)] | None = None
    suffix: Annotated[str, StringConstraints(max_length=2_000)] | None = None
    deep_link: Annotated[str, StringConstraints(max_length=4_096)] | None = None

    @model_validator(mode="after")
    def validate_lines(self) -> LineRangeSelector:
        _validate_order(self.start_line, self.end_line, "line range")
        return self


class GitLineRangeSelector(ClosedModel):
    type: Literal["git_line_range"]
    path: Annotated[str, StringConstraints(min_length=1, max_length=4_096)] = Field(
        json_schema_extra={
            "pattern": r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$)).+$",
        }
    )
    commit_sha: GitObjectId
    blob_sha: GitObjectId
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    exact: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    prefix: Annotated[str, StringConstraints(max_length=2_000)] | None = None
    suffix: Annotated[str, StringConstraints(max_length=2_000)] | None = None
    deep_link: Annotated[str, StringConstraints(max_length=4_096)] | None = None

    @field_validator("path")
    @classmethod
    def validate_posix_path(cls, value: str) -> str:
        if value.startswith("/") or "\\" in value or ".." in value.split("/"):
            raise ValueError("path must be a normalized relative POSIX path")
        return value

    @model_validator(mode="after")
    def validate_lines(self) -> GitLineRangeSelector:
        _validate_order(self.start_line, self.end_line, "Git line range")
        return self


class PageRangeSelector(ClosedModel):
    type: Literal["page_range"]
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    exact: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    deep_link: Annotated[str, StringConstraints(max_length=4_096)] | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> PageRangeSelector:
        _validate_order(self.start_page, self.end_page, "page range")
        _validate_order(self.start, self.end, "page character range")
        return self


class CharRangeSelector(ClosedModel):
    type: Literal["char_range"]
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    exact: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    deep_link: Annotated[str, StringConstraints(max_length=4_096)] | None = None

    @model_validator(mode="after")
    def validate_offsets(self) -> CharRangeSelector:
        _validate_order(self.start, self.end, "character range")
        return self


class JsonPointerSelector(ClosedModel):
    type: Literal["json_pointer"]
    pointer: Annotated[str, StringConstraints(max_length=4_096)] = Field(
        json_schema_extra={"pattern": r"^(?:|/.*)$"}
    )
    exact: Annotated[str, StringConstraints(max_length=20_000)] | None = None
    deep_link: Annotated[str, StringConstraints(max_length=4_096)] | None = None

    @field_validator("pointer")
    @classmethod
    def validate_pointer(cls, value: str) -> str:
        if value and not value.startswith("/"):
            raise ValueError("JSON Pointer must be empty or start with '/'")
        return value


class DomTextSelector(ClosedModel):
    type: Literal["dom_text"]
    css_selector: Annotated[str, StringConstraints(max_length=2_000)] | None = None
    exact: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    prefix: Annotated[str, StringConstraints(max_length=2_000)] | None = None
    suffix: Annotated[str, StringConstraints(max_length=2_000)] | None = None
    deep_link: Annotated[str, StringConstraints(max_length=4_096)] | None = None


SourceSelectorV2 = Annotated[
    TextQuoteSelector
    | LineRangeSelector
    | GitLineRangeSelector
    | PageRangeSelector
    | CharRangeSelector
    | JsonPointerSelector
    | DomTextSelector,
    Field(discriminator="type"),
]


class NamespaceSelector(ClosedModel):
    kind: NamespaceKind
    id: NonEmptyString200


class DepositInquiry(ClosedModel):
    client_ref: NonEmptyString100
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=50_000)]
    topic_label: Annotated[str, StringConstraints(max_length=500)] | None = None
    focus: JsonObject20 | None = None


class RunProvenance(ClosedModel):
    actor_type: (
        Literal["human", "agent", "system", "import", "migration"]
        | SkipJsonSchema[None]
    ) = Field(default=None, json_schema_extra=_remove_json_schema_default)
    actor_id: Annotated[str, StringConstraints(max_length=200)] | None = None
    client_name: Annotated[str, StringConstraints(max_length=200)] | None = None
    client_version: Annotated[str, StringConstraints(max_length=200)] | None = None
    provider: Annotated[str, StringConstraints(max_length=200)] | None = None
    model: Annotated[str, StringConstraints(max_length=300)] | None = None
    model_version: Annotated[str, StringConstraints(max_length=200)] | None = None
    plugin_version: Annotated[str, StringConstraints(max_length=200)] | None = None
    prompt_sha256: Sha256 | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null_actor_type(cls, value: Any) -> Any:
        if isinstance(value, dict) and "actor_type" in value and value["actor_type"] is None:
            raise ValueError("actor_type cannot be null when provided")
        return value


class DepositRun(ClosedModel):
    client_ref: NonEmptyString100
    mode: Literal["research", "reuse", "refresh", "import", "migration", "manual"]
    started_at: datetime | None = Field(default=None, strict=False)
    finished_at: datetime | None = Field(default=None, strict=False)
    notes: Annotated[str, StringConstraints(max_length=10_000)] | None = None
    provenance: RunProvenance

    @model_validator(mode="after")
    def validate_times(self) -> DepositRun:
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("finished_at must be at or after started_at")
        return self


class SourceIdentity(ClosedModel):
    locator: Locator
    title: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    source_type: Literal[
        "webpage",
        "official-docs",
        "paper",
        "report",
        "dataset",
        "code",
        "test",
        "documentation",
        "script",
        "local_file",
        "git_file",
        "pdf",
        "api",
        "note",
        "other",
    ]
    site_name: Annotated[str, StringConstraints(max_length=300)] | None = None
    author: Annotated[str, StringConstraints(max_length=500)] | None = None
    canonical_key: Annotated[str, StringConstraints(max_length=500)] | None = None
    metadata: JsonObject50 = Field(default_factory=dict)


class SourceSnapshot(ClosedModel):
    policy: SnapshotPolicy
    text: Annotated[str, StringConstraints(max_length=5_000_000)] | None = None
    media_type: Annotated[str, StringConstraints(max_length=200)] | None = None
    byte_count: int | None = Field(default=None, ge=0, le=50_000_000)


class RepositoryVersion(ClosedModel):
    repository_id: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    remote_fingerprint: Annotated[str, StringConstraints(max_length=500)] | None = None
    commit_sha: GitObjectId
    blob_sha: GitObjectId
    path: Annotated[str, StringConstraints(min_length=1, max_length=4_096)] = Field(
        json_schema_extra={
            "pattern": r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$)).+$",
        }
    )
    file_mode: Annotated[str, StringConstraints(max_length=20)] | None = None

    @field_validator("path")
    @classmethod
    def validate_posix_path(cls, value: str) -> str:
        if value.startswith("/") or "\\" in value or ".." in value.split("/"):
            raise ValueError("path must be a normalized relative POSIX path")
        return value


class SourceVersion(ClosedModel):
    version_key: Annotated[str, StringConstraints(max_length=500)] | None = None
    version_kind: Literal["web", "doi", "file", "git_blob", "pdf", "api", "note", "migration"]
    retrieved_at: datetime = Field(strict=False)
    published_at: datetime | None = Field(default=None, strict=False)
    content_sha256: Sha256
    canonical_locator: Locator
    snapshot: SourceSnapshot
    parser_name: Annotated[str, StringConstraints(max_length=200)] | None = None
    parser_version: Annotated[str, StringConstraints(max_length=100)] | None = None
    repository: RepositoryVersion | None = None
    metadata: JsonObject100 = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_git_provenance(self) -> SourceVersion:
        if self.version_kind == "git_blob" and self.repository is None:
            raise ValueError("git_blob source versions require repository provenance")
        return self


class DepositSource(ClosedModel):
    client_ref: NonEmptyString100
    identity: SourceIdentity
    version: SourceVersion


class IdReference(ClosedModel):
    id: RecordId


class ClientReference(ClosedModel):
    ref: NonEmptyString100


LocalReference = IdReference | ClientReference


class DepositEvidence(ClosedModel):
    client_ref: NonEmptyString100
    source_version: LocalReference
    quote_text: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    selector: SourceSelectorV2
    note: Annotated[str, StringConstraints(max_length=10_000)] | None = None
    confidence: float = Field(default=0.75, ge=0, le=1)
    review_state: ReviewState = "unreviewed"
    trust_tier: TrustTier = "medium"
    metadata: JsonObject50 = Field(default_factory=dict)


class ClaimEvidenceLink(ClosedModel):
    evidence: LocalReference
    relationship: EvidenceRelationship
    rationale: Annotated[str, StringConstraints(max_length=10_000)] | None = None
    weight: float = Field(default=1.0, ge=0, le=1)


class DepositClaim(ClosedModel):
    client_ref: NonEmptyString100
    claim_id: Annotated[str, StringConstraints(max_length=200)] | None = None
    expected_revision_id: Annotated[str, StringConstraints(max_length=200)] | None = None
    canonical_key: Annotated[str, StringConstraints(max_length=500)] | None = None
    title: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    statement: Annotated[str, StringConstraints(min_length=1, max_length=50_000)]
    status: ClaimRevisionStatus
    confidence: float = Field(ge=0, le=1)
    scope: JsonObject50 | None = None
    evidence: list[ClaimEvidenceLink] = Field(max_length=100)
    metadata: JsonObject50 = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_supported_evidence(self) -> DepositClaim:
        if self.status == "supported" and not any(
            link.relationship in {"supports", "qualifies"} for link in self.evidence
        ):
            raise ValueError("supported claims require supports or qualifies evidence")
        return self


class DepositReport(ClosedModel):
    client_ref: NonEmptyString100
    title: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    summary_md: Annotated[str, StringConstraints(min_length=1, max_length=200_000)]
    report_kind: Annotated[str, StringConstraints(max_length=100)] = "guidance"
    claims: list[LocalReference] = Field(max_length=100)
    guidance: JsonObject50 | None = None
    metadata: JsonObject50 = Field(default_factory=dict)


class ResearchDepositRequest(ClosedModel):
    protocol: Literal["research-deposit/v2"]
    idempotency_key: NonEmptyString200
    validate_only: bool = False
    visibility: Visibility = "private"
    namespace: NamespaceSelector | None = None
    inquiry: DepositInquiry | None = None
    run: DepositRun
    sources: list[DepositSource] = Field(max_length=50)
    evidence: list[DepositEvidence] = Field(max_length=200)
    claims: list[DepositClaim] = Field(max_length=100)
    report: DepositReport | None = None
    metadata: JsonObject50 = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_local_references(self) -> ResearchDepositRequest:
        groups: tuple[list[Any], ...] = (
            [self.inquiry] if self.inquiry is not None else [],
            [self.run],
            self.sources,
            self.evidence,
            self.claims,
            [self.report] if self.report is not None else [],
        )
        refs = [item.client_ref for group in groups for item in group]
        _ensure_unique(refs, "client_ref")

        source_refs = {source.client_ref for source in self.sources}
        for evidence in self.evidence:
            if (
                isinstance(evidence.source_version, ClientReference)
                and evidence.source_version.ref not in source_refs
            ):
                raise ValueError(
                    f"source version reference not found: {evidence.source_version.ref}"
                )

        evidence_refs = {evidence.client_ref for evidence in self.evidence}
        for claim in self.claims:
            for link in claim.evidence:
                if (
                    isinstance(link.evidence, ClientReference)
                    and link.evidence.ref not in evidence_refs
                ):
                    raise ValueError(
                        f"evidence reference not found: {link.evidence.ref}"
                    )

        claim_refs = {claim.client_ref for claim in self.claims}
        if self.report is not None:
            for claim in self.report.claims:
                if isinstance(claim, ClientReference) and claim.ref not in claim_refs:
                    raise ValueError(f"claim reference not found: {claim.ref}")
        return self


class DepositRecordIds(ClosedModel):
    question_id: RecordId | None = None
    run_id: RecordId | None = None
    source_ids: dict[NonEmptyString100, RecordId] = Field(
        default_factory=dict,
        max_length=50,
    )
    source_version_ids: dict[NonEmptyString100, RecordId] = Field(
        default_factory=dict,
        max_length=50,
    )
    evidence_ids: dict[NonEmptyString100, RecordId] = Field(
        default_factory=dict,
        max_length=200,
    )
    claim_ids: dict[NonEmptyString100, RecordId] = Field(
        default_factory=dict,
        max_length=100,
    )
    claim_revision_ids: dict[NonEmptyString100, RecordId] = Field(
        default_factory=dict,
        max_length=100,
    )
    report_id: RecordId | None = None


class ResearchDepositResult(ClosedModel):
    protocol: Literal["research-deposit-result/v2"]
    status: Literal["validated", "committed"]
    committed: bool
    idempotent_replay: bool
    request_sha256: Sha256 | SkipJsonSchema[None] = Field(
        default=None,
        json_schema_extra=_remove_json_schema_default,
    )
    records: DepositRecordIds
    warnings: list[Annotated[str, StringConstraints(max_length=1_000)]] = Field(
        max_length=100
    )

    @field_validator("request_sha256", mode="before")
    @classmethod
    def reject_explicit_null_request_hash(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("request_sha256 cannot be null when provided")
        return value

    @model_validator(mode="after")
    def validate_status(self) -> ResearchDepositResult:
        if self.status == "validated" and self.committed:
            raise ValueError("validated results cannot be committed")
        if self.status == "committed" and not self.committed:
            raise ValueError("committed results must set committed=true")
        return self


SearchKind = Literal[
    "question",
    "source",
    "source_version",
    "evidence",
    "claim",
    "report",
]


class SearchScope(ClosedModel):
    repository: Annotated[str, StringConstraints(max_length=500)] | None = None
    paths: list[Annotated[str, StringConstraints(max_length=4_096)]] = Field(
        default_factory=list,
        max_length=50,
    )
    topic_ids: list[Annotated[str, StringConstraints(max_length=200)]] = Field(
        default_factory=list,
        max_length=50,
    )
    source_types: list[Annotated[str, StringConstraints(max_length=100)]] = Field(
        default_factory=list,
        max_length=50,
    )
    created_after: datetime | None = Field(default=None, strict=False)
    created_before: datetime | None = Field(default=None, strict=False)

    @model_validator(mode="after")
    def validate_time_window(self) -> SearchScope:
        if (
            self.created_after is not None
            and self.created_before is not None
            and self.created_before < self.created_after
        ):
            raise ValueError("created_before must be at or after created_after")
        return self


class ResearchSearchRequest(ClosedModel):
    protocol: Literal["research-search/v2"]
    query: Annotated[str, StringConstraints(min_length=1, max_length=10_000)]
    kinds: list[SearchKind] = Field(default_factory=list, max_length=8)
    scope: SearchScope | None = None
    review_states: list[ReviewState] = Field(default_factory=list, max_length=3)
    conflict_states: list[ConflictState] = Field(default_factory=list, max_length=3)
    freshness: list[FreshnessState] = Field(default_factory=list, max_length=4)
    include_private: bool = True
    include_rejected: bool = False
    limit: int = Field(default=10, ge=1, le=100)
    cursor: Annotated[str, StringConstraints(max_length=2_000)] | None = None
    explain: bool = True

    @field_validator("kinds", "review_states", "conflict_states", "freshness")
    @classmethod
    def validate_unique_filters(cls, value: list[Any], info: Any) -> list[Any]:
        return _ensure_unique(value, info.field_name)


class SearchHitV2(ClosedModel):
    id: RecordId
    kind: SearchKind
    title: Annotated[str, StringConstraints(max_length=500)]
    summary: Annotated[str, StringConstraints(max_length=4_000)]
    score: float = Field(ge=0, le=1)
    score_components: dict[
        Annotated[str, StringConstraints(max_length=100)],
        float,
    ] = Field(default_factory=dict, max_length=20)
    matched_by: list[Annotated[str, StringConstraints(max_length=500)]] = Field(
        default_factory=list,
        max_length=50,
    )
    review_state: ReviewState | None
    conflict_state: ConflictState | None
    freshness: FreshnessState | None
    evidence_count: int = Field(ge=0)
    updated_at: datetime | None = Field(default=None, strict=False)
    url: Annotated[str, StringConstraints(max_length=8_192)] | None = None


class ResearchSearchResponse(ClosedModel):
    protocol: Literal["research-search-result/v2"]
    query: Annotated[str, StringConstraints(max_length=10_000)]
    hits: list[SearchHitV2] = Field(max_length=100)
    next_cursor: Annotated[str, StringConstraints(max_length=2_000)] | None


GetInclude = Literal[
    "current_revision",
    "revision_history",
    "evidence",
    "source_versions",
    "reviews",
    "reports",
    "refresh",
]


class ResearchGetRequest(ClosedModel):
    protocol: Literal["research-get/v2"]
    id: RecordId
    include: list[GetInclude] = Field(default_factory=list, max_length=10)
    depth: int = Field(default=1, ge=0, le=2)
    include_private: bool = True

    @field_validator("include")
    @classmethod
    def validate_unique_include(cls, value: list[GetInclude]) -> list[GetInclude]:
        return _ensure_unique(value, "include")


class ReviewEntity(ClosedModel):
    kind: Literal[
        "claim_revision",
        "evidence",
        "source_version",
        "report",
        "refresh_item",
    ]
    id: RecordId


class ReviewNewRevision(ClosedModel):
    title: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    statement: Annotated[str, StringConstraints(min_length=1, max_length=50_000)]
    status: ClaimRevisionStatus
    confidence: float = Field(ge=0, le=1)


class ResearchReviewRequest(ClosedModel):
    protocol: Literal["research-review/v2"]
    idempotency_key: NonEmptyString200
    entity: ReviewEntity
    action: Literal[
        "approve",
        "contest",
        "reject",
        "supersede",
        "request_refresh",
        "dismiss_refresh",
    ]
    expected_revision_id: Annotated[str, StringConstraints(max_length=200)] | None = None
    expected_state: Annotated[str, StringConstraints(max_length=100)] | None = None
    note: Annotated[str, StringConstraints(max_length=20_000)] | None = None
    new_revision: ReviewNewRevision | None = None


class ClaimCurrentState(ClosedModel):
    claim_id: RecordId
    current_revision_id: RecordId
    revision_number: int = Field(ge=1)
    status: ClaimRevisionStatus
    review_state: ReviewState
    conflict_state: ConflictState
    freshness: FreshnessState


class ResearchReviewResult(ClosedModel):
    protocol: Literal["research-review-result/v2"]
    status: Literal["applied"]
    idempotent_replay: bool
    event_id: RecordId
    entity: ReviewEntity
    current_revision_id: RecordId | None = None
    revision_created: bool
    current_state: ClaimCurrentState | None = None
    refresh_item_ids: list[RecordId] = Field(default_factory=list, max_length=200)


class RefreshEntity(ClosedModel):
    kind: Literal[
        "source",
        "source_version",
        "evidence",
        "claim",
        "report",
        "refresh_item",
    ]
    id: RecordId


class ResearchRefreshRequest(ClosedModel):
    protocol: Literal["research-refresh/v2"]
    mode: Literal["inspect", "enqueue", "verify", "capture"]
    idempotency_key: Annotated[str, StringConstraints(max_length=200)] | None = None
    entities: list[RefreshEntity] = Field(min_length=1, max_length=100)
    snapshot_policy: SnapshotPolicy | None = None
    priority: float = Field(default=0.5, ge=0, le=1)


class RefreshPlanItem(ClosedModel):
    entity: RefreshEntity
    reason: Literal[
        "expired",
        "source_changed",
        "anchor_missing",
        "conflict",
        "manual",
    ]
    refresh_item_id: RecordId | None = None
    queue_status: Literal[
        "not_enqueued",
        "pending",
        "running",
        "resolved",
        "dismissed",
        "failed",
    ]
    created: bool


class ResearchRefreshResult(ClosedModel):
    protocol: Literal["research-refresh-result/v2"]
    status: Literal["inspected", "enqueued"]
    committed: bool
    idempotent_replay: bool
    items: list[RefreshPlanItem] = Field(max_length=500)


class RefreshBacklog(ClosedModel):
    pending: int = Field(default=0, ge=0)
    running: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)


class ResearchStatusResponse(ClosedModel):
    protocol: Literal["research-status-result/v2"]
    server_version: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    schema_version: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    namespace: NamespaceSelector
    database_type: Literal["sqlite", "postgres"]
    capture_mode: Literal["explicit", "suggest", "automatic"]
    capabilities: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = (
        Field(max_length=50)
    )
    legacy_tools_enabled: bool
    embedding_status: Literal["disabled", "available", "unavailable"]
    refresh_backlog: RefreshBacklog
    migration_state: Literal["current", "pending", "failed"]

    @field_validator("capabilities")
    @classmethod
    def validate_unique_capabilities(cls, value: list[str]) -> list[str]:
        return _ensure_unique(value, "capabilities")


ErrorCode = Literal[
    "INVALID_PROTOCOL_VERSION",
    "INVALID_REQUEST",
    "UNKNOWN_FIELD",
    "VALUE_OUT_OF_RANGE",
    "INVALID_SELECTOR",
    "INVALID_HASH",
    "INVALID_RELATIONSHIP",
    "AUTH_REQUIRED",
    "INSUFFICIENT_SCOPE",
    "NAMESPACE_ACCESS_DENIED",
    "CAPTURE_POLICY_DENIED",
    "PUBLISH_REQUIRES_EXPLICIT_ACTION",
    "SNAPSHOT_POLICY_DENIED",
    "RECORD_NOT_FOUND",
    "DEPOSIT_REFERENCE_NOT_FOUND",
    "SOURCE_VERSION_NOT_FOUND",
    "CURRENT_REVISION_NOT_FOUND",
    "IDEMPOTENCY_CONFLICT",
    "EXPECTED_REVISION_MISMATCH",
    "EXPECTED_STATE_MISMATCH",
    "DUPLICATE_CLIENT_REFERENCE",
    "DUPLICATE_PENDING_REFRESH",
    "MIGRATION_LOCKED",
    "EVIDENCE_UNRESOLVED",
    "EVIDENCE_AMBIGUOUS",
    "SUPPORTED_CLAIM_REQUIRES_EVIDENCE",
    "INVALID_CLAIM_TRANSITION",
    "IMMUTABLE_RECORD",
    "SOURCE_VERSION_HASH_MISMATCH",
    "CONTENT_OBJECT_MISSING",
    "MIGRATION_CHECKSUM_MISMATCH",
    "MIGRATION_PLAN_INVALID",
    "BACKFILL_INCOMPLETE",
    "LEGACY_DATA_WARNING",
    "BACKUP_VERIFICATION_FAILED",
    "BLOB_FINALIZATION_FAILED",
    "DATABASE_INTEGRITY_ERROR",
    "URL_SCHEME_DENIED",
    "URL_ADDRESS_DENIED",
    "URL_REDIRECT_DENIED",
    "FETCH_TIMEOUT",
    "FETCH_TOO_LARGE",
    "MEDIA_TYPE_DENIED",
    "PARSER_FAILED",
    "GIT_OBJECT_NOT_FOUND",
    "GIT_CREDENTIAL_LOCATOR_REJECTED",
    "INVALID_CURSOR",
    "QUERY_TOO_LARGE",
    "RETRIEVAL_INDEX_UNAVAILABLE",
    "SEMANTIC_PROVIDER_DISABLED",
]


class CompactError(ClosedModel):
    code: ErrorCode
    message: Annotated[str, StringConstraints(min_length=1, max_length=1_000)]
    retryable: bool
    details: dict[
        Annotated[str, StringConstraints(min_length=1, max_length=100)],
        Annotated[str, StringConstraints(max_length=500)] | int | float | bool | None,
    ] = Field(default_factory=dict, max_length=20)

    @field_validator("message")
    @classmethod
    def validate_safe_compact_message(cls, value: str) -> str:
        if "\n" in value or "traceback" in value.casefold():
            raise ValueError("error messages must be compact and contain no stack trace")
        return value


class ResearchErrorResponse(ClosedModel):
    error: CompactError
    request_id: Annotated[str, StringConstraints(min_length=1, max_length=200)]


# Concise aliases for adapters that already carry the v2 module name.
DepositRequest = ResearchDepositRequest
DepositResult = ResearchDepositResult
ErrorResponse = ResearchErrorResponse
GetRequest = ResearchGetRequest
RefreshRequest = ResearchRefreshRequest
RefreshResult = ResearchRefreshResult
ReviewRequest = ResearchReviewRequest
ReviewResult = ResearchReviewResult
SearchRequest = ResearchSearchRequest
SearchResponse = ResearchSearchResponse
StatusResponse = ResearchStatusResponse

__all__ = [
    "CharRangeSelector",
    "ClientReference",
    "CompactError",
    "DepositClaim",
    "DepositEvidence",
    "DepositInquiry",
    "DepositRecordIds",
    "DepositReport",
    "DepositRequest",
    "DepositResult",
    "DepositRun",
    "DepositSource",
    "DomTextSelector",
    "ErrorResponse",
    "GetRequest",
    "GitLineRangeSelector",
    "IdReference",
    "JsonPointerSelector",
    "LineRangeSelector",
    "RefreshRequest",
    "RefreshResult",
    "ResearchRefreshResult",
    "ResearchDepositRequest",
    "ResearchDepositResult",
    "ResearchErrorResponse",
    "ResearchGetRequest",
    "ResearchRefreshRequest",
    "ResearchReviewRequest",
    "ResearchReviewResult",
    "ResearchSearchRequest",
    "ResearchSearchResponse",
    "ResearchStatusResponse",
    "ReviewRequest",
    "ReviewResult",
    "SearchRequest",
    "SearchResponse",
    "SourceSelectorV2",
    "StatusResponse",
    "TextQuoteSelector",
]
