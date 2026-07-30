from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Iterable
from uuid import UUID, uuid5

from ..db import DbConnection
from ..ingestion.blobs import BlobReference
from ..timestamps import is_due, utc_text


V2_MIGRATION_ID = "0003_v2_evidence"
_MIGRATION_NAMESPACE = UUID("0a270f0b-0a9a-4e6a-9ce5-51449c700ac3")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_SELECTOR_TYPES = {
    "text_quote",
    "line_range",
    "char_range",
    "page_range",
    "json_pointer",
    "dom_text",
    "git_line_range",
}
_SELECTOR_TYPE_ALIASES = {
    "textquoteselector": "text_quote",
    "text_quote": "text_quote",
    "textrangeselector": "char_range",
    "textpositionselector": "char_range",
    "char_range": "char_range",
    "linerangeselector": "line_range",
    "line_range": "line_range",
    "pagerangeselector": "page_range",
    "page_range": "page_range",
    "jsonpointerselector": "json_pointer",
    "json_pointer": "json_pointer",
    "domtextselector": "dom_text",
    "dom_text": "dom_text",
    "gitlinerangeselector": "git_line_range",
    "git_line_range": "git_line_range",
}
_SELECTOR_FIELDS = {
    "deep_link",
    "end",
    "end_line",
    "end_page",
    "exact",
    "path",
    "pointer",
    "prefix",
    "start",
    "start_line",
    "start_page",
    "suffix",
}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def deterministic_v2_id(prefix: str, kind: str, legacy_id: str) -> str:
    return f"{prefix}_{uuid5(_MIGRATION_NAMESPACE, f'{kind}:{legacy_id}')}"


@dataclass(frozen=True)
class ContentObjectRecord:
    id: str
    sha256: str
    storage_backend: str
    storage_key: str
    media_type: str | None
    byte_count: int
    compression: str
    created_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SourceVersionRecord:
    id: str
    source_id: str
    version_key: str
    version_kind: str
    retrieved_at: str
    published_at: str | None
    content_sha256: str
    canonical_locator: str
    metadata: dict[str, Any]
    created_at: str
    content_object_id: str | None = None
    media_type: str | None = None
    byte_count: int | None = None
    parser_name: str | None = None
    parser_version: str | None = None
    repository_locator: str | None = None
    commit_sha: str | None = None
    blob_sha: str | None = None
    path: str | None = None
    persisted: bool = True


@dataclass(frozen=True)
class EvidenceSpanRecord:
    id: str
    source_version_id: str
    topic_id: str | None
    question_id: str | None
    session_id: str | None
    quote_text: str
    quote_sha256: str
    selector_type: str
    selector: dict[str, Any]
    note: str | None
    confidence: float
    anchor_state: str
    review_state: str
    trust_tier: str
    created_by_model: str | None
    created_at: str
    metadata: dict[str, Any]
    persisted: bool = True


@dataclass(frozen=True)
class ClaimRevisionRecord:
    id: str
    claim_id: str
    revision_number: int
    title: str
    statement: str
    status: str
    confidence: float
    created_by_model: str | None
    created_at: str
    metadata: dict[str, Any]
    persisted: bool = True


@dataclass(frozen=True)
class ClaimEvidenceRecord:
    claim_revision_id: str
    evidence_span_id: str
    relationship: str
    rationale: str | None
    weight: float
    review_state: str
    created_at: str
    persisted: bool = True


def source_version_from_legacy(
    row: Any, *, persisted: bool
) -> SourceVersionRecord:
    legacy_hash = (row["content_sha256"] or "").strip()
    warnings: list[str] = []
    source_type = (row["source_type"] or "").strip().lower()
    locator = row["locator"]
    if _SHA256.fullmatch(legacy_hash):
        content_hash = legacy_hash.lower()
        if legacy_hash != content_hash:
            warnings.append("normalized_legacy_hash")
        if source_type == "doi" or locator.lower().startswith(
            ("doi:", "https://doi.org/", "http://doi.org/")
        ):
            warnings.append("legacy_locator_hash")
        elif not bool(row["snapshot_present"]):
            warnings.append("weak_legacy_hash")
    else:
        if legacy_hash:
            warnings.append("invalid_legacy_hash")
        else:
            warnings.append("missing_legacy_hash")
        content_hash = sha256(
            canonical_json(_legacy_source_fingerprint(row)).encode("utf-8")
        ).hexdigest()
        warnings.append("legacy_metadata_hash")

    if not bool(row["snapshot_present"]):
        warnings.append("missing_snapshot")
    else:
        # RR2-003 creates metadata only; RR2-004 owns blob capture.
        warnings.append("legacy_snapshot_unlinked")

    metadata = {
        "legacy_content_sha256": row["content_sha256"],
        "legacy_source_id": row["id"],
        "legacy_state": {
            "conflict_state": row["conflict_state"],
            "refresh_due_at": row["refresh_due_at"],
            "review_state": row["review_state"],
            "trust_tier": row["trust_tier"],
        },
        "migration_id": V2_MIGRATION_ID,
        "migration_warnings": sorted(set(warnings)),
        "snapshot_policy": "metadata_only",
        "snapshot_present": bool(row["snapshot_present"]),
        "snapshot_required": bool(row["snapshot_required"]),
        "snapshot_url": row["snapshot_url"],
    }
    return SourceVersionRecord(
        id=deterministic_v2_id("srcv", "source", row["id"]),
        source_id=row["id"],
        version_key=f"migration:{content_hash}",
        version_kind="migration",
        retrieved_at=(
            row["accessed_at"]
            or row["last_verified_at"]
            or row["created_at"]
        ),
        published_at=row["published_at"],
        content_sha256=content_hash,
        canonical_locator=locator,
        metadata=metadata,
        created_at=row["created_at"],
        persisted=persisted,
    )


def _legacy_source_fingerprint(row: Any) -> dict[str, Any]:
    fields = (
        "accessed_at",
        "author",
        "content_sha256",
        "created_at",
        "id",
        "last_verified_at",
        "locator",
        "published_at",
        "site_name",
        "snapshot_present",
        "snapshot_required",
        "snapshot_url",
        "source_type",
        "title",
    )
    return {field: row[field] for field in fields}


def evidence_span_from_legacy(
    row: Any, *, source_version_id: str, persisted: bool
) -> EvidenceSpanRecord:
    selector, warnings = _normalize_legacy_selector(
        row["selector_json"], row["quote_text"]
    )
    review_state = row["review_state"]
    if review_state not in {"unreviewed", "reviewed", "flagged"}:
        warnings.append("invalid_legacy_review_state")
        review_state = "unreviewed"
    trust_tier = row["trust_tier"]
    if trust_tier not in {"low", "medium", "high"}:
        warnings.append("invalid_legacy_trust_tier")
        trust_tier = "low"
    metadata = {
        "legacy_excerpt_id": row["id"],
        "legacy_selector_json": row["selector_json"],
        "legacy_state": {
            "conflict_state": row["conflict_state"],
            "freshness_state": _optional_row_value(
                row, "session_freshness_state"
            ),
            "refresh_due_at": row["refresh_due_at"],
            "review_state": row["review_state"],
            "trust_tier": row["trust_tier"],
        },
        "legacy_tags_json": row["tags_json"],
        "migration_id": V2_MIGRATION_ID,
        "migration_warnings": sorted(set(warnings)),
    }
    return EvidenceSpanRecord(
        id=deterministic_v2_id("evd", "excerpt", row["id"]),
        source_version_id=source_version_id,
        topic_id=row["topic_id"],
        question_id=row["question_id"],
        session_id=row["session_id"],
        quote_text=row["quote_text"],
        quote_sha256=sha256(row["quote_text"].encode("utf-8")).hexdigest(),
        selector_type=selector["type"],
        selector=selector,
        note=row["note"],
        confidence=float(row["confidence"]),
        anchor_state="unverified",
        review_state=review_state,
        trust_tier=trust_tier,
        created_by_model=row["model_name"],
        created_at=row["created_at"],
        metadata=metadata,
        persisted=persisted,
    )


def _normalize_legacy_selector(
    raw_selector: str, quote_text: str
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    try:
        parsed = json.loads(raw_selector)
    except (TypeError, json.JSONDecodeError):
        parsed = {}
        warnings.append("malformed_legacy_selector")
    if not isinstance(parsed, dict):
        parsed = {}
        warnings.append("malformed_legacy_selector")

    raw_type = parsed.get("type")
    normalized_type = None
    if isinstance(raw_type, str):
        normalized_type = _SELECTOR_TYPE_ALIASES.get(
            raw_type.strip().lower().replace("-", "_")
        )
        if normalized_type is None and raw_type in _SELECTOR_TYPES:
            normalized_type = raw_type
        if normalized_type is None:
            warnings.append("legacy_selector_type")
    if normalized_type is None:
        if parsed.get("pointer") is not None:
            normalized_type = "json_pointer"
        elif (
            parsed.get("start_page") is not None
            or parsed.get("end_page") is not None
        ):
            normalized_type = "page_range"
        elif (
            parsed.get("start_line") is not None
            or parsed.get("end_line") is not None
        ):
            normalized_type = (
                "git_line_range"
                if any(parsed.get(field) for field in ("path", "commit_sha", "blob_sha"))
                else "line_range"
            )
        elif parsed.get("start") is not None or parsed.get("end") is not None:
            normalized_type = "char_range"
        else:
            normalized_type = "text_quote"

    selector: dict[str, Any] = {"type": normalized_type}
    for field in sorted(_SELECTOR_FIELDS):
        value = parsed.get(field)
        if value is None:
            continue
        if field in {
            "end",
            "end_line",
            "end_page",
            "start",
            "start_line",
            "start_page",
        } and not isinstance(value, int):
            warnings.append("legacy_selector_field_type")
            continue
        if field not in {
            "end",
            "end_line",
            "end_page",
            "start",
            "start_line",
            "start_page",
        } and not isinstance(value, str):
            warnings.append("legacy_selector_field_type")
            continue
        selector[field] = value
    if "exact" not in selector:
        selector["exact"] = quote_text
    unknown_fields = set(parsed) - _SELECTOR_FIELDS - {"type"}
    if unknown_fields:
        warnings.append("legacy_selector_fields_preserved_in_metadata")
    return dict(sorted(selector.items())), warnings


def claim_revision_from_legacy(
    row: Any, *, persisted: bool
) -> ClaimRevisionRecord:
    status_map = {
        "supported": "supported",
        "partial": "partial",
        "conflicted": "contested",
        "insufficient_evidence": "draft",
    }
    status = status_map.get(row["status"], "draft")
    warnings = (
        []
        if status == row["status"]
        else ["legacy_claim_status_mapped"]
    )
    metadata = {
        "legacy_claim_id": row["id"],
        "legacy_status": row["status"],
        "legacy_state": {
            "conflict_state": row["conflict_state"],
            "freshness_state": _optional_row_value(
                row, "session_freshness_state"
            ),
            "refresh_due_at": row["refresh_due_at"],
            "review_state": row["review_state"],
            "trust_tier": row["trust_tier"],
        },
        "model_version": row["model_version"],
        "migration_id": V2_MIGRATION_ID,
        "migration_warnings": warnings,
    }
    return ClaimRevisionRecord(
        id=deterministic_v2_id("clmr", "claim", row["id"]),
        claim_id=row["id"],
        revision_number=1,
        title=row["title"],
        statement=row["statement"],
        status=status,
        confidence=float(row["confidence"]),
        created_by_model=row["model_name"],
        created_at=row["created_at"],
        metadata=metadata,
        persisted=persisted,
    )


def _optional_row_value(row: Any, field: str) -> Any:
    try:
        return row[field]
    except (IndexError, KeyError):
        return None


class DepositRepository:
    """SQL boundary used by the atomic v2 deposit application service."""

    def __init__(self, conn: DbConnection):
        self.conn = conn

    def get_idempotency(
        self,
        namespace_kind: str,
        namespace_id: str,
        operation: str,
        key: str,
    ) -> Any | None:
        return self.conn.execute(
            """
            SELECT * FROM idempotency_keys
            WHERE namespace_kind = ? AND namespace_id = ?
              AND operation = ? AND "key" = ?
            """,
            (namespace_kind, namespace_id, operation, key),
        ).fetchone()

    def reserve_idempotency(
        self,
        *,
        namespace_kind: str,
        namespace_id: str,
        operation: str,
        key: str,
        request_sha256: str,
        reservation_json: str,
        created_at: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO idempotency_keys (
                namespace_kind, namespace_id, operation, "key", request_sha256,
                response_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(namespace_kind, namespace_id, operation, "key")
            DO NOTHING
            """,
            (
                namespace_kind,
                namespace_id,
                operation,
                key,
                request_sha256,
                reservation_json,
                created_at,
            ),
        )

    def complete_idempotency(
        self,
        *,
        namespace_kind: str,
        namespace_id: str,
        operation: str,
        key: str,
        reservation_json: str,
        response_json: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE idempotency_keys
            SET response_json = ?
            WHERE namespace_kind = ? AND namespace_id = ?
              AND operation = ? AND "key" = ?
              AND response_json = ?
            """,
            (
                response_json,
                namespace_kind,
                namespace_id,
                operation,
                key,
                reservation_json,
            ),
        )

    def find_topic(
        self, *, slug: str, namespace_kind: str, namespace_id: str
    ) -> Any | None:
        return self.conn.execute(
            """
            SELECT * FROM topics
            WHERE slug = ? AND namespace_kind = ? AND namespace_id = ?
            """,
            (slug, namespace_kind, namespace_id),
        ).fetchone()

    def insert_topic(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO topics (
                id, label, slug, focus_json, namespace_kind, namespace_id,
                dedupe_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["id"],
                values["label"],
                values["slug"],
                values["focus_json"],
                values["namespace_kind"],
                values["namespace_id"],
                values["dedupe_key"],
                values["created_at"],
            ),
        )

    def find_question(
        self,
        *,
        topic_id: str,
        normalized_prompt: str,
        namespace_kind: str,
        namespace_id: str,
    ) -> Any | None:
        return self.conn.execute(
            """
            SELECT * FROM questions
            WHERE topic_id = ? AND normalized_prompt = ?
              AND namespace_kind = ? AND namespace_id = ?
            LIMIT 1
            """,
            (topic_id, normalized_prompt, namespace_kind, namespace_id),
        ).fetchone()

    def insert_question(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO questions (
                id, topic_id, prompt, normalized_prompt, focus_json, status,
                follow_up_status, priority_score, visibility, author_type,
                namespace_kind, namespace_id, actor_user_id, actor_org_id,
                api_key_id, public_namespace_slug,
                public_index_state, dedupe_key, human_reviewed, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, 'open', 'open', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, 0, ?
            )
            """,
            (
                values["id"],
                values["topic_id"],
                values["prompt"],
                values["normalized_prompt"],
                values["focus_json"],
                values["visibility"],
                values["author_type"],
                values["namespace_kind"],
                values["namespace_id"],
                values.get("actor_user_id"),
                values.get("actor_org_id"),
                values.get("api_key_id"),
                values["namespace_id"],
                values["public_index_state"],
                values["dedupe_key"],
                values["created_at"],
            ),
        )

    def insert_session(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO research_sessions (
                id, question_id, prompt, model_name, model_version, mode,
                status, source_signals_json, notes, visibility, author_type,
                namespace_kind, namespace_id, actor_user_id, actor_org_id,
                api_key_id, public_namespace_slug,
                public_index_state, dedupe_key, ttl_days, expires_at,
                freshness_state, created_at, started_at, finished_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, 30, ?, 'fresh', ?, ?, ?
            )
            """,
            (
                values["id"],
                values["question_id"],
                values["prompt"],
                values["model_name"],
                values["model_version"],
                values["mode"],
                values["source_signals_json"],
                values["notes"],
                values["visibility"],
                values["author_type"],
                values["namespace_kind"],
                values["namespace_id"],
                values.get("actor_user_id"),
                values.get("actor_org_id"),
                values.get("api_key_id"),
                values["namespace_id"],
                values["public_index_state"],
                values["dedupe_key"],
                values["expires_at"],
                values["created_at"],
                values["started_at"],
                values["finished_at"],
            ),
        )

    def find_source(
        self,
        *,
        dedupe_key: str | None,
        locator: str,
        namespace_kind: str,
        namespace_id: str,
    ) -> Any | None:
        if dedupe_key is not None:
            return self.conn.execute(
                "SELECT * FROM sources WHERE dedupe_key = ? LIMIT 1",
                (dedupe_key,),
            ).fetchone()
        return self.conn.execute(
            """
            SELECT * FROM sources
            WHERE locator = ? AND namespace_kind = ? AND namespace_id = ?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (locator, namespace_kind, namespace_id),
        ).fetchone()

    def insert_source(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO sources (
                id, locator, title, source_type, site_name, published_at,
                accessed_at, author, content_sha256, snapshot_required,
                snapshot_present, last_verified_at, review_state, trust_tier,
                conflict_state, visibility, namespace_kind, namespace_id,
                actor_user_id, actor_org_id, api_key_id,
                public_namespace_slug, public_index_state, dedupe_key,
                created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'unreviewed', ?, 'none',
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                values["id"],
                values["locator"],
                values["title"],
                values["source_type"],
                values["site_name"],
                values["published_at"],
                values["accessed_at"],
                values["author"],
                values["content_sha256"],
                values["snapshot_present"],
                values["last_verified_at"],
                values["trust_tier"],
                values["visibility"],
                values["namespace_kind"],
                values["namespace_id"],
                values.get("actor_user_id"),
                values.get("actor_org_id"),
                values.get("api_key_id"),
                values["namespace_id"],
                values["public_index_state"],
                values["dedupe_key"],
                values["created_at"],
            ),
        )

    def get_source_version_scoped(
        self,
        source_version_id: str,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> Any | None:
        return self.conn.execute(
            """
            SELECT sv.*, s.namespace_kind, s.namespace_id
            FROM source_versions sv
            JOIN sources s ON s.id = sv.source_id
            WHERE sv.id = ? AND s.namespace_kind = ? AND s.namespace_id = ?
            """,
            (source_version_id, namespace_kind, namespace_id),
        ).fetchone()

    def get_evidence_scoped(
        self,
        evidence_id: str,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> Any | None:
        return self.conn.execute(
            """
            SELECT e.*, sv.source_id
            FROM evidence_spans e
            JOIN source_versions sv ON sv.id = e.source_version_id
            JOIN sources s ON s.id = sv.source_id
            WHERE e.id = ? AND s.namespace_kind = ? AND s.namespace_id = ?
            """,
            (evidence_id, namespace_kind, namespace_id),
        ).fetchone()

    def insert_legacy_excerpt(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO excerpts (
                id, source_id, question_id, session_id, topic_id, focal_label,
                note, selector_json, quote_text, confidence, tags_json,
                review_state, trust_tier, conflict_state, visibility,
                author_type, model_name, model_version, namespace_kind,
                namespace_id, actor_user_id, actor_org_id, api_key_id,
                public_namespace_slug, public_index_state,
                dedupe_key, human_reviewed, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, 'none', ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                values["id"],
                values["source_id"],
                values["question_id"],
                values["session_id"],
                values["topic_id"],
                values["focal_label"],
                values["note"],
                values["selector_json"],
                values["quote_text"],
                values["confidence"],
                values["review_state"],
                values["trust_tier"],
                values["visibility"],
                values["author_type"],
                values["model_name"],
                values["model_version"],
                values["namespace_kind"],
                values["namespace_id"],
                values.get("actor_user_id"),
                values.get("actor_org_id"),
                values.get("api_key_id"),
                values["namespace_id"],
                values["public_index_state"],
                values["dedupe_key"],
                values["human_reviewed"],
                values["created_at"],
            ),
        )

    def insert_evidence(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO evidence_spans (
                id, source_version_id, topic_id, question_id, session_id,
                quote_text, quote_sha256, selector_type, selector_json, note,
                confidence, anchor_state, review_state, trust_tier,
                created_by_model, created_at, metadata_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unverified', ?, ?, ?, ?, ?
            )
            """,
            (
                values["id"],
                values["source_version_id"],
                values["topic_id"],
                values["question_id"],
                values["session_id"],
                values["quote_text"],
                values["quote_sha256"],
                values["selector_type"],
                values["selector_json"],
                values["note"],
                values["confidence"],
                values["review_state"],
                values["trust_tier"],
                values["created_by_model"],
                values["created_at"],
                values["metadata_json"],
            ),
        )

    def get_claim_scoped(
        self,
        claim_id: str,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> Any | None:
        suffix = " FOR UPDATE" if self.conn.target.kind == "postgres" else ""
        return self.conn.execute(
            """
            SELECT * FROM claims
            WHERE id = ? AND namespace_kind = ? AND namespace_id = ?
            """
            + suffix,
            (claim_id, namespace_kind, namespace_id),
        ).fetchone()

    def find_claim_by_canonical(
        self,
        canonical_key: str,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> Any | None:
        if self.conn.target.kind == "postgres":
            self.conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                (f"{namespace_kind}:{namespace_id}:{canonical_key}",),
            )
        suffix = " FOR UPDATE" if self.conn.target.kind == "postgres" else ""
        return self.conn.execute(
            """
            SELECT * FROM claims
            WHERE canonical_key = ? AND namespace_kind = ? AND namespace_id = ?
            ORDER BY created_at ASC
            LIMIT 1
            """
            + suffix,
            (canonical_key, namespace_kind, namespace_id),
        ).fetchone()

    def insert_claim(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO claims (
                id, question_id, session_id, topic_id, title, focal_label,
                statement, status, confidence, review_state, trust_tier,
                conflict_state, visibility, author_type, model_name,
                model_version, namespace_kind, namespace_id,
                actor_user_id, actor_org_id, api_key_id,
                public_namespace_slug, public_index_state, dedupe_key,
                human_reviewed, created_at, canonical_key, scope_json,
                updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed', 'medium', 'none', ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?
            )
            """,
            (
                values["id"],
                values["question_id"],
                values["session_id"],
                values["topic_id"],
                values["title"],
                values["focal_label"],
                values["statement"],
                values["legacy_status"],
                values["confidence"],
                values["visibility"],
                values["author_type"],
                values["model_name"],
                values["model_version"],
                values["namespace_kind"],
                values["namespace_id"],
                values.get("actor_user_id"),
                values.get("actor_org_id"),
                values.get("api_key_id"),
                values["namespace_id"],
                values["public_index_state"],
                values["dedupe_key"],
                values["created_at"],
                values["canonical_key"],
                values["scope_json"],
                values["created_at"],
            ),
        )

    def next_claim_revision_number(self, claim_id: str) -> int:
        row = self.conn.execute(
            """
            SELECT COALESCE(MAX(revision_number), 0) + 1 AS number
            FROM claim_revisions WHERE claim_id = ?
            """,
            (claim_id,),
        ).fetchone()
        return int(row["number"])

    def insert_claim_revision(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO claim_revisions (
                id, claim_id, revision_number, title, statement, status,
                confidence, supersedes_revision_id, created_by_model,
                created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["id"],
                values["claim_id"],
                values["revision_number"],
                values["title"],
                values["statement"],
                values["status"],
                values["confidence"],
                values["supersedes_revision_id"],
                values["created_by_model"],
                values["created_at"],
                values["metadata_json"],
            ),
        )

    def insert_claim_evidence(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO claim_evidence (
                claim_revision_id, evidence_span_id, relationship, rationale,
                weight, review_state, created_at
            ) VALUES (?, ?, ?, ?, ?, 'unreviewed', ?)
            """,
            (
                values["claim_revision_id"],
                values["evidence_span_id"],
                values["relationship"],
                values["rationale"],
                values["weight"],
                values["created_at"],
            ),
        )

    def update_claim_pointer(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            UPDATE claims
            SET current_revision_id = ?, title = ?, statement = ?, status = ?,
                confidence = ?, canonical_key = ?, scope_json = ?,
                session_id = ?, topic_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                values["revision_id"],
                values["title"],
                values["statement"],
                values["legacy_status"],
                values["confidence"],
                values["canonical_key"],
                values["scope_json"],
                values["session_id"],
                values["topic_id"],
                values["updated_at"],
                values["claim_id"],
            ),
        )

    def replace_claim_excerpts(
        self, claim_id: str, links: list[tuple[str, str | None, float]]
    ) -> None:
        self.conn.execute(
            "DELETE FROM claim_excerpts WHERE claim_id = ?",
            (claim_id,),
        )
        for excerpt_id, rationale, weight in links:
            self.conn.execute(
                """
                INSERT INTO claim_excerpts (
                    claim_id, excerpt_id, rationale, weight
                ) VALUES (?, ?, ?, ?)
                """,
                (claim_id, excerpt_id, rationale, weight),
            )

    def insert_report(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO reports (
                id, question_id, session_id, title, focal_label, summary_md,
                report_kind, guidance_json, review_state, trust_tier,
                conflict_state, visibility, author_type, model_name,
                model_version, namespace_kind, namespace_id,
                actor_user_id, actor_org_id, api_key_id,
                public_namespace_slug, public_index_state, dedupe_key,
                human_reviewed, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed', 'medium', 'none', ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?
            )
            """,
            (
                values["id"],
                values["question_id"],
                values["session_id"],
                values["title"],
                values["focal_label"],
                values["summary_md"],
                values["report_kind"],
                values["guidance_json"],
                values["visibility"],
                values["author_type"],
                values["model_name"],
                values["model_version"],
                values["namespace_kind"],
                values["namespace_id"],
                values.get("actor_user_id"),
                values.get("actor_org_id"),
                values.get("api_key_id"),
                values["namespace_id"],
                values["public_index_state"],
                values["dedupe_key"],
                values["created_at"],
            ),
        )

    def insert_report_claim(self, report_id: str, claim_id: str) -> None:
        self.conn.execute(
            "INSERT INTO report_claims (report_id, claim_id) VALUES (?, ?)",
            (report_id, claim_id),
        )

    def mark_question_answered(self, question_id: str) -> None:
        self.conn.execute(
            "UPDATE questions SET status = 'answered' WHERE id = ?",
            (question_id,),
        )

    def insert_deposit_audit(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO audit_log (
                id, action, kind, record_id, api_key_id, actor_user_id,
                actor_org_id, details_json, created_at
            ) VALUES (?, 'research_deposit', 'deposit', ?, ?, ?, ?, ?, ?)
            """,
            (
                values["id"],
                values["record_id"],
                values.get("api_key_id"),
                values.get("actor_user_id"),
                values.get("actor_org_id"),
                values["details_json"],
                values["created_at"],
            ),
        )


class ReviewRefreshRepository:
    """Portable SQL for append-only review decisions and refresh work."""

    def __init__(self, conn: DbConnection):
        self.conn = conn

    def get_idempotency(
        self,
        namespace_kind: str,
        namespace_id: str,
        operation: str,
        key: str,
    ) -> Any | None:
        return self.conn.execute(
            """
            SELECT * FROM idempotency_keys
            WHERE namespace_kind = ? AND namespace_id = ?
              AND operation = ? AND "key" = ?
            """,
            (namespace_kind, namespace_id, operation, key),
        ).fetchone()

    def reserve_idempotency(
        self,
        *,
        namespace_kind: str,
        namespace_id: str,
        operation: str,
        key: str,
        request_sha256: str,
        reservation_json: str,
        created_at: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO idempotency_keys (
                namespace_kind, namespace_id, operation, "key", request_sha256,
                response_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(namespace_kind, namespace_id, operation, "key")
            DO NOTHING
            """,
            (
                namespace_kind,
                namespace_id,
                operation,
                key,
                request_sha256,
                reservation_json,
                created_at,
            ),
        )

    def complete_idempotency(
        self,
        *,
        namespace_kind: str,
        namespace_id: str,
        operation: str,
        key: str,
        reservation_json: str,
        response_json: str,
    ) -> None:
        cursor = self.conn.execute(
            """
            UPDATE idempotency_keys
            SET response_json = ?
            WHERE namespace_kind = ? AND namespace_id = ?
              AND operation = ? AND "key" = ?
              AND response_json = ?
            """,
            (
                response_json,
                namespace_kind,
                namespace_id,
                operation,
                key,
                reservation_json,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("idempotency reservation was not completed")

    def release_idempotency(
        self,
        *,
        namespace_kind: str,
        namespace_id: str,
        operation: str,
        key: str,
        reservation_json: str,
    ) -> None:
        self.conn.execute(
            """
            DELETE FROM idempotency_keys
            WHERE namespace_kind = ? AND namespace_id = ?
              AND operation = ? AND "key" = ?
              AND response_json = ?
            """,
            (
                namespace_kind,
                namespace_id,
                operation,
                key,
                reservation_json,
            ),
        )

    def get_claim_for_revision(
        self,
        revision_id: str,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> Any | None:
        suffix = " FOR UPDATE" if self.conn.target.kind == "postgres" else ""
        return self.conn.execute(
            """
            SELECT
                c.id AS claim_id,
                c.current_revision_id,
                c.review_state,
                c.conflict_state,
                c.human_reviewed,
                c.session_id,
                c.topic_id,
                c.canonical_key,
                c.scope_json,
                c.created_at AS claim_created_at,
                cr.id AS revision_id,
                cr.revision_number,
                cr.title,
                cr.statement,
                cr.status AS revision_status,
                cr.confidence,
                cr.valid_from,
                cr.valid_until,
                cr.created_by_model,
                cr.metadata_json
            FROM claims c
            JOIN claim_revisions cr ON cr.claim_id = c.id
            WHERE cr.id = ? AND c.namespace_kind = ? AND c.namespace_id = ?
            """
            + suffix,
            (revision_id, namespace_kind, namespace_id),
        ).fetchone()

    def get_claim(
        self,
        claim_id: str,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> Any | None:
        suffix = " FOR UPDATE" if self.conn.target.kind == "postgres" else ""
        return self.conn.execute(
            """
            SELECT
                c.id AS claim_id,
                c.current_revision_id,
                c.review_state,
                c.conflict_state,
                c.human_reviewed,
                c.session_id,
                c.topic_id,
                c.canonical_key,
                c.scope_json,
                c.created_at AS claim_created_at,
                cr.id AS revision_id,
                cr.revision_number,
                cr.title,
                cr.statement,
                cr.status AS revision_status,
                cr.confidence,
                cr.valid_from,
                cr.valid_until,
                cr.created_by_model,
                cr.metadata_json
            FROM claims c
            JOIN claim_revisions cr ON cr.id = c.current_revision_id
            WHERE c.id = ? AND c.namespace_kind = ? AND c.namespace_id = ?
            """
            + suffix,
            (claim_id, namespace_kind, namespace_id),
        ).fetchone()

    def next_claim_revision_number(self, claim_id: str) -> int:
        row = self.conn.execute(
            """
            SELECT COALESCE(MAX(revision_number), 0) + 1 AS number
            FROM claim_revisions WHERE claim_id = ?
            """,
            (claim_id,),
        ).fetchone()
        return int(row["number"])

    def insert_claim_revision(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO claim_revisions (
                id, claim_id, revision_number, title, statement, status,
                confidence, valid_from, valid_until, supersedes_revision_id,
                created_by_model, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["id"],
                values["claim_id"],
                values["revision_number"],
                values["title"],
                values["statement"],
                values["status"],
                values["confidence"],
                values["valid_from"],
                values["valid_until"],
                values["supersedes_revision_id"],
                values["created_by_model"],
                values["created_at"],
                values["metadata_json"],
            ),
        )

    def copy_claim_evidence(
        self, *, from_revision_id: str, to_revision_id: str
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO claim_evidence (
                claim_revision_id, evidence_span_id, relationship, rationale,
                weight, review_state, created_at
            )
            SELECT ?, evidence_span_id, relationship, rationale, weight,
                   review_state, created_at
            FROM claim_evidence
            WHERE claim_revision_id = ?
            """,
            (to_revision_id, from_revision_id),
        )

    def update_claim_current(
        self,
        *,
        claim_id: str,
        expected_revision_id: str,
        revision_id: str,
        title: str,
        statement: str,
        legacy_status: str,
        confidence: float,
        review_state: str,
        conflict_state: str,
        updated_at: str,
    ) -> None:
        cursor = self.conn.execute(
            """
            UPDATE claims
            SET current_revision_id = ?, title = ?, statement = ?, status = ?,
                confidence = ?, review_state = ?, conflict_state = ?,
                human_reviewed = ?, updated_at = ?
            WHERE id = ? AND current_revision_id = ?
            """,
            (
                revision_id,
                title,
                statement,
                legacy_status,
                confidence,
                review_state,
                conflict_state,
                int(review_state == "reviewed"),
                updated_at,
                claim_id,
                expected_revision_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("EXPECTED_REVISION_MISMATCH")

    def update_claim_review_state(
        self,
        *,
        claim_id: str,
        expected_revision_id: str,
        review_state: str,
        conflict_state: str,
        updated_at: str,
    ) -> None:
        cursor = self.conn.execute(
            """
            UPDATE claims
            SET review_state = ?, conflict_state = ?, human_reviewed = ?,
                updated_at = ?
            WHERE id = ? AND current_revision_id = ?
            """,
            (
                review_state,
                conflict_state,
                int(review_state == "reviewed"),
                updated_at,
                claim_id,
                expected_revision_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("EXPECTED_REVISION_MISMATCH")

    def current_claim_has_refuting_evidence(self, claim_id: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 AS present
            FROM claims c
            JOIN claim_evidence ce
              ON ce.claim_revision_id = c.current_revision_id
            WHERE c.id = ? AND ce.relationship = 'refutes'
            LIMIT 1
            """,
            (claim_id,),
        ).fetchone()
        return row is not None

    def claim_revision_has_refuting_evidence(self, revision_id: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 AS present
            FROM claim_evidence
            WHERE claim_revision_id = ? AND relationship = 'refutes'
            LIMIT 1
            """,
            (revision_id,),
        ).fetchone()
        return row is not None

    def claim_freshness(self, claim_id: str) -> str:
        stale = self.conn.execute(
            """
            SELECT 1 AS present
            FROM claims c
            JOIN claim_evidence ce
              ON ce.claim_revision_id = c.current_revision_id
            JOIN evidence_spans e ON e.id = ce.evidence_span_id
            WHERE c.id = ? AND e.anchor_state = 'stale'
            LIMIT 1
            """,
            (claim_id,),
        ).fetchone()
        if stale is not None:
            return "stale"
        failed = self.conn.execute(
            """
            SELECT 1 AS present
            FROM refresh_queue rq
            WHERE rq.status = 'failed' AND (
                (rq.entity_kind = 'claim' AND rq.entity_id = ?)
                OR (
                    rq.entity_kind = 'evidence'
                    AND EXISTS (
                        SELECT 1 FROM claims c
                        JOIN claim_evidence ce
                          ON ce.claim_revision_id = c.current_revision_id
                        WHERE c.id = ? AND ce.evidence_span_id = rq.entity_id
                    )
                )
            )
            LIMIT 1
            """,
            (claim_id, claim_id),
        ).fetchone()
        if failed is not None:
            return "stale"
        pending = self.conn.execute(
            """
            SELECT 1 AS present
            FROM refresh_queue rq
            WHERE rq.status IN ('pending', 'running') AND (
                (rq.entity_kind = 'claim' AND rq.entity_id = ?)
                OR (
                    rq.entity_kind = 'evidence'
                    AND EXISTS (
                        SELECT 1 FROM claims c
                        JOIN claim_evidence ce
                          ON ce.claim_revision_id = c.current_revision_id
                        WHERE c.id = ? AND ce.evidence_span_id = rq.entity_id
                    )
                )
            )
            LIMIT 1
            """,
            (claim_id, claim_id),
        ).fetchone()
        if pending is not None:
            return "needs_refresh"
        row = self.conn.execute(
            """
            SELECT COALESCE(rs.freshness_state, 'fresh') AS freshness
            FROM claims c
            LEFT JOIN research_sessions rs ON rs.id = c.session_id
            WHERE c.id = ?
            """,
            (claim_id,),
        ).fetchone()
        value = row["freshness"] if row is not None else "unknown"
        return value if value in {"fresh", "needs_refresh", "stale", "unknown"} else "unknown"

    def insert_review_event(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO review_events (
                id, entity_kind, entity_id, action, from_state, to_state,
                note, actor_type, actor_id, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["id"],
                values["entity_kind"],
                values["entity_id"],
                values["action"],
                values["from_state"],
                values["to_state"],
                values["note"],
                values["actor_type"],
                values["actor_id"],
                values["created_at"],
                values["metadata_json"],
            ),
        )

    def get_source_capture_context(
        self,
        source_id: str,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> tuple[Any, Any | None] | None:
        source = self.conn.execute(
            """
            SELECT * FROM sources
            WHERE id = ? AND namespace_kind = ? AND namespace_id = ?
            """,
            (source_id, namespace_kind, namespace_id),
        ).fetchone()
        if source is None:
            return None
        version = self.conn.execute(
            """
            SELECT * FROM source_versions
            WHERE source_id = ?
            ORDER BY retrieved_at DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            (source_id,),
        ).fetchone()
        return source, version

    def get_source_id_for_evidence(
        self,
        evidence_id: str,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> str | None:
        row = self.conn.execute(
            """
            SELECT sv.source_id
            FROM evidence_spans e
            JOIN source_versions sv ON sv.id = e.source_version_id
            JOIN sources s ON s.id = sv.source_id
            WHERE e.id = ? AND s.namespace_kind = ? AND s.namespace_id = ?
            """,
            (evidence_id, namespace_kind, namespace_id),
        ).fetchone()
        return row["source_id"] if row is not None else None

    def list_evidence_for_version(self, source_version_id: str) -> list[Any]:
        return self.conn.execute(
            """
            SELECT * FROM evidence_spans
            WHERE source_version_id = ?
            ORDER BY id
            """,
            (source_version_id,),
        ).fetchall()

    def insert_reanchored_evidence(self, values: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO evidence_spans (
                id, source_version_id, topic_id, question_id, session_id,
                quote_text, quote_sha256, selector_type, selector_json, note,
                confidence, anchor_state, review_state, trust_tier,
                created_by_model, created_at, last_resolved_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["id"],
                values["source_version_id"],
                values["topic_id"],
                values["question_id"],
                values["session_id"],
                values["quote_text"],
                values["quote_sha256"],
                values["selector_type"],
                values["selector_json"],
                values["note"],
                values["confidence"],
                values["anchor_state"],
                values["review_state"],
                values["trust_tier"],
                values["created_by_model"],
                values["created_at"],
                values["last_resolved_at"],
                values["metadata_json"],
            ),
        )

    def get_reviewable_entity(
        self,
        *,
        kind: str,
        entity_id: str,
        namespace_kind: str,
        namespace_id: str,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if self.conn.target.kind == "postgres" else ""
        if kind == "evidence":
            row = self.conn.execute(
                """
                SELECT e.*, s.namespace_kind, s.namespace_id
                FROM evidence_spans e
                JOIN source_versions sv ON sv.id = e.source_version_id
                JOIN sources s ON s.id = sv.source_id
                WHERE e.id = ? AND s.namespace_kind = ? AND s.namespace_id = ?
                """
                + suffix,
                (entity_id, namespace_kind, namespace_id),
            ).fetchone()
            if row is None:
                return None
            metadata = _json_object(row["metadata_json"])
            return {
                "event_kind": "evidence",
                "event_id": entity_id,
                "queue_kind": "evidence",
                "queue_id": entity_id,
                "review_state": self._latest_review_state(
                    "evidence", entity_id, row["review_state"]
                ),
                "legacy_table": "excerpts",
                "legacy_id": metadata.get("v1_excerpt_id")
                or metadata.get("legacy_excerpt_id"),
            }
        if kind == "source_version":
            row = self.conn.execute(
                """
                SELECT sv.id, sv.source_id, s.review_state,
                       s.namespace_kind, s.namespace_id
                FROM source_versions sv
                JOIN sources s ON s.id = sv.source_id
                WHERE sv.id = ? AND s.namespace_kind = ? AND s.namespace_id = ?
                """
                + suffix,
                (entity_id, namespace_kind, namespace_id),
            ).fetchone()
            if row is None:
                return None
            return {
                "event_kind": "source_version",
                "event_id": entity_id,
                "queue_kind": "source",
                "queue_id": row["source_id"],
                "review_state": self._latest_review_state(
                    "source_version", entity_id, row["review_state"]
                ),
                "legacy_table": "sources",
                "legacy_id": row["source_id"],
            }
        if kind == "report":
            row = self.conn.execute(
                """
                SELECT id, review_state FROM reports
                WHERE id = ? AND namespace_kind = ? AND namespace_id = ?
                """
                + suffix,
                (entity_id, namespace_kind, namespace_id),
            ).fetchone()
            if row is None:
                return None
            return {
                "event_kind": "report",
                "event_id": entity_id,
                "queue_kind": "report",
                "queue_id": entity_id,
                "review_state": self._latest_review_state(
                    "report", entity_id, row["review_state"]
                ),
                "legacy_table": "reports",
                "legacy_id": entity_id,
            }
        return None

    def update_legacy_review_mirror(
        self,
        *,
        table: str,
        record_id: str | None,
        review_state: str,
        conflict_state: str,
    ) -> None:
        if record_id is None:
            return
        if table not in {"sources", "excerpts", "reports"}:
            raise ValueError("unsupported review mirror")
        self.conn.execute(
            f"""
            UPDATE {table}
            SET review_state = ?, conflict_state = ?, human_reviewed = ?
            WHERE id = ?
            """,
            (
                review_state,
                conflict_state,
                int(review_state == "reviewed"),
                record_id,
            ),
        )

    def resolve_refresh_root(
        self,
        *,
        kind: str,
        entity_id: str,
        namespace_kind: str,
        namespace_id: str,
    ) -> tuple[str, str] | None:
        if kind == "source":
            row = self.conn.execute(
                """
                SELECT id FROM sources
                WHERE id = ? AND namespace_kind = ? AND namespace_id = ?
                """,
                (entity_id, namespace_kind, namespace_id),
            ).fetchone()
            return ("source", entity_id) if row is not None else None
        if kind == "source_version":
            row = self.conn.execute(
                """
                SELECT sv.source_id
                FROM source_versions sv
                JOIN sources s ON s.id = sv.source_id
                WHERE sv.id = ? AND s.namespace_kind = ? AND s.namespace_id = ?
                """,
                (entity_id, namespace_kind, namespace_id),
            ).fetchone()
            return ("source", row["source_id"]) if row is not None else None
        if kind == "evidence":
            row = self.conn.execute(
                """
                SELECT e.id
                FROM evidence_spans e
                JOIN source_versions sv ON sv.id = e.source_version_id
                JOIN sources s ON s.id = sv.source_id
                WHERE e.id = ? AND s.namespace_kind = ? AND s.namespace_id = ?
                """,
                (entity_id, namespace_kind, namespace_id),
            ).fetchone()
            return ("evidence", entity_id) if row is not None else None
        if kind == "claim":
            row = self.conn.execute(
                """
                SELECT id FROM claims
                WHERE id = ? AND namespace_kind = ? AND namespace_id = ?
                """,
                (entity_id, namespace_kind, namespace_id),
            ).fetchone()
            return ("claim", entity_id) if row is not None else None
        if kind == "report":
            row = self.conn.execute(
                """
                SELECT id FROM reports
                WHERE id = ? AND namespace_kind = ? AND namespace_id = ?
                """,
                (entity_id, namespace_kind, namespace_id),
            ).fetchone()
            return ("report", entity_id) if row is not None else None
        if kind == "refresh_item":
            item = self.get_refresh_item(
                entity_id,
                namespace_kind=namespace_kind,
                namespace_id=namespace_id,
            )
            if item is None:
                return None
            return (item["entity_kind"], item["entity_id"])
        return None

    def expand_refresh_targets(
        self, root_kind: str, root_id: str
    ) -> list[tuple[str, str]]:
        targets: list[tuple[str, str]] = [(root_kind, root_id)]
        evidence_ids: list[str] = []
        if root_kind == "source":
            evidence_ids = [
                row["id"]
                for row in self.conn.execute(
                    """
                    SELECT e.id
                    FROM evidence_spans e
                    JOIN source_versions sv ON sv.id = e.source_version_id
                    WHERE sv.source_id = ?
                    ORDER BY e.id
                    """,
                    (root_id,),
                ).fetchall()
            ]
            targets.extend(("evidence", item) for item in evidence_ids)
        elif root_kind == "evidence":
            evidence_ids = [root_id]

        claim_ids: list[str] = []
        if evidence_ids:
            claim_ids = [
                row["id"]
                for row in self.conn.execute(
                    """
                    SELECT DISTINCT c.id
                    FROM claims c
                    JOIN claim_evidence ce
                      ON ce.claim_revision_id = c.current_revision_id
                    WHERE ce.evidence_span_id IN (
                    """
                    + ",".join("?" for _ in evidence_ids)
                    + ") ORDER BY c.id",
                    tuple(evidence_ids),
                ).fetchall()
            ]
            targets.extend(("claim", item) for item in claim_ids)
        elif root_kind == "claim":
            claim_ids = [root_id]

        if claim_ids:
            report_ids = [
                row["id"]
                for row in self.conn.execute(
                    """
                    SELECT DISTINCT r.id
                    FROM reports r
                    JOIN report_claims rc ON rc.report_id = r.id
                    WHERE rc.claim_id IN (
                    """
                    + ",".join("?" for _ in claim_ids)
                    + ") ORDER BY r.id",
                    tuple(claim_ids),
                ).fetchall()
            ]
            targets.extend(("report", item) for item in report_ids)
        return list(dict.fromkeys(targets))

    def enqueue_refresh(
        self,
        *,
        refresh_id: str,
        entity_kind: str,
        entity_id: str,
        reason: str,
        priority: float,
        detected_at: str,
        details_json: str,
    ) -> tuple[Any, bool]:
        cursor = self.conn.execute(
            """
            INSERT INTO refresh_queue (
                id, entity_kind, entity_id, reason, status, priority,
                detected_at, details_json
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (
                refresh_id,
                entity_kind,
                entity_id,
                reason,
                priority,
                detected_at,
                details_json,
            ),
        )
        created = cursor.rowcount == 1
        row = self.conn.execute(
            """
            SELECT * FROM refresh_queue
            WHERE entity_kind = ? AND entity_id = ? AND reason = ?
              AND status IN ('pending', 'running')
            ORDER BY detected_at, id
            LIMIT 1
            """,
            (entity_kind, entity_id, reason),
        ).fetchone()
        if row is None:
            raise RuntimeError("pending refresh item could not be resolved")
        return row, created

    def get_refresh_item(
        self,
        refresh_id: str,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> Any | None:
        suffix = " FOR UPDATE" if self.conn.target.kind == "postgres" else ""
        row = self.conn.execute(
            "SELECT * FROM refresh_queue WHERE id = ?" + suffix,
            (refresh_id,),
        ).fetchone()
        if row is None:
            return None
        root = self.resolve_refresh_root(
            kind=row["entity_kind"],
            entity_id=row["entity_id"],
            namespace_kind=namespace_kind,
            namespace_id=namespace_id,
        )
        return row if root is not None else None

    def dismiss_refresh(
        self,
        *,
        refresh_id: str,
        expected_state: str,
        resolved_at: str,
    ) -> Any:
        cursor = self.conn.execute(
            """
            UPDATE refresh_queue
            SET status = 'dismissed', resolved_at = ?
            WHERE id = ? AND status = ?
            """,
            (resolved_at, refresh_id, expected_state),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("EXPECTED_STATE_MISMATCH")
        return self.conn.execute(
            "SELECT * FROM refresh_queue WHERE id = ?",
            (refresh_id,),
        ).fetchone()

    def review_event_target_for_refresh(
        self, entity_kind: str, entity_id: str
    ) -> tuple[str, str] | None:
        if entity_kind in {"evidence", "report"}:
            return (entity_kind, entity_id)
        if entity_kind == "claim":
            row = self.conn.execute(
                "SELECT current_revision_id FROM claims WHERE id = ?",
                (entity_id,),
            ).fetchone()
            if row is not None and row["current_revision_id"]:
                return ("claim_revision", row["current_revision_id"])
            return None
        if entity_kind == "source":
            row = self.conn.execute(
                """
                SELECT id FROM source_versions
                WHERE source_id = ?
                ORDER BY retrieved_at DESC, created_at DESC, id DESC
                LIMIT 1
                """,
                (entity_id,),
            ).fetchone()
            if row is not None:
                return ("source_version", row["id"])
        return None

    def _latest_review_state(
        self, entity_kind: str, entity_id: str, fallback: str
    ) -> str:
        row = self.conn.execute(
            """
            SELECT to_state FROM review_events
            WHERE entity_kind = ? AND entity_id = ?
              AND action IN ('approve', 'contest', 'reject', 'supersede')
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (entity_kind, entity_id),
        ).fetchone()
        return row["to_state"] if row is not None else fallback


class SourceVersionRepository:
    """SQL boundary for immutable content objects and source versions."""

    def __init__(self, conn: DbConnection):
        self.conn = conn

    def find_content_by_sha256(
        self,
        content_sha256: str,
    ) -> ContentObjectRecord | None:
        row = self.conn.execute(
            "SELECT * FROM content_objects WHERE sha256 = ?",
            (content_sha256,),
        ).fetchone()
        return self._content_from_row(row) if row is not None else None

    def get_content_object(self, content_object_id: str) -> ContentObjectRecord:
        row = self.conn.execute(
            "SELECT * FROM content_objects WHERE id = ?",
            (content_object_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"content_object:{content_object_id} not found")
        return self._content_from_row(row)

    def insert_content_object(self, record: ContentObjectRecord) -> None:
        self.conn.execute(
            """
            INSERT INTO content_objects (
                id, sha256, storage_backend, storage_key, media_type,
                byte_count, compression, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.sha256,
                record.storage_backend,
                record.storage_key,
                record.media_type,
                record.byte_count,
                record.compression,
                record.created_at,
                canonical_json(record.metadata),
            ),
        )

    def find_by_source_and_key(
        self,
        source_id: str,
        version_key: str,
    ) -> SourceVersionRecord | None:
        row = self.conn.execute(
            """
            SELECT * FROM source_versions
            WHERE source_id = ? AND version_key = ?
            """,
            (source_id, version_key),
        ).fetchone()
        return self._source_version_from_row(row) if row is not None else None

    def get_source_version(self, source_version_id: str) -> SourceVersionRecord:
        row = self.conn.execute(
            "SELECT * FROM source_versions WHERE id = ?",
            (source_version_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"source_version:{source_version_id} not found")
        return self._source_version_from_row(row)

    def insert_source_version(self, record: SourceVersionRecord) -> None:
        self.conn.execute(
            """
            INSERT INTO source_versions (
                id, source_id, version_key, version_kind, retrieved_at,
                published_at, content_sha256, content_object_id, media_type,
                byte_count, parser_name, parser_version, canonical_locator,
                repository_locator, commit_sha, blob_sha, path, metadata_json,
                created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                record.id,
                record.source_id,
                record.version_key,
                record.version_kind,
                record.retrieved_at,
                record.published_at,
                record.content_sha256,
                record.content_object_id,
                record.media_type,
                record.byte_count,
                record.parser_name,
                record.parser_version,
                record.canonical_locator,
                record.repository_locator,
                record.commit_sha,
                record.blob_sha,
                record.path,
                canonical_json(record.metadata),
                record.created_at,
            ),
        )

    def list_referenced_blobs(self) -> list[BlobReference]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT
                co.sha256, co.storage_key, co.byte_count, co.media_type
            FROM content_objects co
            JOIN source_versions sv ON sv.content_object_id = co.id
            WHERE co.storage_backend = 'filesystem'
            ORDER BY co.storage_key
            """
        ).fetchall()
        return [
            BlobReference(
                sha256=row["sha256"],
                storage_key=row["storage_key"],
                byte_count=int(row["byte_count"]),
                media_type=row["media_type"],
            )
            for row in rows
        ]

    def blob_reference_error_count(self) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM source_versions sv
            LEFT JOIN content_objects co ON co.id = sv.content_object_id
            WHERE sv.content_object_id IS NOT NULL
              AND (
                  co.id IS NULL
                  OR co.storage_backend <> 'filesystem'
                  OR co.sha256 <> sv.content_sha256
                  OR co.byte_count <> sv.byte_count
                  OR (
                      co.media_type <> sv.media_type
                      OR (co.media_type IS NULL AND sv.media_type IS NOT NULL)
                      OR (co.media_type IS NOT NULL AND sv.media_type IS NULL)
                  )
              )
            """
        ).fetchone()
        return int(row["count"])

    @staticmethod
    def _content_from_row(row: Any) -> ContentObjectRecord:
        return ContentObjectRecord(
            id=row["id"],
            sha256=row["sha256"],
            storage_backend=row["storage_backend"],
            storage_key=row["storage_key"],
            media_type=row["media_type"],
            byte_count=int(row["byte_count"]),
            compression=row["compression"],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _source_version_from_row(row: Any) -> SourceVersionRecord:
        return SourceVersionRecord(
            id=row["id"],
            source_id=row["source_id"],
            version_key=row["version_key"],
            version_kind=row["version_kind"],
            retrieved_at=row["retrieved_at"],
            published_at=row["published_at"],
            content_sha256=row["content_sha256"],
            canonical_locator=row["canonical_locator"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            content_object_id=row["content_object_id"],
            media_type=row["media_type"],
            byte_count=(
                int(row["byte_count"]) if row["byte_count"] is not None else None
            ),
            parser_name=row["parser_name"],
            parser_version=row["parser_version"],
            repository_locator=row["repository_locator"],
            commit_sha=row["commit_sha"],
            blob_sha=row["blob_sha"],
            path=row["path"],
        )


class V2BackfillRepository:
    def __init__(self, conn: DbConnection, *, now_text: str | None = None):
        self.conn = conn
        self.now_text = (
            utc_text(datetime.fromisoformat(now_text.replace("Z", "+00:00")))
            if now_text is not None
            else utc_text(datetime.now(timezone.utc).replace(microsecond=0))
        )

    def initialize_progress(
        self, phases: Iterable[str], *, updated_at: str
    ) -> None:
        for phase in phases:
            self.conn.execute(
                """
                INSERT INTO migration_backfill_progress (
                    migration_id, phase, processed_count, warning_count,
                    error_count, status, updated_at
                ) VALUES (?, ?, 0, 0, 0, 'pending', ?)
                ON CONFLICT(migration_id, phase) DO NOTHING
                """,
                (V2_MIGRATION_ID, phase, updated_at),
            )

    def adopt_authoritative_projection_identities(self) -> None:
        """Persist explicit identities for v2-alpha rows created before 0006."""
        sources = self.conn.execute(
            """
            SELECT s.id
            FROM sources s
            WHERE EXISTS (
                SELECT 1
                FROM source_versions sv
                WHERE sv.source_id = s.id
                  AND sv.version_kind <> 'migration'
            )
            ORDER BY s.id
            """
        ).fetchall()
        for source in sources:
            version = self.conn.execute(
                """
                SELECT id
                FROM source_versions
                WHERE source_id = ? AND version_kind <> 'migration'
                ORDER BY created_at, id
                LIMIT 1
                """,
                (source["id"],),
            ).fetchone()
            assert version is not None
            self.record_projection_identity(
                "source",
                source["id"],
                "source_version",
                version["id"],
                update_existing=False,
            )

        evidence_rows = self.conn.execute(
            """
            SELECT id, metadata_json
            FROM evidence_spans
            ORDER BY created_at, id
            """
        ).fetchall()
        for evidence in evidence_rows:
            legacy_excerpt_id = _json_object(evidence["metadata_json"]).get(
                "v1_excerpt_id"
            )
            if not isinstance(legacy_excerpt_id, str):
                continue
            if (
                self.conn.execute(
                    "SELECT id FROM excerpts WHERE id = ?",
                    (legacy_excerpt_id,),
                ).fetchone()
                is None
            ):
                continue
            self.record_projection_identity(
                "excerpt",
                legacy_excerpt_id,
                "evidence",
                evidence["id"],
                update_existing=False,
            )

        claims = self.conn.execute(
            """
            SELECT id, current_revision_id
            FROM claims
            WHERE current_revision_id IS NOT NULL
            ORDER BY id
            """
        ).fetchall()
        for claim in claims:
            self.record_projection_identity(
                "claim",
                claim["id"],
                "claim_revision",
                claim["current_revision_id"],
                update_existing=False,
            )

        receipts = self.conn.execute(
            """
            SELECT response_json FROM idempotency_keys
            WHERE operation = 'research_deposit_v2'
            ORDER BY created_at
            """
        ).fetchall()
        for receipt in receipts:
            records = _json_object(receipt["response_json"]).get("records")
            if not isinstance(records, dict):
                continue
            self._adopt_receipt_identity_pairs(
                records.get("source_ids"),
                records.get("source_version_ids"),
                legacy_kind="source",
                v2_kind="source_version",
                target_table="source_versions",
                target_parent_column="source_id",
            )
            self._adopt_receipt_identity_pairs(
                records.get("claim_ids"),
                records.get("claim_revision_ids"),
                legacy_kind="claim",
                v2_kind="claim_revision",
                target_table="claim_revisions",
                target_parent_column="claim_id",
            )
            report_id = (
                records.get("report_id")
            )
            if not isinstance(report_id, str):
                continue
            if (
                self.conn.execute(
                    "SELECT id FROM reports WHERE id = ?", (report_id,)
                ).fetchone()
                is None
            ):
                continue
            self.record_projection_identity(
                "report",
                report_id,
                "report",
                report_id,
                update_existing=False,
            )

    def _adopt_receipt_identity_pairs(
        self,
        legacy_ids: Any,
        v2_ids: Any,
        *,
        legacy_kind: str,
        v2_kind: str,
        target_table: str,
        target_parent_column: str,
    ) -> None:
        if not isinstance(legacy_ids, dict) or not isinstance(v2_ids, dict):
            return
        for client_ref, legacy_id in legacy_ids.items():
            v2_id = v2_ids.get(client_ref)
            if not isinstance(legacy_id, str) or not isinstance(v2_id, str):
                continue
            target = self.conn.execute(
                f"""
                SELECT metadata_json
                    {", revision_number" if legacy_kind == "claim" else ""}
                FROM {target_table}
                WHERE id = ? AND {target_parent_column} = ?
                """,
                (v2_id, legacy_id),
            ).fetchone()
            if target is None:
                continue
            if legacy_kind != "claim":
                self.record_projection_identity(
                    legacy_kind,
                    legacy_id,
                    v2_kind,
                    v2_id,
                    update_existing=True,
                )
                continue
            current = self.conn.execute(
                """
                SELECT c.current_revision_id, cr.metadata_json,
                       cr.revision_number
                FROM claims c
                LEFT JOIN claim_revisions cr ON cr.id = c.current_revision_id
                WHERE c.id = ?
                """,
                (legacy_id,),
            ).fetchone()
            chosen_id = v2_id
            if current is not None and current["current_revision_id"] is not None:
                current_metadata = _json_object(current["metadata_json"])
                if current_metadata.get("review_action") is not None:
                    chosen_id = current["current_revision_id"]
                elif (
                    current_metadata.get("migration_id") != V2_MIGRATION_ID
                    and int(current["revision_number"] or 0)
                    >= int(target["revision_number"])
                ):
                    chosen_id = current["current_revision_id"]
            self.record_projection_identity(
                legacy_kind,
                legacy_id,
                v2_kind,
                chosen_id,
                update_existing=True,
            )
            if (
                current is not None
                and current["current_revision_id"] != chosen_id
                and _json_object(current["metadata_json"]).get("review_action")
                is None
            ):
                self.conn.execute(
                    "UPDATE claims SET current_revision_id = ? WHERE id = ?",
                    (chosen_id, legacy_id),
                )

    def record_projection_identity(
        self,
        legacy_kind: str,
        legacy_id: str,
        v2_kind: str,
        v2_id: str,
        *,
        update_existing: bool,
    ) -> None:
        conflict_action = (
            """
            DO UPDATE SET
                v2_kind = excluded.v2_kind,
                v2_id = excluded.v2_id,
                updated_at = excluded.updated_at
            """
            if update_existing
            else "DO NOTHING"
        )
        self.conn.execute(
            f"""
            INSERT INTO legacy_projection_identity (
                legacy_kind, legacy_id, v2_kind, v2_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(legacy_kind, legacy_id) {conflict_action}
            """,
            (
                legacy_kind,
                legacy_id,
                v2_kind,
                v2_id,
                self.now_text,
                self.now_text,
            ),
        )

    def projection_target(
        self,
        legacy_kind: str,
        legacy_id: str,
        v2_kind: str,
    ) -> str | None:
        row = self.conn.execute(
            """
            SELECT v2_id
            FROM legacy_projection_identity
            WHERE legacy_kind = ? AND legacy_id = ? AND v2_kind = ?
            """,
            (legacy_kind, legacy_id, v2_kind),
        ).fetchone()
        return row["v2_id"] if row is not None else None

    def progress(self) -> list[Any]:
        return self.conn.execute(
            """
            SELECT * FROM migration_backfill_progress
            WHERE migration_id = ?
            ORDER BY phase
            """,
            (V2_MIGRATION_ID,),
        ).fetchall()

    def phase_progress(self, phase: str) -> Any:
        return self.conn.execute(
            """
            SELECT * FROM migration_backfill_progress
            WHERE migration_id = ? AND phase = ?
            """,
            (V2_MIGRATION_ID, phase),
        ).fetchone()

    def reset_completed_progress(self, *, updated_at: str) -> None:
        self.conn.execute(
            """
            UPDATE migration_backfill_progress
            SET last_legacy_id = NULL,
                last_related_id = NULL,
                processed_count = 0,
                warning_count = 0,
                error_count = 0,
                status = 'pending',
                updated_at = ?
            WHERE migration_id = ?
            """,
            (updated_at, V2_MIGRATION_ID),
        )

    def fetch_batch(
        self,
        phase: str,
        *,
        last_legacy_id: str | None,
        last_related_id: str | None,
        batch_size: int,
    ) -> list[Any]:
        last_id = last_legacy_id or ""
        if phase == "source_versions":
            return self.conn.execute(
                "SELECT * FROM sources WHERE id > ? ORDER BY id LIMIT ?",
                (last_id, batch_size),
            ).fetchall()
        if phase == "evidence_spans":
            return self.conn.execute(
                """
                SELECT e.*, rs.freshness_state AS session_freshness_state
                FROM excerpts e
                LEFT JOIN research_sessions rs ON rs.id = e.session_id
                WHERE e.id > ?
                ORDER BY e.id
                LIMIT ?
                """,
                (last_id, batch_size),
            ).fetchall()
        if phase == "claim_revisions":
            return self.conn.execute(
                """
                SELECT c.*, rs.freshness_state AS session_freshness_state
                FROM claims c
                LEFT JOIN research_sessions rs ON rs.id = c.session_id
                WHERE c.id > ?
                ORDER BY c.id
                LIMIT ?
                """,
                (last_id, batch_size),
            ).fetchall()
        if phase == "claim_evidence":
            last_related = last_related_id or ""
            return self.conn.execute(
                """
                SELECT ce.*, c.created_at AS claim_created_at
                FROM claim_excerpts ce
                JOIN claims c ON c.id = ce.claim_id
                WHERE ce.claim_id > ?
                   OR (ce.claim_id = ? AND ce.excerpt_id > ?)
                ORDER BY ce.claim_id, ce.excerpt_id
                LIMIT ?
                """,
                (last_id, last_id, last_related, batch_size),
            ).fetchall()
        if phase == "claim_pointers":
            return self.conn.execute(
                "SELECT * FROM claims WHERE id > ? ORDER BY id LIMIT ?",
                (last_id, batch_size),
            ).fetchall()
        if phase == "report_state":
            return self.conn.execute(
                "SELECT * FROM reports WHERE id > ? ORDER BY id LIMIT ?",
                (last_id, batch_size),
            ).fetchall()
        raise ValueError(f"unsupported v2 backfill phase: {phase}")

    def process_row(self, phase: str, row: Any) -> tuple[int, int]:
        if phase == "source_versions":
            return self._process_source(row)
        if phase == "evidence_spans":
            return self._process_evidence(row)
        if phase == "claim_revisions":
            return self._process_claim(row)
        if phase == "claim_evidence":
            return self._process_claim_evidence(row)
        if phase == "claim_pointers":
            return self._process_claim_pointer(row)
        if phase == "report_state":
            return self._process_report_state(row)
        raise ValueError(f"unsupported v2 backfill phase: {phase}")

    def project_legacy_write(self, kind: str, record_id: str) -> None:
        """Idempotently dual-project one retained v1 mutation into v2."""
        if kind == "source":
            row = self.conn.execute(
                "SELECT * FROM sources WHERE id = ?", (record_id,)
            ).fetchone()
            if row is not None:
                self._process_source(row)
            return
        if kind == "excerpt":
            row = self.conn.execute(
                """
                SELECT e.*, rs.freshness_state AS session_freshness_state
                FROM excerpts e
                LEFT JOIN research_sessions rs ON rs.id = e.session_id
                WHERE e.id = ?
                """,
                (record_id,),
            ).fetchone()
            if row is not None:
                self._process_evidence(row)
            return
        if kind == "claim":
            row = self.conn.execute(
                """
                SELECT c.*, rs.freshness_state AS session_freshness_state
                FROM claims c
                LEFT JOIN research_sessions rs ON rs.id = c.session_id
                WHERE c.id = ?
                """,
                (record_id,),
            ).fetchone()
            if row is None:
                return
            self._process_claim(row)
            links = self.conn.execute(
                """
                SELECT ce.*, c.created_at AS claim_created_at
                FROM claim_excerpts ce
                JOIN claims c ON c.id = ce.claim_id
                WHERE ce.claim_id = ?
                ORDER BY ce.excerpt_id
                """,
                (record_id,),
            ).fetchall()
            for link in links:
                self._process_claim_evidence(link)
            self._process_claim_pointer(row)
            return
        if kind == "report":
            row = self.conn.execute(
                "SELECT * FROM reports WHERE id = ?", (record_id,)
            ).fetchone()
            if row is not None:
                self._process_report_state(row)
            return
        raise ValueError(f"unsupported retained v1 write kind: {kind}")

    def project_imported_excerpt(
        self,
        excerpt_id: str,
        *,
        source_version_id: str,
        evidence_id: str,
    ) -> str:
        existing_id = self.projection_target(
            "excerpt", excerpt_id, "evidence"
        )
        if existing_id is not None:
            return existing_id
        row = self.conn.execute(
            """
            SELECT e.*, rs.freshness_state AS session_freshness_state
            FROM excerpts e
            LEFT JOIN research_sessions rs ON rs.id = e.session_id
            WHERE e.id = ?
            """,
            (excerpt_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"excerpt:{excerpt_id} not found")
        version = self.conn.execute(
            "SELECT source_id FROM source_versions WHERE id = ?",
            (source_version_id,),
        ).fetchone()
        if version is None or version["source_id"] != row["source_id"]:
            raise ValueError(
                "imported evidence source version must belong to its source"
            )
        record = evidence_span_from_legacy(
            row,
            source_version_id=source_version_id,
            persisted=True,
        )
        metadata = {
            **record.metadata,
            "projection_origin": "import",
            "v1_excerpt_id": excerpt_id,
        }
        metadata.pop("migration_id", None)
        metadata.pop("migration_warnings", None)
        self.conn.execute(
            """
            INSERT INTO evidence_spans (
                id, source_version_id, topic_id, question_id, session_id,
                quote_text, quote_sha256, selector_type, selector_json, note,
                confidence, anchor_state, review_state, trust_tier,
                created_by_model, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                record.source_version_id,
                record.topic_id,
                record.question_id,
                record.session_id,
                record.quote_text,
                record.quote_sha256,
                record.selector_type,
                canonical_json(record.selector),
                record.note,
                record.confidence,
                record.anchor_state,
                record.review_state,
                record.trust_tier,
                record.created_by_model,
                record.created_at,
                canonical_json(metadata),
            ),
        )
        self.record_projection_identity(
            "excerpt",
            excerpt_id,
            "evidence",
            evidence_id,
            update_existing=False,
        )
        return evidence_id

    def update_progress(
        self,
        phase: str,
        *,
        last_legacy_id: str | None,
        last_related_id: str | None,
        processed_count: int,
        warning_count: int,
        error_count: int,
        status: str,
        updated_at: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE migration_backfill_progress
            SET last_legacy_id = ?,
                last_related_id = ?,
                processed_count = processed_count + ?,
                warning_count = warning_count + ?,
                error_count = error_count + ?,
                status = ?,
                updated_at = ?
            WHERE migration_id = ? AND phase = ?
            """,
            (
                last_legacy_id,
                last_related_id,
                processed_count,
                warning_count,
                error_count,
                status,
                updated_at,
                V2_MIGRATION_ID,
                phase,
            ),
        )

    def totals(self) -> tuple[int, int]:
        warning_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM migration_backfill_warnings "
            "WHERE migration_id = ?",
            (V2_MIGRATION_ID,),
        ).fetchone()["count"]
        error_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM migration_backfill_errors "
            "WHERE migration_id = ? AND resolved_at IS NULL",
            (V2_MIGRATION_ID,),
        ).fetchone()["count"]
        return int(warning_count), int(error_count)

    def _process_source(self, row: Any) -> tuple[int, int]:
        mapped_id = self.projection_target(
            "source", row["id"], "source_version"
        )
        if mapped_id is not None:
            mapped = self.conn.execute(
                """
                SELECT id FROM source_versions
                WHERE id = ? AND source_id = ?
                """,
                (mapped_id, row["id"]),
            ).fetchone()
            if mapped is not None:
                return 0, 0
        record = source_version_from_legacy(row, persisted=True)
        self.conn.execute(
            """
            INSERT INTO source_versions (
                id, source_id, version_key, version_kind, retrieved_at,
                published_at, content_sha256, canonical_locator,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                record.id,
                record.source_id,
                record.version_key,
                record.version_kind,
                record.retrieved_at,
                record.published_at,
                record.content_sha256,
                record.canonical_locator,
                canonical_json(record.metadata),
                record.created_at,
            ),
        )
        self.record_projection_identity(
            "source",
            row["id"],
            "source_version",
            record.id,
            update_existing=False,
        )
        warnings = record.metadata["migration_warnings"]
        self._record_warnings("source", row["id"], warnings, row["created_at"])
        self._record_legacy_state(
            row,
            event_entity_kind="source_version",
            event_entity_id=record.id,
            refresh_entity_kind="source",
            refresh_entity_id=row["id"],
        )
        return len(warnings), 0

    def _process_evidence(self, row: Any) -> tuple[int, int]:
        mapped_id = self.projection_target(
            "excerpt", row["id"], "evidence"
        )
        if mapped_id is not None:
            mapped = self.conn.execute(
                "SELECT id FROM evidence_spans WHERE id = ?",
                (mapped_id,),
            ).fetchone()
            if mapped is not None:
                return 0, 0
        if not row["quote_text"]:
            self._record_error(
                "excerpt",
                row["id"],
                "empty_legacy_quote",
                {"field": "quote_text"},
                row["created_at"],
            )
            return 0, 1
        source_version_id = self.projection_target(
            "source", row["source_id"], "source_version"
        )
        if source_version_id is None:
            self._record_error(
                "excerpt",
                row["id"],
                "legacy_source_version_missing",
                {"field": "source_id"},
                row["created_at"],
            )
            return 0, 1
        record = evidence_span_from_legacy(
            row,
            source_version_id=source_version_id,
            persisted=True,
        )
        self.conn.execute(
            """
            INSERT INTO evidence_spans (
                id, source_version_id, topic_id, question_id, session_id,
                quote_text, quote_sha256, selector_type, selector_json, note,
                confidence, anchor_state, review_state, trust_tier,
                created_by_model, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                record.id,
                record.source_version_id,
                record.topic_id,
                record.question_id,
                record.session_id,
                record.quote_text,
                record.quote_sha256,
                record.selector_type,
                canonical_json(record.selector),
                record.note,
                record.confidence,
                record.anchor_state,
                record.review_state,
                record.trust_tier,
                record.created_by_model,
                record.created_at,
                canonical_json(record.metadata),
            ),
        )
        self.record_projection_identity(
            "excerpt",
            row["id"],
            "evidence",
            record.id,
            update_existing=False,
        )
        warnings = record.metadata["migration_warnings"]
        self._record_warnings("excerpt", row["id"], warnings, row["created_at"])
        self._record_legacy_state(
            row,
            event_entity_kind="evidence",
            event_entity_id=record.id,
            refresh_entity_kind="evidence",
            refresh_entity_id=record.id,
        )
        return len(warnings), 0

    def _process_claim(self, row: Any) -> tuple[int, int]:
        mapped_id = self.projection_target(
            "claim", row["id"], "claim_revision"
        )
        if mapped_id is not None:
            mapped = self.conn.execute(
                """
                SELECT id FROM claim_revisions
                WHERE id = ? AND claim_id = ?
                """,
                (mapped_id, row["id"]),
            ).fetchone()
            if mapped is not None:
                return 0, 0
        record = claim_revision_from_legacy(row, persisted=True)
        if record.status == "supported":
            legacy_links = self.conn.execute(
                """
                SELECT excerpt_id
                FROM claim_excerpts
                WHERE claim_id = ?
                ORDER BY excerpt_id
                """,
                (row["id"],),
            ).fetchall()
            has_representable_support = any(
                self.conn.execute(
                    "SELECT id FROM evidence_spans WHERE id = ?",
                    (
                        self.projection_target(
                            "excerpt", link["excerpt_id"], "evidence"
                        ),
                    ),
                ).fetchone()
                is not None
                for link in legacy_links
            )
            if not has_representable_support:
                self._record_error(
                    "claim",
                    row["id"],
                    "supported_claim_without_evidence",
                    {"field": "claim_excerpts"},
                    row["created_at"],
                )
                return 0, 1
        self.conn.execute(
            """
            INSERT INTO claim_revisions (
                id, claim_id, revision_number, title, statement, status,
                confidence, created_by_model, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                record.id,
                record.claim_id,
                record.revision_number,
                record.title,
                record.statement,
                record.status,
                record.confidence,
                record.created_by_model,
                record.created_at,
                canonical_json(record.metadata),
            ),
        )
        self.record_projection_identity(
            "claim",
            row["id"],
            "claim_revision",
            record.id,
            update_existing=False,
        )
        warnings = record.metadata["migration_warnings"]
        self._record_warnings("claim", row["id"], warnings, row["created_at"])
        self._record_legacy_state(
            row,
            event_entity_kind="claim_revision",
            event_entity_id=record.id,
            refresh_entity_kind="claim",
            refresh_entity_id=row["id"],
        )
        return len(warnings), 0

    def _process_claim_evidence(self, row: Any) -> tuple[int, int]:
        revision_id = self.projection_target(
            "claim", row["claim_id"], "claim_revision"
        )
        evidence_id = self.projection_target(
            "excerpt", row["excerpt_id"], "evidence"
        )
        if revision_id is None or evidence_id is None:
            self._record_error(
                "claim_evidence",
                f"{row['claim_id']}:{row['excerpt_id']}",
                "legacy_relationship_target_missing",
                {"field": "claim_excerpts"},
                row["claim_created_at"],
            )
            return 0, 1
        self.conn.execute(
            """
            INSERT INTO claim_evidence (
                claim_revision_id, evidence_span_id, relationship,
                rationale, weight, review_state, created_at
            ) VALUES (?, ?, 'supports', ?, ?, 'unreviewed', ?)
            ON CONFLICT(claim_revision_id, evidence_span_id) DO NOTHING
            """,
            (
                revision_id,
                evidence_id,
                row["rationale"],
                float(row["weight"]),
                row["claim_created_at"],
            ),
        )
        return 0, 0

    def _process_claim_pointer(self, row: Any) -> tuple[int, int]:
        revision_id = self.projection_target(
            "claim", row["id"], "claim_revision"
        )
        if row["current_revision_id"] is not None:
            return 0, 0
        revision = self.conn.execute(
            "SELECT * FROM claim_revisions WHERE id = ?", (revision_id,)
        ).fetchone()
        if revision is None:
            self._record_error(
                "claim",
                row["id"],
                "claim_revision_missing",
                {"field": "current_revision_id"},
                row["created_at"],
            )
            return 0, 1
        self.conn.execute(
            """
            UPDATE claims
            SET canonical_key = COALESCE(canonical_key, dedupe_key),
                current_revision_id = ?,
                scope_json = COALESCE(scope_json, '{}'),
                title = ?,
                statement = ?,
                status = ?,
                confidence = ?,
                updated_at = ?
            WHERE id = ? AND current_revision_id IS NULL
            """,
            (
                revision_id,
                revision["title"],
                revision["statement"],
                row["status"],
                revision["confidence"],
                revision["created_at"],
                row["id"],
            ),
        )
        return 0, 0

    def _process_report_state(self, row: Any) -> tuple[int, int]:
        self.record_projection_identity(
            "report",
            row["id"],
            "report",
            row["id"],
            update_existing=False,
        )
        self._record_legacy_state(
            row,
            event_entity_kind="report",
            event_entity_id=row["id"],
            refresh_entity_kind="report",
            refresh_entity_id=row["id"],
        )
        relationship_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM report_claims WHERE report_id = ?",
            (row["id"],),
        ).fetchone()["count"]
        unresolved = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM report_claims rc
            LEFT JOIN claims c ON c.id = rc.claim_id
            LEFT JOIN claim_revisions cr ON cr.id = c.current_revision_id
            WHERE rc.report_id = ?
              AND (
                  cr.id IS NULL
                  OR NOT EXISTS (
                      SELECT 1
                      FROM claim_evidence ce
                      WHERE ce.claim_revision_id = cr.id
                  )
              )
            """,
            (row["id"],),
        ).fetchone()["count"]
        warning_codes = []
        if not relationship_count:
            warning_codes.append("legacy_report_without_claims")
        if unresolved:
            warning_codes.append("unresolved_report_evidence")
        if warning_codes:
            self._record_warnings(
                "report",
                row["id"],
                warning_codes,
                row["created_at"],
            )
            return len(warning_codes), 0
        return 0, 0

    def _record_warnings(
        self,
        entity_kind: str,
        legacy_id: str,
        codes: Iterable[str],
        created_at: str,
    ) -> None:
        for code in sorted(set(codes)):
            warning_id = deterministic_v2_id(
                "migw", f"{entity_kind}:{code}", legacy_id
            )
            details = {
                "field": {
                    "invalid_legacy_hash": "content_sha256",
                    "legacy_locator_hash": "content_sha256",
                    "legacy_metadata_hash": "content_sha256",
                    "malformed_legacy_selector": "selector_json",
                    "missing_legacy_hash": "content_sha256",
                    "missing_snapshot": "snapshot_present",
                    "normalized_legacy_hash": "content_sha256",
                    "weak_legacy_hash": "content_sha256",
                }.get(code, "legacy_record")
            }
            self.conn.execute(
                """
                INSERT INTO migration_backfill_warnings (
                    id, migration_id, entity_kind, legacy_id, code,
                    details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    warning_id,
                    V2_MIGRATION_ID,
                    entity_kind,
                    legacy_id,
                    code,
                    canonical_json(details),
                    created_at,
                ),
            )

    def _record_error(
        self,
        entity_kind: str,
        legacy_id: str,
        code: str,
        details: dict[str, Any],
        created_at: str,
    ) -> None:
        error_id = deterministic_v2_id(
            "mige", f"{entity_kind}:{code}", legacy_id
        )
        self.conn.execute(
            """
            INSERT INTO migration_backfill_errors (
                id, migration_id, entity_kind, legacy_id, code, retryable,
                details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                error_id,
                V2_MIGRATION_ID,
                entity_kind,
                legacy_id,
                code,
                canonical_json(details),
                created_at,
            ),
        )

    def _record_legacy_state(
        self,
        row: Any,
        *,
        event_entity_kind: str,
        event_entity_id: str,
        refresh_entity_kind: str,
        refresh_entity_id: str,
    ) -> None:
        review_state = row["review_state"]
        if review_state == "reviewed":
            self._insert_review_event(
                event_entity_kind,
                event_entity_id,
                "approve",
                "unreviewed",
                "reviewed",
                row["id"],
                row["created_at"],
            )
        elif review_state == "flagged":
            self._insert_review_event(
                event_entity_kind,
                event_entity_id,
                "contest",
                "unreviewed",
                "flagged",
                row["id"],
                row["created_at"],
            )
        if row["conflict_state"] == "conflicted" and review_state != "flagged":
            self._insert_review_event(
                event_entity_kind,
                event_entity_id,
                "contest",
                review_state,
                "conflicted",
                row["id"],
                row["created_at"],
            )

        refresh_due_at = row["refresh_due_at"]
        freshness_state = _optional_row_value(row, "session_freshness_state")
        if is_due(
            refresh_due_at, now=self.now_text
        ) or freshness_state in {"needs_refresh", "stale"}:
            self._insert_refresh(
                refresh_entity_kind,
                refresh_entity_id,
                "expired",
                row["id"],
                refresh_due_at,
                row["created_at"],
            )
        if row["conflict_state"] == "conflicted":
            self._insert_refresh(
                refresh_entity_kind,
                refresh_entity_id,
                "conflict",
                row["id"],
                refresh_due_at,
                row["created_at"],
            )

    def _insert_review_event(
        self,
        entity_kind: str,
        entity_id: str,
        action: str,
        from_state: str,
        to_state: str,
        legacy_id: str,
        created_at: str,
    ) -> None:
        event_id = deterministic_v2_id(
            "rev",
            f"review:{entity_kind}:{entity_id}:{action}:{to_state}",
            legacy_id,
        )
        self.conn.execute(
            """
            INSERT INTO review_events (
                id, entity_kind, entity_id, action, from_state, to_state,
                actor_type, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, 'migration', ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                event_id,
                entity_kind,
                entity_id,
                action,
                from_state,
                to_state,
                created_at,
                canonical_json(
                    {"legacy_id": legacy_id, "migration_id": V2_MIGRATION_ID}
                ),
            ),
        )

    def _insert_refresh(
        self,
        entity_kind: str,
        entity_id: str,
        reason: str,
        legacy_id: str,
        refresh_due_at: str | None,
        created_at: str,
    ) -> None:
        refresh_id = deterministic_v2_id(
            "rfr", f"refresh:{entity_kind}:{entity_id}:{reason}", legacy_id
        )
        self.conn.execute(
            """
            INSERT INTO refresh_queue (
                id, entity_kind, entity_id, reason, status, priority,
                detected_at, details_json
            ) VALUES (?, ?, ?, ?, 'pending', 0.5, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (
                refresh_id,
                entity_kind,
                entity_id,
                reason,
                refresh_due_at or created_at,
                canonical_json(
                    {
                        "legacy_id": legacy_id,
                        "migration_id": V2_MIGRATION_ID,
                        "refresh_due_at": refresh_due_at,
                    }
                ),
            ),
        )


class V2ReadRepository:
    """Read persisted v2 records, with a write-free v1 compatibility fallback."""

    def __init__(self, conn: DbConnection):
        self.conn = conn

    def get_source_version(self, source_id: str) -> SourceVersionRecord:
        mapped = self.conn.execute(
            """
            SELECT sv.*
            FROM legacy_projection_identity lpi
            JOIN source_versions sv ON sv.id = lpi.v2_id
            WHERE lpi.legacy_kind = 'source'
              AND lpi.legacy_id = ?
              AND lpi.v2_kind = 'source_version'
              AND sv.source_id = ?
            """,
            (source_id, source_id),
        ).fetchone()
        if mapped is not None:
            return self._source_version_from_row(mapped)
        row = self.conn.execute(
            """
            SELECT * FROM source_versions
            WHERE source_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (source_id,),
        ).fetchone()
        if row is not None:
            return self._source_version_from_row(row)
        legacy = self.conn.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if legacy is None:
            raise KeyError(f"source:{source_id} not found")
        return source_version_from_legacy(legacy, persisted=False)

    def get_evidence_for_legacy_excerpt(
        self, excerpt_id: str
    ) -> EvidenceSpanRecord:
        mapped = self.conn.execute(
            """
            SELECT e.*
            FROM legacy_projection_identity lpi
            JOIN evidence_spans e ON e.id = lpi.v2_id
            WHERE lpi.legacy_kind = 'excerpt'
              AND lpi.legacy_id = ?
              AND lpi.v2_kind = 'evidence'
            """,
            (excerpt_id,),
        ).fetchone()
        if mapped is not None:
            return self._evidence_from_row(mapped)
        evidence_id = deterministic_v2_id("evd", "excerpt", excerpt_id)
        row = self.conn.execute(
            "SELECT * FROM evidence_spans WHERE id = ?", (evidence_id,)
        ).fetchone()
        if row is not None:
            return self._evidence_from_row(row)
        legacy = self.conn.execute(
            """
            SELECT e.*, rs.freshness_state AS session_freshness_state
            FROM excerpts e
            LEFT JOIN research_sessions rs ON rs.id = e.session_id
            WHERE e.id = ?
            """,
            (excerpt_id,),
        ).fetchone()
        if legacy is None:
            raise KeyError(f"excerpt:{excerpt_id} not found")
        source_version = self.get_source_version(legacy["source_id"])
        return evidence_span_from_legacy(
            legacy,
            source_version_id=source_version.id,
            persisted=False,
        )

    def get_current_claim_revision(
        self, claim_id: str
    ) -> ClaimRevisionRecord:
        row = self.conn.execute(
            """
            SELECT cr.*
            FROM claims c
            JOIN claim_revisions cr ON cr.id = c.current_revision_id
            WHERE c.id = ?
            """,
            (claim_id,),
        ).fetchone()
        if row is not None:
            return self._claim_revision_from_row(row)
        legacy = self.conn.execute(
            """
            SELECT c.*, rs.freshness_state AS session_freshness_state
            FROM claims c
            LEFT JOIN research_sessions rs ON rs.id = c.session_id
            WHERE c.id = ?
            """,
            (claim_id,),
        ).fetchone()
        if legacy is None:
            raise KeyError(f"claim:{claim_id} not found")
        return claim_revision_from_legacy(legacy, persisted=False)

    def list_claim_evidence(self, claim_id: str) -> list[ClaimEvidenceRecord]:
        rows = self.conn.execute(
            """
            SELECT ce.*
            FROM claims c
            JOIN claim_evidence ce
              ON ce.claim_revision_id = c.current_revision_id
            WHERE c.id = ?
            ORDER BY ce.evidence_span_id
            """,
            (claim_id,),
        ).fetchall()
        if rows:
            return [
                ClaimEvidenceRecord(
                    claim_revision_id=row["claim_revision_id"],
                    evidence_span_id=row["evidence_span_id"],
                    relationship=row["relationship"],
                    rationale=row["rationale"],
                    weight=float(row["weight"]),
                    review_state=row["review_state"],
                    created_at=row["created_at"],
                )
                for row in rows
            ]
        legacy_rows = self.conn.execute(
            """
            SELECT ce.*, c.created_at AS claim_created_at
            FROM claim_excerpts ce
            JOIN claims c ON c.id = ce.claim_id
            WHERE ce.claim_id = ?
            ORDER BY ce.excerpt_id
            """,
            (claim_id,),
        ).fetchall()
        return [
            ClaimEvidenceRecord(
                claim_revision_id=(
                    self._projection_target(
                        "claim", row["claim_id"], "claim_revision"
                    )
                    or deterministic_v2_id(
                        "clmr", "claim", row["claim_id"]
                    )
                ),
                evidence_span_id=(
                    self._projection_target(
                        "excerpt", row["excerpt_id"], "evidence"
                    )
                    or deterministic_v2_id(
                        "evd", "excerpt", row["excerpt_id"]
                    )
                ),
                relationship="supports",
                rationale=row["rationale"],
                weight=float(row["weight"]),
                review_state="unreviewed",
                created_at=row["claim_created_at"],
                persisted=False,
            )
            for row in legacy_rows
        ]

    def _projection_target(
        self,
        legacy_kind: str,
        legacy_id: str,
        v2_kind: str,
    ) -> str | None:
        row = self.conn.execute(
            """
            SELECT v2_id
            FROM legacy_projection_identity
            WHERE legacy_kind = ? AND legacy_id = ? AND v2_kind = ?
            """,
            (legacy_kind, legacy_id, v2_kind),
        ).fetchone()
        return row["v2_id"] if row is not None else None

    def _source_version_from_row(self, row: Any) -> SourceVersionRecord:
        return SourceVersionRepository._source_version_from_row(row)

    def _evidence_from_row(self, row: Any) -> EvidenceSpanRecord:
        return EvidenceSpanRecord(
            id=row["id"],
            source_version_id=row["source_version_id"],
            topic_id=row["topic_id"],
            question_id=row["question_id"],
            session_id=row["session_id"],
            quote_text=row["quote_text"],
            quote_sha256=row["quote_sha256"],
            selector_type=row["selector_type"],
            selector=json.loads(row["selector_json"]),
            note=row["note"],
            confidence=float(row["confidence"]),
            anchor_state=row["anchor_state"],
            review_state=row["review_state"],
            trust_tier=row["trust_tier"],
            created_by_model=row["created_by_model"],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata_json"]),
        )

    def _claim_revision_from_row(self, row: Any) -> ClaimRevisionRecord:
        return ClaimRevisionRecord(
            id=row["id"],
            claim_id=row["claim_id"],
            revision_number=int(row["revision_number"]),
            title=row["title"],
            statement=row["statement"],
            status=row["status"],
            confidence=float(row["confidence"]),
            created_by_model=row["created_by_model"],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata_json"]),
        )
