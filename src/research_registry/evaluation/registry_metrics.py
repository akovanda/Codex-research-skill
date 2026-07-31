from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from ..data_audit import connect_database_read_only


_SINCE = re.compile(r"^[1-9][0-9]*[dhm]$")


def collect_registry_metrics(
    database: str | Path,
    *,
    since: str = "30d",
) -> dict[str, Any]:
    """Return content-free evidence, deposit, migration, and storage health."""
    if not _SINCE.fullmatch(since):
        raise ValueError("since must use a positive integer plus d, h, or m")
    with connect_database_read_only(database) as conn:
        tables = _tables(conn)
        evidence = _evidence_metrics(conn, tables)
        deposit = _deposit_metrics(conn, tables)
        migration = _migration_metrics(conn, tables)
        storage = {
            "database_kind": conn.target.kind,
            "record_counts": {
                table: _count(conn, table)
                for table in (
                    "questions",
                    "research_sessions",
                    "sources",
                    "source_versions",
                    "evidence_spans",
                    "claims",
                    "claim_revisions",
                    "reports",
                )
                if table in tables
            },
        }
    return {
        "protocol": "research-registry-metrics/v1",
        "since": since,
        "evidence": evidence,
        "deposit": deposit,
        "migration": migration,
        "storage": storage,
        "operation_observability": {
            "status": "not_recorded",
            "reason": (
                "outbound telemetry is disabled and no content-free "
                "operation-event store exists"
            ),
        },
    }


def _evidence_metrics(conn: Any, tables: set[str]) -> dict[str, Any]:
    required = {
        "claims",
        "claim_revisions",
        "claim_evidence",
        "evidence_spans",
        "source_versions",
    }
    if not required.issubset(tables):
        return {"status": "v2_schema_unavailable"}
    supported = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM claims c
        JOIN claim_revisions cr ON cr.id = c.current_revision_id
        WHERE cr.status = 'supported'
        """,
    )
    supported_with_evidence = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM claims c
        JOIN claim_revisions cr ON cr.id = c.current_revision_id
        WHERE cr.status = 'supported'
          AND EXISTS (
              SELECT 1
              FROM claim_evidence ce
              JOIN evidence_spans e ON e.id = ce.evidence_span_id
              WHERE ce.claim_revision_id = cr.id
                AND ce.relationship IN ('supports', 'qualifies')
                AND e.anchor_state IN ('resolved', 'relocated')
          )
        """,
    )
    evidence_total = _count(conn, "evidence_spans")
    resolved = _scalar(
        conn,
        """
        SELECT COUNT(*) FROM evidence_spans
        WHERE anchor_state IN ('resolved', 'relocated')
        """,
    )
    versions = _count(conn, "source_versions")
    valid_hashes = _scalar(
        conn,
        """
        SELECT COUNT(*) FROM source_versions
        WHERE length(content_sha256) = 64
          AND content_sha256 = lower(content_sha256)
        """,
    )
    missing_objects = (
        _scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM source_versions sv
            LEFT JOIN content_objects co ON co.id = sv.content_object_id
            WHERE sv.content_object_id IS NOT NULL AND co.id IS NULL
            """,
        )
        if "content_objects" in tables
        else 0
    )
    contested_with_refuting = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM claims c
        JOIN claim_revisions cr ON cr.id = c.current_revision_id
        WHERE cr.status = 'contested'
          AND EXISTS (
              SELECT 1 FROM claim_evidence ce
              WHERE ce.claim_revision_id = cr.id
                AND ce.relationship = 'refutes'
          )
        """,
    )
    history_errors = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM claims c
        WHERE c.current_revision_id IS NOT NULL
          AND (
              NOT EXISTS (
                  SELECT 1 FROM claim_revisions current_revision
                  WHERE current_revision.id = c.current_revision_id
                    AND current_revision.claim_id = c.id
              )
              OR (
                  SELECT COUNT(*) FROM claim_revisions all_revisions
                  WHERE all_revisions.claim_id = c.id
              ) <> (
                  SELECT COALESCE(MAX(revision_number), 0)
                  FROM claim_revisions numbered_revisions
                  WHERE numbered_revisions.claim_id = c.id
              )
          )
        """,
    )
    return {
        "status": "available",
        "supported_current_claims": supported,
        "supported_with_valid_evidence": supported_with_evidence,
        "supported_evidence_rate": _ratio(
            supported_with_evidence, supported
        ),
        "evidence_span_count": evidence_total,
        "uniquely_resolved_selector_count": resolved,
        "selector_resolution_rate": _ratio(resolved, evidence_total),
        "source_version_count": versions,
        "valid_source_hash_count": valid_hashes,
        "valid_source_hash_rate": _ratio(valid_hashes, versions),
        "missing_content_object_references": missing_objects,
        "contested_claims_with_refuting_evidence": (
            contested_with_refuting
        ),
        "claim_revision_history_errors": history_errors,
    }


def _deposit_metrics(conn: Any, tables: set[str]) -> dict[str, Any]:
    if "idempotency_keys" not in tables:
        return {"status": "v2_schema_unavailable"}
    accepted = _scalar(
        conn,
        """
        SELECT COUNT(*) FROM idempotency_keys
        WHERE operation = 'research_deposit_v2'
        """,
    )
    incomplete = _scalar(
        conn,
        """
        SELECT COUNT(*) FROM idempotency_keys
        WHERE operation = 'research_deposit_v2'
          AND response_json LIKE '%"reservation"%'
        """,
    )
    return {
        "status": "available",
        "accepted_bundle_count": accepted,
        "incomplete_reservation_count": incomplete,
        "partial_bundle_count": incomplete,
        "partial_bundle_observation": (
            "durable incomplete reservations only; injected rollback coverage "
            "is enforced by the release security suite"
        ),
    }


def _migration_metrics(conn: Any, tables: set[str]) -> dict[str, Any]:
    if "migration_backfill_progress" not in tables:
        return {"status": "v2_schema_unavailable"}
    return {
        "status": "available",
        "processed_count": _scalar(
            conn,
            "SELECT COALESCE(SUM(processed_count), 0) "
            "FROM migration_backfill_progress",
        ),
        "warning_count": (
            _count(conn, "migration_backfill_warnings")
            if "migration_backfill_warnings" in tables
            else 0
        ),
        "unresolved_error_count": (
            _scalar(
                conn,
                "SELECT COUNT(*) FROM migration_backfill_errors "
                "WHERE resolved_at IS NULL",
            )
            if "migration_backfill_errors" in tables
            else 0
        ),
        "completed_phase_count": _scalar(
            conn,
            "SELECT COUNT(*) FROM migration_backfill_progress "
            "WHERE status = 'completed'",
        ),
        "phase_count": _count(conn, "migration_backfill_progress"),
    }


def _tables(conn: Any) -> set[str]:
    if conn.target.kind == "sqlite":
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT table_name AS name
            FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_type = 'BASE TABLE'
            """
        ).fetchall()
    return {str(row["name"]) for row in rows}


def _count(conn: Any, table: str) -> int:
    return _scalar(conn, f"SELECT COUNT(*) FROM {table}")


def _scalar(conn: Any, query: str) -> int:
    row = conn.execute(query).fetchone()
    value = (
        next(iter(row.values()))
        if isinstance(row, Mapping)
        else row[0]
    )
    return int(value or 0)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 6)
