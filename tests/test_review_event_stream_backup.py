from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from research_registry.application.review import ResearchReviewService
from research_registry.backup import (
    backup_sqlite,
    restore_sqlite_backup,
    sqlite_database_inventory,
)
from research_registry.persistence.conflict_state import (
    latest_effective_conflict_state,
)
from research_registry.persistence.review_state import (
    latest_effective_review_state,
)
from research_registry.service import RegistryService
from tests.fixtures.v2_review import seed_review_registry


_FIXED_TIME = datetime(2026, 8, 1, 0, 30, tzinfo=timezone.utc)


def test_review_event_stream_round_trips_through_verified_backup(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    manifest = tmp_path / "backup.manifest.json"
    key = "review-stream-backup"
    blob_root = tmp_path / f"{key}-blobs"
    registry, ids = seed_review_registry(
        tmp_path,
        key=key,
        database=source,
    )
    evidence_id = ids["supporting"]
    reviews = ResearchReviewService(
        registry.database,
        clock=lambda: _FIXED_TIME,
    )

    reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"{key}-approve-one",
            "entity": {"kind": "evidence", "id": evidence_id},
            "action": "approve",
            "expected_state": "unreviewed",
        }
    )
    reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"{key}-contest",
            "entity": {"kind": "evidence", "id": evidence_id},
            "action": "contest",
            "expected_state": "reviewed",
        }
    )
    reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"{key}-approve-two",
            "entity": {"kind": "evidence", "id": evidence_id},
            "action": "approve",
            "expected_state": "flagged",
        }
    )

    created = backup_sqlite(
        source,
        backup,
        manifest_path=manifest,
        blob_root=blob_root,
    )
    restore_sqlite_backup(
        backup,
        restored,
        manifest_path=manifest,
        verify=True,
        blob_root=blob_root,
    )

    restored_registry = RegistryService(restored)
    with registry.connect() as source_conn, restored_registry.connect() as restored_conn:
        source_inventory = sqlite_database_inventory(source_conn.raw_connection)
        restored_inventory = sqlite_database_inventory(
            restored_conn.raw_connection
        )
        source_stream = source_conn.execute(
            """
            SELECT re.action, re.created_at, res.stream_position
            FROM review_events re
            JOIN review_event_stream res ON res.event_id = re.id
            WHERE re.entity_kind = 'evidence' AND re.entity_id = ?
              AND re.action IN ('approve', 'contest', 'reject', 'supersede')
            ORDER BY res.stream_position
            """,
            (evidence_id,),
        ).fetchall()
        restored_stream = restored_conn.execute(
            """
            SELECT re.action, re.created_at, res.stream_position
            FROM review_events re
            JOIN review_event_stream res ON res.event_id = re.id
            WHERE re.entity_kind = 'evidence' AND re.entity_id = ?
              AND re.action IN ('approve', 'contest', 'reject', 'supersede')
            ORDER BY res.stream_position
            """,
            (evidence_id,),
        ).fetchall()
        restored_review = latest_effective_review_state(
            restored_conn,
            entity_kind="evidence",
            entity_id=evidence_id,
            fallback="unreviewed",
        )
        restored_conflict = latest_effective_conflict_state(
            restored_conn,
            entity_kind="evidence",
            entity_id=evidence_id,
        )

    assert source_inventory == restored_inventory
    assert "review_event_stream" in created["inventory"]["tables"]
    assert created["inventory"]["tables"]["review_event_stream"][
        "row_count"
    ] >= 3
    assert [tuple(row) for row in restored_stream] == [
        tuple(row) for row in source_stream
    ]
    assert [row["action"] for row in restored_stream] == [
        "approve",
        "contest",
        "approve",
    ]
    assert {row["created_at"] for row in restored_stream} == {
        _FIXED_TIME.isoformat()
    }
    assert restored_review == "reviewed"
    assert restored_conflict == "none"
