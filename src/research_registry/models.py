from __future__ import annotations

from datetime import datetime
import re
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

Visibility = Literal["private", "public"]
AuthorType = Literal["human", "agent"]
RecordKind = Literal["source", "question", "excerpt", "claim", "report", "annotation", "finding"]
NamespaceKind = Literal["user", "org"]
PublicIndexState = Literal["private", "namespace_only", "included", "suppressed"]
ApiKeyScope = Literal["ingest", "publish", "read_private", "admin"]
ApiKeyStatus = Literal["active", "revoked", "blocked"]
QuestionStatus = Literal["open", "answered", "insufficient_evidence"]
FollowUpStatus = Literal["open", "ready", "blocked", "done"]
SessionMode = Literal["reuse", "live_research", "synthesis", "insufficient_evidence"]
SessionStatus = Literal["completed", "insufficient_evidence"]
ClaimStatus = Literal["supported", "partial", "conflicted", "insufficient_evidence"]
FreshnessState = Literal["fresh", "needs_refresh"]
ReportKind = Literal["guidance", "legacy_answer"]
ReviewState = Literal["unreviewed", "reviewed", "flagged"]
TrustTier = Literal["low", "medium", "high"]
ConflictState = Literal["none", "conflicted"]


def slugify(text: str) -> str:
    collapsed = re.sub(r"[^a-z0-9]+", "-", text.strip().lower())
    return collapsed.strip("-") or "research"


class SourceSelector(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = "TextQuoteSelector"
    deep_link: str | None = None
    exact: str | None = None
    prefix: str | None = None
    suffix: str | None = None
    start: int | None = None
    end: int | None = None
    start_line: int | None = None
    end_line: int | None = None


class FocusTuple(BaseModel):
    domain: str | None = None
    object: str | None = None
    concern: str | None = None
    context: str | None = None
    constraint: str | None = None
    label: str | None = None
    slug: str | None = None

    @model_validator(mode="after")
    def populate_derived_fields(self) -> "FocusTuple":
        if not self.label:
            parts = [self.domain, self.object, self.concern, self.context, self.constraint]
            self.label = " | ".join(part.strip() for part in parts if part and part.strip()) or "research"
        if not self.slug:
            self.slug = slugify(self.label)
        return self

    def parts(self) -> list[str]:
        return [part for part in [self.domain, self.object, self.concern, self.context, self.constraint] if part]


class GuidancePayload(BaseModel):
    current_guidance: list[str] = Field(default_factory=list)
    evidence_now: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    needs: list[str] = Field(default_factory=list)
    wants: list[str] = Field(default_factory=list)
    follow_up_question_ids: list[str] = Field(default_factory=list)


class TopicCreate(BaseModel):
    focus: FocusTuple
    label: str | None = None
    slug: str | None = None
    parent_topic_id: str | None = None
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    dedupe_key: str | None = None

    @model_validator(mode="after")
    def sync_label_and_slug(self) -> "TopicCreate":
        if not self.label:
            self.label = self.focus.label
        if not self.slug:
            self.slug = self.focus.slug
        return self


class TopicRecord(TopicCreate):
    id: str
    created_at: datetime


class QuestionCreate(BaseModel):
    prompt: str = Field(validation_alias=AliasChoices("prompt", "question"))
    focus: FocusTuple | None = None
    topic_id: str | None = None
    parent_question_id: str | None = None
    generated_by_session_id: str | None = None
    generation_reason: str | None = None
    priority_score: float = 0.0
    status: QuestionStatus = "open"
    follow_up_status: FollowUpStatus = "open"
    visibility: Visibility = "private"
    author_type: AuthorType = "agent"
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    dedupe_key: str | None = None

    @model_validator(mode="after")
    def validate_focus_reference(self) -> "QuestionCreate":
        if not self.focus and not self.topic_id:
            raise ValueError("either focus or topic_id must be provided")
        return self


class QuestionRecord(QuestionCreate):
    id: str
    normalized_prompt: str
    created_at: datetime
    latest_session_id: str | None = None
    latest_report_id: str | None = None
    latest_session_freshness_state: FreshnessState | None = None
    latest_session_expires_at: datetime | None = None
    latest_session_is_stale: bool = False
    actor_user_id: str | None = None
    actor_org_id: str | None = None
    api_key_id: str | None = None
    public_namespace_slug: str | None = None
    public_index_state: PublicIndexState = "private"

    @property
    def question(self) -> str:
        return self.prompt


class ResearchSessionCreate(BaseModel):
    question_id: str
    prompt: str | None = None
    model_name: str
    model_version: str
    mode: SessionMode
    ttl_days: int = Field(default=30, ge=1, le=3650)
    refresh_of_session_id: str | None = None
    source_signals: list[str] = Field(default_factory=list)
    notes: str | None = None
    visibility: Visibility = "private"
    author_type: AuthorType = "agent"
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    dedupe_key: str | None = None


class ResearchSessionRecord(ResearchSessionCreate):
    id: str
    status: SessionStatus
    created_at: datetime
    started_at: datetime
    finished_at: datetime
    expires_at: datetime | None = None
    freshness_state: FreshnessState = "fresh"
    is_stale: bool = False
    claim_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    report_ids: list[str] = Field(default_factory=list)
    actor_user_id: str | None = None
    actor_org_id: str | None = None
    api_key_id: str | None = None
    public_namespace_slug: str | None = None
    public_index_state: PublicIndexState = "private"

    @property
    def question(self) -> str:
        return self.prompt or ""


class SourceCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    locator: str = Field(validation_alias=AliasChoices("locator", "canonical_url"))
    title: str
    source_type: str = "webpage"
    site_name: str | None = None
    published_at: datetime | None = None
    accessed_at: datetime | None = None
    author: str | None = None
    snippet: str | None = None
    content_sha256: str | None = None
    snapshot_url: str | None = None
    snapshot_required: bool = False
    snapshot_present: bool = False
    last_verified_at: datetime | None = None
    review_state: ReviewState = "unreviewed"
    trust_tier: TrustTier = "low"
    conflict_state: ConflictState = "none"
    refresh_due_at: datetime | None = None
    visibility: Visibility = "private"
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    dedupe_key: str | None = None

    @property
    def canonical_url(self) -> str:
        return self.locator


class SourceRecord(SourceCreate):
    id: str
    created_at: datetime
    actor_user_id: str | None = None
    actor_org_id: str | None = None
    api_key_id: str | None = None
    public_namespace_slug: str | None = None
    public_index_state: PublicIndexState = "private"


class ExcerptCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_id: str | None = None
    source: SourceCreate | None = None
    question_id: str
    session_id: str | None = None
    topic_id: str | None = None
    focal_label: str = Field(validation_alias=AliasChoices("focal_label", "subject"))
    note: str
    selector: SourceSelector
    quote_text: str
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    review_state: ReviewState = "unreviewed"
    trust_tier: TrustTier = "low"
    conflict_state: ConflictState = "none"
    refresh_due_at: datetime | None = None
    visibility: Visibility = "private"
    author_type: AuthorType = "agent"
    model_name: str | None = None
    model_version: str | None = None
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    dedupe_key: str | None = None

    @model_validator(mode="after")
    def validate_source_reference(self) -> "ExcerptCreate":
        if not self.source_id and not self.source:
            raise ValueError("either source_id or source must be provided")
        return self


class ExcerptRecord(ExcerptCreate):
    id: str
    created_at: datetime
    human_reviewed: bool = False
    freshness_state: FreshnessState | None = None
    expires_at: datetime | None = None
    is_stale: bool = False
    actor_user_id: str | None = None
    actor_org_id: str | None = None
    api_key_id: str | None = None
    public_namespace_slug: str | None = None
    public_index_state: PublicIndexState = "private"

    @property
    def subject(self) -> str:
        return self.focal_label


class ClaimCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    question_id: str
    session_id: str | None = None
    topic_id: str | None = None
    title: str
    focal_label: str = Field(validation_alias=AliasChoices("focal_label", "subject"))
    statement: str = Field(validation_alias=AliasChoices("statement", "claim"))
    excerpt_ids: list[str] = Field(min_length=1, validation_alias=AliasChoices("excerpt_ids", "annotation_ids"))
    status: ClaimStatus = "supported"
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    review_state: ReviewState = "unreviewed"
    trust_tier: TrustTier = "medium"
    conflict_state: ConflictState = "none"
    refresh_due_at: datetime | None = None
    visibility: Visibility = "private"
    author_type: AuthorType = "agent"
    model_name: str | None = None
    model_version: str | None = None
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    dedupe_key: str | None = None


class ClaimRecord(ClaimCreate):
    id: str
    created_at: datetime
    human_reviewed: bool = False
    freshness_state: FreshnessState | None = None
    expires_at: datetime | None = None
    is_stale: bool = False
    actor_user_id: str | None = None
    actor_org_id: str | None = None
    api_key_id: str | None = None
    public_namespace_slug: str | None = None
    public_index_state: PublicIndexState = "private"

    @property
    def subject(self) -> str:
        return self.focal_label

    @property
    def claim(self) -> str:
        return self.statement

    @property
    def annotation_ids(self) -> list[str]:
        return self.excerpt_ids


class ReportCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    question_id: str
    session_id: str | None = None
    title: str
    focal_label: str = Field(validation_alias=AliasChoices("focal_label", "subject"))
    summary_md: str
    report_kind: ReportKind = "guidance"
    refresh_of_report_id: str | None = None
    guidance: GuidancePayload = Field(default_factory=GuidancePayload, validation_alias=AliasChoices("guidance", "guidance_json"))
    claim_ids: list[str] = Field(min_length=1, validation_alias=AliasChoices("claim_ids", "finding_ids"))
    review_state: ReviewState = "unreviewed"
    trust_tier: TrustTier = "medium"
    conflict_state: ConflictState = "none"
    refresh_due_at: datetime | None = None
    visibility: Visibility = "private"
    author_type: AuthorType = "agent"
    model_name: str | None = None
    model_version: str | None = None
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    dedupe_key: str | None = None


class ReportRecord(ReportCreate):
    id: str
    source_ids: list[str]
    created_at: datetime
    human_reviewed: bool = False
    freshness_state: FreshnessState | None = None
    expires_at: datetime | None = None
    is_stale: bool = False
    actor_user_id: str | None = None
    actor_org_id: str | None = None
    api_key_id: str | None = None
    public_namespace_slug: str | None = None
    public_index_state: PublicIndexState = "private"

    @property
    def subject(self) -> str:
        return self.focal_label

    @property
    def finding_ids(self) -> list[str]:
        return self.claim_ids


class PublishRequest(BaseModel):
    kind: RecordKind
    record_id: str
    cascade_linked_sources: bool = True
    include_in_global_index: bool = False


class ReviewRequest(BaseModel):
    kind: RecordKind
    record_id: str
    reviewed: bool = True


class SearchHit(BaseModel):
    kind: RecordKind
    id: str
    title: str
    summary: str
    subject: str
    visibility: Visibility
    created_at: datetime
    score: float
    url: str
    source_title: str | None = None
    human_reviewed: bool = False
    review_state: ReviewState = "unreviewed"
    trust_tier: TrustTier = "low"
    conflict_state: ConflictState = "none"
    freshness_state: FreshnessState | None = None
    expires_at: datetime | None = None
    is_stale: bool = False
    refresh_due_at: datetime | None = None
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    public_namespace_slug: str | None = None
    public_index_state: PublicIndexState = "private"


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


class DashboardData(BaseModel):
    reports: list[ReportRecord]
    claims: list[ClaimRecord]
    questions: list[QuestionRecord]


class UserRecord(BaseModel):
    id: str
    display_name: str
    created_at: datetime


class OrganizationRecord(BaseModel):
    id: str
    display_name: str
    created_at: datetime


class ApiKeyCreate(BaseModel):
    label: str
    actor_user_id: str
    actor_org_id: str | None = None
    namespace_kind: NamespaceKind = "user"
    namespace_id: str | None = None
    scopes: list[ApiKeyScope] = Field(default_factory=lambda: ["ingest", "publish", "read_private"])


class ApiKeyRecord(BaseModel):
    id: str
    label: str
    actor_user_id: str
    actor_org_id: str | None = None
    namespace_kind: NamespaceKind
    namespace_id: str
    scopes: list[ApiKeyScope]
    status: ApiKeyStatus
    created_at: datetime
    revoked_at: datetime | None = None


class IssuedApiKey(BaseModel):
    token: str
    record: ApiKeyRecord


class AuthContext(BaseModel):
    api_key_id: str | None = None
    actor_user_id: str | None = None
    actor_org_id: str | None = None
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    scopes: list[ApiKeyScope] = Field(default_factory=list)
    is_admin: bool = False

    def has_scope(self, scope: ApiKeyScope) -> bool:
        return self.is_admin or scope in self.scopes


class IndexStateRequest(BaseModel):
    kind: RecordKind
    record_id: str
    state: PublicIndexState


class BackendStatus(BaseModel):
    name: str
    kind: str
    selection_source: str
    url: str | None = None
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    api_key_present: bool = False
    org: str | None = None


class FollowUpStatusUpdate(BaseModel):
    follow_up_status: FollowUpStatus


class ImportUrlRequest(BaseModel):
    url: str
    question_id: str | None = None
    focal_label: str | None = None
    note: str | None = None
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"


class ImportDoiRequest(BaseModel):
    doi: str
    question_id: str | None = None
    focal_label: str | None = None
    note: str | None = None
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"


class ImportBibtexRequest(BaseModel):
    bibtex: str
    question_id: str | None = None
    focal_label: str | None = None
    note: str | None = None
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"


class ImportResult(BaseModel):
    source_ids: list[str] = Field(default_factory=list)
    excerpt_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    review_state: ReviewState = "unreviewed"
    question_id: str | None = None


class BriefResolveRequest(BaseModel):
    prompt: str
    limit: int = Field(default=5, ge=1, le=20)
    include_private: bool = True


class RelatedQuestionCluster(BaseModel):
    topic_id: str
    focus_label: str
    canonical_question_id: str
    question_ids: list[str]
    latest_report_id: str | None = None
    question_count: int


class BriefBundle(BaseModel):
    prompt: str
    focus: FocusTuple
    reports: list[ReportRecord] = Field(default_factory=list)
    claims: list[ClaimRecord] = Field(default_factory=list)
    excerpts: list[ExcerptRecord] = Field(default_factory=list)
    related_questions: list[QuestionRecord] = Field(default_factory=list)
    related_clusters: list[RelatedQuestionCluster] = Field(default_factory=list)
    stale_items: list[SearchHit] = Field(default_factory=list)
    suggested_follow_ups: list[QuestionRecord] = Field(default_factory=list)


# Compatibility types retained so older helper modules still import cleanly while the
# runtime storage contract moves to question/session/excerpt/claim/report.


class RunCreate(BaseModel):
    question: str
    model_name: str
    model_version: str
    notes: str | None = None
    visibility: Visibility = "private"
    author_type: AuthorType = "agent"
    freshness_ttl_days: int = Field(default=30, ge=1, le=3650)
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    dedupe_key: str | None = None


class RunRecord(RunCreate):
    id: str
    started_at: datetime
    finished_at: datetime
    created_at: datetime
    actor_user_id: str | None = None
    actor_org_id: str | None = None
    api_key_id: str | None = None
    public_namespace_slug: str | None = None
    public_index_state: PublicIndexState = "private"


class AnnotationCreate(BaseModel):
    source_id: str | None = None
    source: SourceCreate | None = None
    run_id: str | None = None
    subject: str
    note: str
    selector: SourceSelector
    quote_text: str | None = None
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    freshness_ttl_days: int = Field(default=30, ge=1, le=3650)
    visibility: Visibility = "private"
    author_type: AuthorType = "agent"
    model_name: str | None = None
    model_version: str | None = None
    parent_annotation_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    dedupe_key: str | None = None

    @model_validator(mode="after")
    def validate_source_reference(self) -> "AnnotationCreate":
        if not self.source_id and not self.source:
            raise ValueError("either source_id or source must be provided")
        return self


class AnnotationRecord(BaseModel):
    id: str
    source_id: str
    run_id: str | None = None
    subject: str
    note: str
    selector: SourceSelector
    quote_text: str | None = None
    quote_hash: str | None = None
    anchor_fingerprint: str = ""
    confidence: float
    freshness_ttl_days: int
    visibility: Visibility
    author_type: AuthorType
    model_name: str | None = None
    model_version: str | None = None
    parent_annotation_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    source_content_sha256: str | None = None
    created_at: datetime
    human_reviewed: bool = False
    is_stale: bool = False
    staleness_reason: str | None = None
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    actor_user_id: str | None = None
    actor_org_id: str | None = None
    api_key_id: str | None = None
    public_namespace_slug: str | None = None
    public_index_state: PublicIndexState = "private"
    dedupe_key: str | None = None


class FindingCreate(BaseModel):
    title: str
    subject: str
    claim: str
    annotation_ids: list[str] = Field(min_length=1)
    visibility: Visibility = "private"
    author_type: AuthorType = "agent"
    model_name: str | None = None
    model_version: str | None = None
    run_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    dedupe_key: str | None = None


class FindingRecord(BaseModel):
    id: str
    title: str
    subject: str
    claim: str
    annotation_ids: list[str]
    visibility: Visibility
    author_type: AuthorType
    model_name: str | None = None
    model_version: str | None = None
    run_id: str | None = None
    confidence: float
    created_at: datetime
    human_reviewed: bool = False
    is_stale: bool = False
    staleness_reason: str | None = None
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    actor_user_id: str | None = None
    actor_org_id: str | None = None
    api_key_id: str | None = None
    public_namespace_slug: str | None = None
    public_index_state: PublicIndexState = "private"
    dedupe_key: str | None = None


class ReportCompileCreate(BaseModel):
    question: str
    subject: str
    finding_ids: list[str] = Field(min_length=1)
    visibility: Visibility = "private"
    author_type: AuthorType = "agent"
    model_name: str | None = None
    model_version: str | None = None
    run_id: str | None = None
    namespace_kind: NamespaceKind = "user"
    namespace_id: str = "local"
    dedupe_key: str | None = None
