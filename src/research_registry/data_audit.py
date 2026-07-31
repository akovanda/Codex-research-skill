from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any, Iterator

from .db import DatabaseTarget, DbConnection, dict_row, psycopg, resolve_database_target


AUDIT_FORMAT_VERSION = 1
V1_TABLES = (
    "api_keys",
    "audit_log",
    "claim_excerpts",
    "claims",
    "excerpts",
    "organizations",
    "org_memberships",
    "questions",
    "report_claims",
    "reports",
    "research_sessions",
    "schema_migrations",
    "sources",
    "topics",
    "users",
)
DEDUPE_TABLES = (
    "topics",
    "questions",
    "research_sessions",
    "sources",
    "excerpts",
    "claims",
    "reports",
)
ENUM_FIELDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("topics", "namespace_kind"): ("user", "org"),
    ("questions", "status"): ("open", "answered", "insufficient_evidence"),
    ("questions", "follow_up_status"): ("open", "ready", "blocked", "done"),
    ("questions", "visibility"): ("private", "public"),
    ("questions", "author_type"): ("human", "agent"),
    ("questions", "namespace_kind"): ("user", "org"),
    ("questions", "public_index_state"): ("private", "namespace_only", "included", "suppressed"),
    ("research_sessions", "mode"): (
        "reuse",
        "live_research",
        "synthesis",
        "insufficient_evidence",
        "repo_triage",
        "repo_review",
    ),
    ("research_sessions", "status"): ("completed", "insufficient_evidence"),
    ("research_sessions", "freshness_state"): ("fresh", "needs_refresh"),
    ("research_sessions", "visibility"): ("private", "public"),
    ("research_sessions", "author_type"): ("human", "agent"),
    ("research_sessions", "namespace_kind"): ("user", "org"),
    ("research_sessions", "public_index_state"): ("private", "namespace_only", "included", "suppressed"),
    ("sources", "review_state"): ("unreviewed", "reviewed", "flagged"),
    ("sources", "trust_tier"): ("low", "medium", "high"),
    ("sources", "conflict_state"): ("none", "conflicted"),
    ("sources", "visibility"): ("private", "public"),
    ("sources", "namespace_kind"): ("user", "org"),
    ("sources", "public_index_state"): ("private", "namespace_only", "included", "suppressed"),
    ("excerpts", "review_state"): ("unreviewed", "reviewed", "flagged"),
    ("excerpts", "trust_tier"): ("low", "medium", "high"),
    ("excerpts", "conflict_state"): ("none", "conflicted"),
    ("excerpts", "visibility"): ("private", "public"),
    ("excerpts", "author_type"): ("human", "agent"),
    ("excerpts", "namespace_kind"): ("user", "org"),
    ("excerpts", "public_index_state"): ("private", "namespace_only", "included", "suppressed"),
    ("claims", "status"): ("supported", "partial", "conflicted", "insufficient_evidence"),
    ("claims", "review_state"): ("unreviewed", "reviewed", "flagged"),
    ("claims", "trust_tier"): ("low", "medium", "high"),
    ("claims", "conflict_state"): ("none", "conflicted"),
    ("claims", "visibility"): ("private", "public"),
    ("claims", "author_type"): ("human", "agent"),
    ("claims", "namespace_kind"): ("user", "org"),
    ("claims", "public_index_state"): ("private", "namespace_only", "included", "suppressed"),
    ("reports", "report_kind"): ("guidance", "legacy_answer"),
    ("reports", "review_state"): ("unreviewed", "reviewed", "flagged"),
    ("reports", "trust_tier"): ("low", "medium", "high"),
    ("reports", "conflict_state"): ("none", "conflicted"),
    ("reports", "visibility"): ("private", "public"),
    ("reports", "author_type"): ("human", "agent"),
    ("reports", "namespace_kind"): ("user", "org"),
    ("reports", "public_index_state"): ("private", "namespace_only", "included", "suppressed"),
    ("org_memberships", "role"): ("member", "reviewer", "admin"),
    ("api_keys", "namespace_kind"): ("user", "org"),
    ("api_keys", "status"): ("active", "revoked", "blocked"),
}
ORPHAN_RELATIONS: dict[str, tuple[str, str, str, bool]] = {
    "topics.parent_topic_id": ("topics", "parent_topic_id", "topics", True),
    "questions.topic_id": ("questions", "topic_id", "topics", False),
    "questions.parent_question_id": ("questions", "parent_question_id", "questions", True),
    "questions.generated_by_session_id": (
        "questions",
        "generated_by_session_id",
        "research_sessions",
        True,
    ),
    "research_sessions.question_id": ("research_sessions", "question_id", "questions", False),
    "research_sessions.refresh_of_session_id": (
        "research_sessions",
        "refresh_of_session_id",
        "research_sessions",
        True,
    ),
    "excerpts.source_id": ("excerpts", "source_id", "sources", False),
    "excerpts.question_id": ("excerpts", "question_id", "questions", False),
    "excerpts.session_id": ("excerpts", "session_id", "research_sessions", True),
    "excerpts.topic_id": ("excerpts", "topic_id", "topics", True),
    "claims.question_id": ("claims", "question_id", "questions", False),
    "claims.session_id": ("claims", "session_id", "research_sessions", True),
    "claims.topic_id": ("claims", "topic_id", "topics", True),
    "claim_excerpts.claim_id": ("claim_excerpts", "claim_id", "claims", False),
    "claim_excerpts.excerpt_id": ("claim_excerpts", "excerpt_id", "excerpts", False),
    "reports.question_id": ("reports", "question_id", "questions", False),
    "reports.session_id": ("reports", "session_id", "research_sessions", True),
    "reports.refresh_of_report_id": ("reports", "refresh_of_report_id", "reports", True),
    "report_claims.report_id": ("report_claims", "report_id", "reports", False),
    "report_claims.claim_id": ("report_claims", "claim_id", "claims", False),
    "org_memberships.org_id": ("org_memberships", "org_id", "organizations", False),
    "org_memberships.user_id": ("org_memberships", "user_id", "users", False),
    "api_keys.actor_user_id": ("api_keys", "actor_user_id", "users", False),
    "api_keys.actor_org_id": ("api_keys", "actor_org_id", "organizations", True),
    "audit_log.api_key_id": ("audit_log", "api_key_id", "api_keys", True),
    "audit_log.actor_user_id": ("audit_log", "actor_user_id", "users", True),
    "audit_log.actor_org_id": ("audit_log", "actor_org_id", "organizations", True),
}
_SAFE_MIGRATION_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@contextmanager
def connect_database_read_only(target: str | Path | DatabaseTarget) -> Iterator[DbConnection]:
    """Open an existing database with writes disabled at the database boundary."""
    resolved = target if isinstance(target, DatabaseTarget) else resolve_database_target(target)
    if resolved.kind == "sqlite":
        assert resolved.sqlite_path is not None
        if not resolved.sqlite_path.is_file():
            raise FileNotFoundError(resolved.sqlite_path)
        raw = sqlite3.connect(f"{resolved.sqlite_path.as_uri()}?mode=ro", uri=True)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
        raw.execute("PRAGMA query_only = ON")
    else:
        if psycopg is None or dict_row is None:  # pragma: no cover - optional import failure
            raise RuntimeError("psycopg is required for postgres database URLs")
        raw = psycopg.connect(resolved.url, row_factory=dict_row)
        raw.execute("SET TRANSACTION READ ONLY")
    connection = DbConnection(resolved, raw)
    try:
        yield connection
    finally:
        raw.rollback()
        connection.close()


def audit_database(target: str | Path | DatabaseTarget) -> dict[str, Any]:
    """Return a deterministic, content-free audit of a v1 registry database."""
    resolved = target if isinstance(target, DatabaseTarget) else resolve_database_target(target)
    with connect_database_read_only(resolved) as conn:
        present_tables = _list_tables(conn)
        row_counts = {
            table: _scalar(conn, f"SELECT COUNT(*) FROM {table}") if table in present_tables else 0
            for table in V1_TABLES
        }
        orphans = {
            label: _orphan_count(conn, relation, present_tables)
            for label, relation in ORPHAN_RELATIONS.items()
        }
        invalid_enums = {
            f"{table}.{column}": _invalid_enum_count(conn, table, column, allowed)
            for (table, column), allowed in ENUM_FIELDS.items()
            if table in present_tables and _has_column(conn, table, column)
        }
        duplicate_keys = {
            table: _duplicate_key_counts(conn, table)
            for table in DEDUPE_TABLES
            if table in present_tables and _has_column(conn, table, "dedupe_key")
        }
        result: dict[str, Any] = {
            "format_version": AUDIT_FORMAT_VERSION,
            "database": {
                "kind": resolved.kind,
                "storage_bytes": _database_size(conn),
                "integrity": _integrity(conn, orphans),
                "schema_migrations": _schema_migrations(conn, present_tables),
                "missing_v1_tables": sorted(set(V1_TABLES) - present_tables),
            },
            "row_counts": row_counts,
            "orphans": orphans,
            "relationship_gaps": _relationship_gaps(conn, present_tables),
            "source_health": _source_health(conn, present_tables),
            "selector_health": _selector_health(conn, present_tables),
            "invalid_enums": invalid_enums,
            "duplicate_keys": duplicate_keys,
            "distributions": _distributions(conn, present_tables),
            "freshness": _freshness(conn, present_tables),
            "legacy_alias_usage": {
                "observable": False,
                "reason": "annotation and finding aliases share excerpt and claim rows in v1",
            },
            "blob_health": {
                "status": "not_configured_v1",
                "referenced_objects": 0,
                "missing_objects": 0,
            },
            "backup_prerequisites": _backup_prerequisites(conn),
        }
    return result


def render_audit_markdown(report: dict[str, Any]) -> str:
    """Render an audit without including any stored content values."""
    integrity = report["database"]["integrity"]
    lines = [
        "# Research Registry v1 Data Audit",
        "",
        f"- Format version: `{report['format_version']}`",
        f"- Database kind: `{report['database']['kind']}`",
        f"- Logical storage bytes: `{report['database']['storage_bytes']}`",
        f"- Integrity check: `{integrity['check']}`",
        f"- Foreign-key violations: `{integrity['foreign_key_violations']}`",
        "",
        "## Row counts",
        "",
        "| Table | Rows |",
        "|---|---:|",
    ]
    lines.extend(
        f"| `{table}` | {count} |"
        for table, count in sorted(report["row_counts"].items())
    )
    lines.extend(
        [
            "",
            "## Data-quality counts",
            "",
            f"- Orphan references: `{sum(report['orphans'].values())}`",
            f"- Claims without excerpts: `{report['relationship_gaps']['claims_without_excerpts']}`",
            f"- Reports without claims: `{report['relationship_gaps']['reports_without_claims']}`",
            f"- Malformed selectors: `{report['selector_health']['malformed_json']}`",
            f"- Invalid enum rows: `{sum(report['invalid_enums'].values())}`",
            f"- Required snapshots missing: `{report['source_health']['required_snapshot_missing']}`",
            "",
            "This report contains aggregate counts and schema health only. Stored prompts, "
            "quotes, claims, reports, source bodies, URL query strings, and tokens are omitted.",
            "",
        ]
    )
    return "\n".join(lines)


def _list_tables(conn: DbConnection) -> set[str]:
    if conn.target.kind == "sqlite":
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT table_name AS name
            FROM information_schema.tables
            WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'
            """
        ).fetchall()
    return {str(row["name"]) for row in rows}


def _has_column(conn: DbConnection, table: str, column: str) -> bool:
    if conn.target.kind == "sqlite":
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return column in {row["name"] for row in rows}
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = ? AND column_name = ?
        """,
        (table, column),
    ).fetchone()
    return bool(row["count"])


def _scalar(conn: DbConnection, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(next(iter(dict(row).values()))) if row is not None else 0


def _orphan_count(
    conn: DbConnection,
    relation: tuple[str, str, str, bool],
    present_tables: set[str],
) -> int:
    table, column, parent, nullable = relation
    if (
        table not in present_tables
        or parent not in present_tables
        or not _has_column(conn, table, column)
    ):
        return 0
    null_clause = f"child.{column} IS NOT NULL AND " if nullable else ""
    return _scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM {table} AS child
        LEFT JOIN {parent} AS parent ON parent.id = child.{column}
        WHERE {null_clause}parent.id IS NULL
        """,
    )


def _invalid_enum_count(
    conn: DbConnection,
    table: str,
    column: str,
    allowed: tuple[str, ...],
) -> int:
    placeholders = ", ".join("?" for _ in allowed)
    return _scalar(
        conn,
        f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL OR {column} NOT IN ({placeholders})",
        allowed,
    )


def _duplicate_key_counts(conn: DbConnection, table: str) -> dict[str, int]:
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS duplicate_groups, COALESCE(SUM(group_size), 0) AS duplicate_rows
        FROM (
            SELECT COUNT(*) AS group_size
            FROM {table}
            WHERE dedupe_key IS NOT NULL
            GROUP BY dedupe_key
            HAVING COUNT(*) > 1
        ) AS duplicates
        """
    ).fetchone()
    return {
        "duplicate_groups": int(row["duplicate_groups"]),
        "duplicate_rows": int(row["duplicate_rows"]),
    }


def _schema_migrations(conn: DbConnection, present_tables: set[str]) -> list[dict[str, str]]:
    if "schema_migrations" not in present_tables:
        return []
    rows = conn.execute(
        "SELECT migration_id, checksum_sha256 FROM schema_migrations ORDER BY migration_id"
    ).fetchall()
    return [
        {
            "migration_id": (
                row["migration_id"]
                if _SAFE_MIGRATION_ID.fullmatch(row["migration_id"])
                else "<invalid>"
            ),
            "checksum_sha256": row["checksum_sha256"]
            if _SHA256.fullmatch(str(row["checksum_sha256"]))
            else "<invalid>",
        }
        for row in rows
    ]


def _integrity(conn: DbConnection, orphans: dict[str, int]) -> dict[str, Any]:
    if conn.target.kind == "sqlite":
        row = conn.execute("PRAGMA integrity_check").fetchone()
        check = str(row[0] if not isinstance(row, dict) else next(iter(row.values())))
        foreign_key_violations = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    else:
        check = "not_available"
        foreign_key_violations = sum(orphans.values())
    return {
        "check": check,
        "foreign_key_violations": foreign_key_violations,
    }


def _database_size(conn: DbConnection) -> int:
    if conn.target.kind == "sqlite":
        page_count = _scalar(conn, "PRAGMA page_count")
        page_size = _scalar(conn, "PRAGMA page_size")
        return page_count * page_size
    return _scalar(conn, "SELECT pg_database_size(current_database())")


def _relationship_gaps(conn: DbConnection, present_tables: set[str]) -> dict[str, int]:
    claims_without = 0
    reports_without = 0
    if {"claims", "claim_excerpts"} <= present_tables:
        claims_without = _scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM claims AS c
            LEFT JOIN claim_excerpts AS ce ON ce.claim_id = c.id
            WHERE ce.claim_id IS NULL
            """,
        )
    if {"reports", "report_claims"} <= present_tables:
        reports_without = _scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM reports AS r
            LEFT JOIN report_claims AS rc ON rc.report_id = r.id
            WHERE rc.report_id IS NULL
            """,
        )
    return {
        "claims_without_excerpts": claims_without,
        "reports_without_claims": reports_without,
    }


def _source_health(conn: DbConnection, present_tables: set[str]) -> dict[str, int]:
    if "sources" not in present_tables:
        return {
            "with_content_sha256": 0,
            "missing_content_sha256": 0,
            "invalid_content_sha256": 0,
            "snapshot_present": 0,
            "snapshot_absent": 0,
            "required_snapshot_missing": 0,
        }
    rows = conn.execute(
        "SELECT content_sha256, snapshot_required, snapshot_present FROM sources"
    ).fetchall()
    hashes = [row["content_sha256"] for row in rows]
    return {
        "with_content_sha256": sum(bool(value) for value in hashes),
        "missing_content_sha256": sum(not value for value in hashes),
        "invalid_content_sha256": sum(
            bool(value) and _SHA256.fullmatch(str(value)) is None for value in hashes
        ),
        "snapshot_present": sum(bool(row["snapshot_present"]) for row in rows),
        "snapshot_absent": sum(not bool(row["snapshot_present"]) for row in rows),
        "required_snapshot_missing": sum(
            bool(row["snapshot_required"]) and not bool(row["snapshot_present"]) for row in rows
        ),
    }


def _selector_health(conn: DbConnection, present_tables: set[str]) -> dict[str, int]:
    health = {
        "total": 0,
        "empty": 0,
        "malformed_json": 0,
        "non_object": 0,
        "missing_anchor": 0,
        "invalid_range": 0,
    }
    if "excerpts" not in present_tables:
        return health
    rows = conn.execute("SELECT selector_json FROM excerpts").fetchall()
    for row in rows:
        health["total"] += 1
        raw = row["selector_json"]
        if raw is None or not str(raw).strip():
            health["empty"] += 1
            continue
        try:
            selector = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            health["malformed_json"] += 1
            continue
        if not isinstance(selector, dict):
            health["non_object"] += 1
            continue
        anchor_fields = (
            "deep_link",
            "exact",
            "start",
            "end",
            "start_line",
            "end_line",
        )
        if not any(
            _selector_value_present(selector.get(field))
            for field in anchor_fields
        ):
            health["missing_anchor"] += 1
        if _range_is_invalid(selector, "start", "end") or _range_is_invalid(
            selector, "start_line", "end_line"
        ):
            health["invalid_range"] += 1
    return health


def _range_is_invalid(selector: dict[str, Any], start_field: str, end_field: str) -> bool:
    start = selector.get(start_field)
    end = selector.get(end_field)
    if start is None and end is None:
        return False
    if not isinstance(start, int) or isinstance(start, bool):
        return True
    if not isinstance(end, int) or isinstance(end, bool):
        return True
    return start < 0 or end < start


def _selector_value_present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _distributions(
    conn: DbConnection,
    present_tables: set[str],
) -> dict[str, dict[str, dict[str, int]]]:
    field_tables = {
        "namespace_kind": (
            "topics",
            "questions",
            "research_sessions",
            "sources",
            "excerpts",
            "claims",
            "reports",
            "api_keys",
        ),
        "visibility": (
            "questions",
            "research_sessions",
            "sources",
            "excerpts",
            "claims",
            "reports",
        ),
        "review_state": ("sources", "excerpts", "claims", "reports"),
        "trust_tier": ("sources", "excerpts", "claims", "reports"),
        "conflict_state": ("sources", "excerpts", "claims", "reports"),
        "freshness_state": ("research_sessions",),
    }
    distributions: dict[str, dict[str, dict[str, int]]] = {}
    for field, tables in field_tables.items():
        by_table: dict[str, dict[str, int]] = {}
        for table in tables:
            if table not in present_tables or not _has_column(conn, table, field):
                continue
            allowed = ENUM_FIELDS[(table, field)]
            counts = {
                value: _scalar(
                    conn,
                    f"SELECT COUNT(*) FROM {table} WHERE {field} = ?",
                    (value,),
                )
                for value in allowed
            }
            invalid = _invalid_enum_count(conn, table, field, allowed)
            if invalid:
                counts["invalid"] = invalid
            by_table[table] = {key: value for key, value in counts.items() if value}
        distributions[field] = by_table
    return distributions


def _freshness(conn: DbConnection, present_tables: set[str]) -> dict[str, int]:
    if "research_sessions" not in present_tables:
        return {
            "expired_sessions": 0,
            "expired_marked_fresh": 0,
            "refresh_due_records": 0,
        }
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    expired = _scalar(
        conn,
        "SELECT COUNT(*) FROM research_sessions WHERE expires_at IS NOT NULL AND expires_at <= ?",
        (now,),
    )
    expired_fresh = _scalar(
        conn,
        """
        SELECT COUNT(*) FROM research_sessions
        WHERE expires_at IS NOT NULL AND expires_at <= ? AND freshness_state = 'fresh'
        """,
        (now,),
    )
    due = 0
    for table in ("sources", "excerpts", "claims", "reports"):
        if table in present_tables and _has_column(conn, table, "refresh_due_at"):
            due += _scalar(
                conn,
                f"SELECT COUNT(*) FROM {table} WHERE refresh_due_at IS NOT NULL AND refresh_due_at <= ?",
                (now,),
            )
    return {
        "expired_sessions": expired,
        "expired_marked_fresh": expired_fresh,
        "refresh_due_records": due,
    }


def _backup_prerequisites(conn: DbConnection) -> dict[str, Any]:
    if conn.target.kind == "sqlite":
        journal_row = conn.execute("PRAGMA journal_mode").fetchone()
        journal_mode = str(
            journal_row[0]
            if not isinstance(journal_row, dict)
            else next(iter(journal_row.values()))
        )
        return {
            "online_backup_api": True,
            "journal_mode": journal_mode,
            "source_read_only": True,
        }
    return {
        "pg_dump_required": True,
        "pg_dump_available": shutil.which("pg_dump") is not None,
        "pg_restore_available": shutil.which("pg_restore") is not None,
        "source_read_only": True,
    }
