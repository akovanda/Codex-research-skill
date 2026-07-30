from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from research_registry.data_audit import (
    audit_database,
    connect_database_read_only,
    render_audit_markdown,
)
from research_registry.service import RegistryService
from tests.fixtures.v1 import populate_v1_fixture, weaken_sqlite_v1_fixture


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_audit_is_repeatable_read_only_and_redacted(tmp_path: Path) -> None:
    database_path = tmp_path / "representative.sqlite3"
    service = RegistryService(database_path)
    ids = populate_v1_fixture(service, suffix="audit-secret")
    follow_up = service.get_question(ids.follow_up_question_id, include_private=True)
    stale_session = service.get_session(ids.stale_session_id, include_private=True)
    snapshotted = service.get_source(ids.snapshotted_source_id, include_private=True)
    missing_snapshot = service.get_source(
        ids.missing_snapshot_source_id,
        include_private=True,
    )
    conflicted_claim = service.get_claim(ids.conflicted_claim_id, include_private=True)
    refreshed_report = service.get_report(ids.refreshed_report_id, include_private=True)

    assert ids.annotation_id == ids.reviewed_excerpt_id
    assert ids.finding_id == ids.reviewed_claim_id
    assert follow_up.parent_question_id == ids.root_question_id
    assert follow_up.generated_by_session_id == ids.stale_session_id
    assert follow_up.follow_up_status == "ready"
    assert stale_session.refresh_of_session_id == ids.fresh_session_id
    assert stale_session.is_stale is True
    assert snapshotted.visibility == "public"
    assert snapshotted.review_state == "reviewed"
    assert missing_snapshot.snapshot_required is True
    assert missing_snapshot.snapshot_present is False
    assert missing_snapshot.review_state == "flagged"
    assert missing_snapshot.visibility == "private"
    assert conflicted_claim.conflict_state == "conflicted"
    assert refreshed_report.refresh_of_report_id == ids.report_id
    weaken_sqlite_v1_fixture(database_path, ids)
    before = _file_sha256(database_path)

    first = audit_database(database_path)
    second = audit_database(database_path)
    markdown = render_audit_markdown(first)

    assert first == second
    assert _file_sha256(database_path) == before
    assert first["database"]["kind"] == "sqlite"
    assert first["database"]["integrity"]["foreign_key_violations"] == 1
    assert first["row_counts"]["sources"] >= 2
    assert first["row_counts"]["audit_log"] >= 1
    assert first["orphans"]["excerpts.source_id"] == 1
    assert first["relationship_gaps"]["claims_without_excerpts"] == 1
    assert first["relationship_gaps"]["reports_without_claims"] == 1
    assert first["source_health"]["missing_content_sha256"] >= 1
    assert first["source_health"]["required_snapshot_missing"] == 1
    assert first["selector_health"]["malformed_json"] == 1
    assert first["invalid_enums"]["sources.review_state"] == 1
    assert first["distributions"]["visibility"]["sources"]["private"] >= 1
    assert first["distributions"]["visibility"]["sources"]["public"] >= 1
    assert first["freshness"]["expired_sessions"] == 1
    assert first["legacy_alias_usage"]["observable"] is False

    rendered = json.dumps(first, sort_keys=True) + markdown
    for private_value in (
        "Private fixture prompt sentinel audit-secret.",
        "Private fixture quote sentinel audit-secret.",
        "Private fixture claim sentinel audit-secret.",
        "Private fixture report sentinel audit-secret",
        "private_query=fixture-secret",
        "legacy_unknown",
    ):
        assert private_value not in rendered


def test_audit_nonexistent_sqlite_does_not_create_database(tmp_path: Path) -> None:
    database_path = tmp_path / "missing.sqlite3"

    with pytest.raises(FileNotFoundError):
        audit_database(database_path)

    assert not database_path.exists()


def test_sqlite_audit_connection_rejects_writes(tmp_path: Path) -> None:
    database_path = tmp_path / "readonly.sqlite3"
    service = RegistryService(database_path)
    service.initialize()

    with connect_database_read_only(database_path) as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly|read-only|attempt to write"):
            conn.execute("CREATE TABLE forbidden_write (id TEXT)")


def test_audit_reports_duplicate_dedupe_keys_without_key_values(tmp_path: Path) -> None:
    database_path = tmp_path / "weak-legacy.sqlite3"
    with sqlite3.connect(database_path) as conn:
        conn.executescript(
            """
            CREATE TABLE sources (
                id TEXT PRIMARY KEY,
                content_sha256 TEXT,
                snapshot_required INTEGER,
                snapshot_present INTEGER,
                review_state TEXT,
                trust_tier TEXT,
                conflict_state TEXT,
                visibility TEXT,
                namespace_kind TEXT,
                public_index_state TEXT,
                dedupe_key TEXT
            );
            """
        )
        for source_id in ("src_one", "src_two"):
            conn.execute(
                """
                INSERT INTO sources (
                    id, snapshot_required, snapshot_present, review_state,
                    trust_tier, conflict_state, visibility, namespace_kind,
                    public_index_state, dedupe_key
                ) VALUES (?, 0, 0, 'unreviewed', 'low', 'none', 'private',
                          'user', 'private', ?)
                """,
                (source_id, "private-duplicate-key"),
            )

    report = audit_database(database_path)
    rendered = json.dumps(report, sort_keys=True)

    assert report["duplicate_keys"]["sources"] == {
        "duplicate_groups": 1,
        "duplicate_rows": 2,
    }
    assert "private-duplicate-key" not in rendered
