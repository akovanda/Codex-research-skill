from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

import pytest

from research_registry.application.migrate_v2 import (
    BackfillResumeRequired,
    InjectedBackfillInterruption,
    run_v2_backfill,
)
from research_registry.cli import build_parser, main as cli_main
from research_registry.migration_runner import MigrationRunner
from research_registry.persistence.repositories import (
    V2BackfillRepository,
    V2ReadRepository,
)
from research_registry.models import SourceCreate
from research_registry.service import RegistryService
from tests.fixtures.v1 import populate_v1_fixture, weaken_sqlite_v1_fixture


V2_TABLES = {
    "content_objects",
    "source_versions",
    "evidence_spans",
    "claim_revisions",
    "claim_evidence",
    "review_events",
    "refresh_queue",
    "idempotency_keys",
    "legacy_projection_identity",
    "migration_backfill_progress",
    "migration_backfill_warnings",
    "migration_backfill_errors",
}


def _counts(service: RegistryService) -> dict[str, int]:
    tables = (
        "sources",
        "excerpts",
        "claims",
        "claim_excerpts",
        "source_versions",
        "evidence_spans",
        "claim_revisions",
        "claim_evidence",
    )
    with service.connect() as conn:
        return {
            table: int(
                conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()[
                    "count"
                ]
            )
            for table in tables
        }


def _v1_snapshot(service: RegistryService, ids) -> dict[str, object]:
    return {
        "sources": [
            service.get_source(ids.snapshotted_source_id, include_private=True).model_dump(
                mode="json"
            ),
            service.get_source(
                ids.missing_snapshot_source_id, include_private=True
            ).model_dump(mode="json"),
        ],
        "excerpts": [
            service.get_excerpt(ids.reviewed_excerpt_id, include_private=True).model_dump(
                mode="json"
            ),
            service.get_excerpt(ids.flagged_excerpt_id, include_private=True).model_dump(
                mode="json"
            ),
        ],
        "claims": [
            service.get_claim(ids.reviewed_claim_id, include_private=True).model_dump(
                mode="json"
            ),
            service.get_claim(ids.conflicted_claim_id, include_private=True).model_dump(
                mode="json"
            ),
        ],
    }


def test_v2_schema_is_additive_and_dialect_bundle_is_applied(tmp_path: Path) -> None:
    service = RegistryService(tmp_path / "schema.sqlite3")
    service.initialize()

    with service.connect() as conn:
        tables = service._list_tables(conn)
        claim_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(claims)").fetchall()
        }
        applied = conn.execute(
            "SELECT checksum_sha256 FROM schema_migrations "
            "WHERE migration_id = '0003_v2_evidence'"
        ).fetchone()

    assert V2_TABLES <= tables
    assert {"sources", "excerpts", "claims", "claim_excerpts"} <= tables
    assert {
        "canonical_key",
        "current_revision_id",
        "scope_json",
        "updated_at",
    } <= claim_columns
    assert applied is not None
    assert len(applied["checksum_sha256"]) == 64


def test_idempotency_namespace_migration_backfills_alpha_rows_as_user(
    tmp_path: Path,
) -> None:
    service = RegistryService(tmp_path / "idempotency-upgrade.sqlite3")
    runner = MigrationRunner(service)
    with service.connect() as conn:
        runner.migrate(conn, target="0004_v2_search")
        conn.execute(
            """
            INSERT INTO idempotency_keys (
                namespace_id, operation, "key", request_sha256,
                response_json, created_at
            ) VALUES ('same-id', 'research_deposit_v2', 'alpha-key', ?,
                      '{}', '2026-07-30T00:00:00+00:00')
            """,
            ("a" * 64,),
        )
        conn.commit()
        runner.migrate(conn)
        row = conn.execute(
            "SELECT namespace_kind, namespace_id FROM idempotency_keys"
        ).fetchone()
        columns = conn.execute("PRAGMA table_info(idempotency_keys)").fetchall()
    assert dict(row) == {"namespace_kind": "user", "namespace_id": "same-id"}
    assert [
        column["name"]
        for column in sorted(columns, key=lambda item: item["pk"])
        if column["pk"]
    ] == ["namespace_kind", "namespace_id", "operation", "key"]


def test_backfill_requires_explicit_schema_migration(tmp_path: Path) -> None:
    database = tmp_path / "unmigrated.sqlite3"
    sqlite3.connect(database).close()

    with pytest.raises(
        RuntimeError,
        match="v2 schema migrations are not applied",
    ):
        run_v2_backfill(database)

    with sqlite3.connect(database) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert tables == set()


def test_backfill_freshness_compares_offset_timestamps_as_instants(
    tmp_path: Path,
) -> None:
    service = RegistryService(tmp_path / "offset-backfill.sqlite3")
    service.initialize()
    future = service.create_source(
        SourceCreate(
            locator="note:backfill-offset-future",
            title="Offset future",
        )
    )
    overdue = service.create_source(
        SourceCreate(
            locator="note:backfill-offset-overdue",
            title="Offset overdue",
        )
    )
    with service.connect() as conn:
        conn.execute(
            "UPDATE sources SET refresh_due_at = ? WHERE id = ?",
            ("2026-07-30T05:30:00-07:00", future.id),
        )
        conn.execute(
            "UPDATE sources SET refresh_due_at = ? WHERE id = ?",
            ("2026-07-30T17:29:59+05:30", overdue.id),
        )
        repository = V2BackfillRepository(
            conn, now_text="2026-07-30T12:00:00Z"
        )
        for source_id in (future.id, overdue.id):
            row = conn.execute(
                "SELECT * FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
            repository.process_row("source_versions", row)
        queued = conn.execute(
            """
            SELECT entity_id FROM refresh_queue
            WHERE reason = 'expired' ORDER BY entity_id
            """
        ).fetchall()
    assert [row["entity_id"] for row in queued] == [overdue.id]


def test_immutable_v2_records_and_review_events_are_database_enforced(
    tmp_path: Path,
) -> None:
    service = RegistryService(tmp_path / "immutable.sqlite3")
    ids = populate_v1_fixture(service, suffix="immutable")
    run_v2_backfill(service.database_url)

    with service.connect() as conn:
        source_version = conn.execute(
            "SELECT id FROM source_versions WHERE source_id = ?",
            (ids.snapshotted_source_id,),
        ).fetchone()
        evidence = conn.execute(
            "SELECT id FROM evidence_spans ORDER BY id LIMIT 1",
        ).fetchone()
        revision = conn.execute(
            "SELECT current_revision_id FROM claims WHERE id = ?",
            (ids.reviewed_claim_id,),
        ).fetchone()
        event = conn.execute(
            "SELECT id FROM review_events LIMIT 1"
        ).fetchone()

        for table, record_id in (
            ("source_versions", source_version["id"]),
            ("evidence_spans", evidence["id"]),
            ("claim_revisions", revision["current_revision_id"]),
            ("review_events", event["id"]),
        ):
            with pytest.raises(
                sqlite3.IntegrityError,
                match="immutable|append-only",
            ):
                conn.execute(
                    f"UPDATE {table} SET created_at = created_at WHERE id = ?",
                    (record_id,),
                )


def test_backfill_maps_once_preserves_v1_reads_and_exposes_v2(
    tmp_path: Path,
) -> None:
    service = RegistryService(tmp_path / "representative.sqlite3")
    ids = populate_v1_fixture(service, suffix="v2-map")
    before_v1 = _v1_snapshot(service, ids)
    before_counts = _counts(service)

    result = run_v2_backfill(service.database_url, batch_size=1)
    after_counts = _counts(service)
    after_v1 = _v1_snapshot(service, ids)

    assert result.status == "completed"
    assert result.error_count == 0
    assert before_v1 == after_v1
    assert after_counts["sources"] == before_counts["sources"]
    assert after_counts["excerpts"] == before_counts["excerpts"]
    assert after_counts["claims"] == before_counts["claims"]
    assert after_counts["claim_excerpts"] == before_counts["claim_excerpts"]
    assert after_counts["source_versions"] == before_counts["sources"]
    assert after_counts["evidence_spans"] == before_counts["excerpts"]
    assert after_counts["claim_revisions"] == before_counts["claims"]
    assert after_counts["claim_evidence"] == before_counts["claim_excerpts"]

    with service.connect() as conn:
        repository = V2ReadRepository(conn)
        source_version = repository.get_source_version(
            ids.missing_snapshot_source_id
        )
        evidence = repository.get_evidence_for_legacy_excerpt(
            ids.reviewed_excerpt_id
        )
        reviewed_revision = repository.get_current_claim_revision(
            ids.reviewed_claim_id
        )
        contested_revision = repository.get_current_claim_revision(
            ids.conflicted_claim_id
        )
        relationships = repository.list_claim_evidence(ids.reviewed_claim_id)
        warning_codes = {
            row["code"]
            for row in conn.execute(
                "SELECT code FROM migration_backfill_warnings"
            ).fetchall()
        }
        claim_pointer = conn.execute(
            "SELECT current_revision_id FROM claims WHERE id = ?",
            (ids.reviewed_claim_id,),
        ).fetchone()
        foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()

    assert source_version.source_id == ids.missing_snapshot_source_id
    assert source_version.metadata["snapshot_policy"] == "metadata_only"
    assert "legacy_metadata_hash" in source_version.metadata["migration_warnings"]
    assert evidence.metadata["legacy_excerpt_id"] == ids.reviewed_excerpt_id
    assert evidence.quote_sha256
    assert evidence.selector["type"] == "text_quote"
    assert evidence.review_state == "reviewed"
    assert evidence.trust_tier == "high"
    assert reviewed_revision.revision_number == 1
    assert reviewed_revision.status == "supported"
    assert contested_revision.status == "contested"
    assert relationships[0].relationship == "supports"
    assert claim_pointer["current_revision_id"] == reviewed_revision.id
    assert foreign_key_errors == []
    assert {
        "missing_legacy_hash",
        "legacy_metadata_hash",
        "missing_snapshot",
    } <= warning_codes

    rerun = run_v2_backfill(service.database_url, batch_size=2, resume=True)
    assert rerun.status == "completed"
    assert _counts(service) == after_counts


def test_backfill_interruption_rolls_back_batch_and_resume_completes(
    tmp_path: Path,
) -> None:
    service = RegistryService(tmp_path / "resume.sqlite3")
    populate_v1_fixture(service, suffix="resume")

    with pytest.raises(InjectedBackfillInterruption):
        run_v2_backfill(
            service.database_url,
            batch_size=1,
            interrupt_after_batches=2,
        )

    with service.connect() as conn:
        source_count = conn.execute(
            "SELECT COUNT(*) AS count FROM source_versions"
        ).fetchone()["count"]
        source_progress = conn.execute(
            "SELECT processed_count, status FROM migration_backfill_progress "
            "WHERE phase = 'source_versions'"
        ).fetchone()

    assert source_count == 1
    assert source_progress["processed_count"] == 1
    assert source_progress["status"] == "running"

    with pytest.raises(BackfillResumeRequired):
        run_v2_backfill(service.database_url, batch_size=1)

    resumed = run_v2_backfill(
        service.database_url,
        batch_size=1,
        resume=True,
    )
    counts = _counts(service)
    assert resumed.status == "completed"
    assert counts["source_versions"] == counts["sources"]
    assert counts["evidence_spans"] == counts["excerpts"]
    assert counts["claim_revisions"] == counts["claims"]
    assert counts["claim_evidence"] == counts["claim_excerpts"]


def test_malformed_selector_is_preserved_as_unverified_evidence(
    tmp_path: Path,
) -> None:
    service = RegistryService(tmp_path / "malformed.sqlite3")
    ids = populate_v1_fixture(service, suffix="malformed")
    original_selector = "{malformed-private-selector"
    with service.connect() as conn:
        conn.execute(
            "UPDATE excerpts SET selector_json = ? WHERE id = ?",
            (original_selector, ids.reviewed_excerpt_id),
        )

    result = run_v2_backfill(service.database_url, batch_size=20)

    with service.connect() as conn:
        repository = V2ReadRepository(conn)
        evidence = repository.get_evidence_for_legacy_excerpt(
            ids.reviewed_excerpt_id
        )
        warning = conn.execute(
            "SELECT details_json FROM migration_backfill_warnings "
            "WHERE legacy_id = ? AND code = 'malformed_legacy_selector'",
            (ids.reviewed_excerpt_id,),
        ).fetchone()

    assert result.error_count == 0
    assert evidence.anchor_state == "unverified"
    assert evidence.selector == {
        "exact": f"Private fixture quote sentinel malformed.",
        "type": "text_quote",
    }
    assert evidence.metadata["legacy_selector_json"] == original_selector
    assert json.loads(warning["details_json"]) == {"field": "selector_json"}


def test_unrepresentable_legacy_relationship_is_reported_without_deletion(
    tmp_path: Path,
) -> None:
    database = tmp_path / "weak.sqlite3"
    service = RegistryService(database)
    ids = populate_v1_fixture(service, suffix="weak-private-sentinel")
    weaken_sqlite_v1_fixture(database, ids)

    result = run_v2_backfill(service.database_url, batch_size=1)

    with service.connect() as conn:
        original = conn.execute(
            "SELECT source_id, quote_text FROM excerpts WHERE id = ?",
            (ids.flagged_excerpt_id,),
        ).fetchone()
        error_codes = {
            row["code"]
            for row in conn.execute(
                "SELECT code FROM migration_backfill_errors"
            ).fetchall()
        }
        report_warning = conn.execute(
            "SELECT id FROM migration_backfill_warnings "
            "WHERE code = 'unresolved_report_evidence'"
        ).fetchone()

    assert result.status == "incomplete"
    assert result.error_count >= 1
    assert original["source_id"] == "src_missing_fixture"
    assert original["quote_text"] == "Synthetic flagged quote weak-private-sentinel."
    assert "legacy_source_version_missing" in error_codes
    assert report_warning is not None
    serialized = json.dumps(result.to_dict())
    assert "weak-private-sentinel" not in serialized
    assert "quote_text" not in serialized


def test_v2_read_repository_synthesizes_without_writing_before_backfill(
    tmp_path: Path,
) -> None:
    service = RegistryService(tmp_path / "dual-read.sqlite3")
    ids = populate_v1_fixture(service, suffix="dual-read")

    with service.connect() as conn:
        repository = V2ReadRepository(conn)
        source_version = repository.get_source_version(
            ids.snapshotted_source_id
        )
        evidence = repository.get_evidence_for_legacy_excerpt(
            ids.reviewed_excerpt_id
        )
        revision = repository.get_current_claim_revision(ids.reviewed_claim_id)
        persisted_count = conn.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM source_versions) + "
            "(SELECT COUNT(*) FROM evidence_spans) + "
            "(SELECT COUNT(*) FROM claim_revisions) AS count"
        ).fetchone()["count"]

    assert source_version.persisted is False
    assert evidence.persisted is False
    assert revision.persisted is False
    assert persisted_count == 0


def test_cli_migrate_v2_data_is_bounded_and_content_free(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    service = RegistryService(tmp_path / "cli.sqlite3")
    populate_v1_fixture(service, suffix="cli-private-sentinel")
    args = build_parser().parse_args(
        [
            "migrate-v2-data",
            "--database",
            service.database_url,
            "--batch-size",
            "1",
            "--resume",
            "--json",
        ]
    )
    assert args.batch_size == 1
    assert args.resume is True

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "research-registry",
            "migrate-v2-data",
            "--database",
            service.database_url,
            "--batch-size",
            "1",
            "--resume",
            "--json",
        ],
    )
    cli_main()
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert payload["status"] == "completed"
    assert payload["database_kind"] == "sqlite"
    assert "cli-private-sentinel" not in output
    assert "quote" not in output
    assert "statement" not in output


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres v2 migration parity requires TEST_DATABASE_URL",
)
def test_postgres_v2_backfill_logical_fixture_parity() -> None:
    service = RegistryService(os.environ["TEST_DATABASE_URL"])
    suffix = f"v2-postgres-{uuid4().hex[:8]}"
    ids = populate_v1_fixture(service, suffix=suffix)

    result = run_v2_backfill(service.database_url, batch_size=1, resume=True)

    with service.connect() as conn:
        repository = V2ReadRepository(conn)
        source_version = repository.get_source_version(
            ids.snapshotted_source_id
        )
        evidence = repository.get_evidence_for_legacy_excerpt(
            ids.reviewed_excerpt_id
        )
        revision = repository.get_current_claim_revision(ids.reviewed_claim_id)
        relationships = repository.list_claim_evidence(ids.reviewed_claim_id)

    assert result.error_count == 0
    assert source_version.source_id == ids.snapshotted_source_id
    assert evidence.metadata["legacy_excerpt_id"] == ids.reviewed_excerpt_id
    assert revision.revision_number == 1
    assert len(relationships) == 1
    assert relationships[0].relationship == "supports"
