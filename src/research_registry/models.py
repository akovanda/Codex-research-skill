from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Visibility = Literal["private", "public"]
AuthorType = Literal["human", "agent"]
RecordKind = Literal["source", "annotation", "finding", "report"]


class SourceSelector(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = "TextQuoteSelector"
    deep_link: str | None = None
    exact: str | None = None
    prefix: str | None = None
    suffix: str | None = None
    start: int | None = None
    end: int | None = None


class RunCreate(BaseModel):
    question: str
    model_name: str
    model_version: str
    notes: str | None = None
    visibility: Visibility = "private"
    author_type: AuthorType = "agent"
    freshness_ttl_days: int = Field(default=30, ge=1, le=3650)


class RunRecord(RunCreate):
    id: str
    started_at: datetime
    finished_at: datetime
    created_at: datetime


class SourceCreate(BaseModel):
    canonical_url: str
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
    visibility: Visibility = "private"


class SourceRecord(SourceCreate):
    id: str
    created_at: datetime


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
    anchor_fingerprint: str
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


class ReportCompileCreate(BaseModel):
    question: str
    subject: str
    finding_ids: list[str] = Field(min_length=1)
    visibility: Visibility = "private"
    author_type: AuthorType = "agent"
    model_name: str | None = None
    model_version: str | None = None
    run_id: str | None = None


class ReportRecord(BaseModel):
    id: str
    question: str
    subject: str
    summary_md: str
    finding_ids: list[str]
    annotation_ids: list[str]
    source_ids: list[str]
    visibility: Visibility
    author_type: AuthorType
    model_name: str | None = None
    model_version: str | None = None
    run_id: str | None = None
    created_at: datetime
    human_reviewed: bool = False
    is_stale: bool = False
    staleness_reason: str | None = None


class PublishRequest(BaseModel):
    kind: RecordKind
    record_id: str
    cascade_linked_sources: bool = True


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


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


class DashboardData(BaseModel):
    reports: list[ReportRecord]
    findings: list[FindingRecord]
    annotations: list[AnnotationRecord]
