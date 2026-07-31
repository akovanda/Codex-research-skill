from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints


class ClosedModel(BaseModel):
    """Base for closed external contracts."""

    model_config = ConfigDict(extra="forbid", strict=True)


NonEmptyString100 = Annotated[str, StringConstraints(min_length=1, max_length=100)]
NonEmptyString200 = Annotated[str, StringConstraints(min_length=1, max_length=200)]
RecordId = Annotated[str, StringConstraints(min_length=3, max_length=200)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitObjectId = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"),
]
Locator = Annotated[str, StringConstraints(min_length=1, max_length=8192)]

Visibility = Literal["private", "public"]
NamespaceKind = Literal["user", "org"]
ReviewState = Literal["unreviewed", "reviewed", "flagged"]
TrustTier = Literal["low", "medium", "high"]
ConflictState = Literal["none", "conflicted", "resolved"]
FreshnessState = Literal["fresh", "needs_refresh", "stale", "unknown"]
ClaimRevisionStatus = Literal[
    "draft",
    "partial",
    "supported",
    "contested",
    "rejected",
    "superseded",
]
EvidenceRelationship = Literal[
    "supports",
    "refutes",
    "qualifies",
    "contextualizes",
]
SnapshotPolicy = Literal[
    "metadata_only",
    "evidence_only",
    "extracted_text",
    "full_content",
]

JsonObject20 = Annotated[dict[str, JsonValue], Field(max_length=20)]
JsonObject50 = Annotated[dict[str, JsonValue], Field(max_length=50)]
JsonObject100 = Annotated[dict[str, JsonValue], Field(max_length=100)]
