from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping


@dataclass(frozen=True)
class SearchDocument:
    id: str
    kind: str
    title: str
    summary: str
    body: str
    locator: str | None = None
    doi: str | None = None
    repository: str | None = None
    path: str | None = None
    canonical_key: str | None = None
    topic_slug: str | None = None
    quote_hash: str | None = None
    dedupe_key: str | None = None
    review_state: str | None = None
    trust_tier: str | None = None
    conflict_state: str | None = None
    freshness: str | None = None
    status: str | None = None
    evidence_count: int = 0
    updated_at: str | None = None
    created_at: str | None = None
    url: str | None = None
    source_type: str | None = None
    topic_id: str | None = None
    visibility: str = "private"
    namespace_kind: str = "user"
    namespace_id: str = "local"
    public_index_state: str = "private"

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> SearchDocument:
        values = {field.name: row[field.name] for field in fields(cls)}
        values["evidence_count"] = int(values["evidence_count"] or 0)
        return cls(**values)

    @property
    def search_text(self) -> str:
        return " ".join(
            value
            for value in (
                self.title,
                self.summary,
                self.body,
                self.locator,
                self.repository,
                self.path,
                self.canonical_key,
                self.topic_slug,
                self.dedupe_key,
            )
            if value
        )


@dataclass(frozen=True)
class LexicalMatch:
    document: SearchDocument
    exact: float = 0.0
    lexical: float = 0.0
    matched_by: tuple[str, ...] = ()
