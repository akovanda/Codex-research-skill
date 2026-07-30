from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Iterable
from uuid import UUID, uuid5

from ..db import DbConnection


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


def deterministic_v2_id(prefix: str, kind: str, legacy_id: str) -> str:
    return f"{prefix}_{uuid5(_MIGRATION_NAMESPACE, f'{kind}:{legacy_id}')}"


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


class V2BackfillRepository:
    def __init__(self, conn: DbConnection):
        self.conn = conn

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
        if not row["quote_text"]:
            self._record_error(
                "excerpt",
                row["id"],
                "empty_legacy_quote",
                {"field": "quote_text"},
                row["created_at"],
            )
            return 0, 1
        source_version_id = deterministic_v2_id(
            "srcv", "source", row["source_id"]
        )
        if self.conn.execute(
            "SELECT id FROM source_versions WHERE id = ?",
            (source_version_id,),
        ).fetchone() is None:
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
                        deterministic_v2_id(
                            "evd", "excerpt", link["excerpt_id"]
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
        revision_id = deterministic_v2_id(
            "clmr", "claim", row["claim_id"]
        )
        evidence_id = deterministic_v2_id(
            "evd", "excerpt", row["excerpt_id"]
        )
        revision_exists = self.conn.execute(
            "SELECT id FROM claim_revisions WHERE id = ?", (revision_id,)
        ).fetchone()
        evidence_exists = self.conn.execute(
            "SELECT id FROM evidence_spans WHERE id = ?", (evidence_id,)
        ).fetchone()
        if revision_exists is None or evidence_exists is None:
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
        revision_id = deterministic_v2_id("clmr", "claim", row["id"])
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
            WHERE id = ?
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
        if refresh_due_at or freshness_state in {"needs_refresh", "stale"}:
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
                claim_revision_id=deterministic_v2_id(
                    "clmr", "claim", row["claim_id"]
                ),
                evidence_span_id=deterministic_v2_id(
                    "evd", "excerpt", row["excerpt_id"]
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

    def _source_version_from_row(self, row: Any) -> SourceVersionRecord:
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
        )

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
