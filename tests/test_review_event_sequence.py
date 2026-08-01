from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from research_registry.application.review import ResearchReviewService
from research_registry.backup import V2_AUTHORITATIVE_TABLES
from research_registry.migration_runner import MigrationRunner, load_sql_migrations
from research_registry.persistence.conflict_state import (
    latest_effective_conflict_state,
)
from research_registry.persistence.review_state import (
    latest_effective_review_state,
)
from research_registry.service import RegistryService
from tests.fixtures.v2_review import seed_review_registry


_FIXED_TIME = datetime(2026, 7, 31, 23, 59, 59, tzinfo=timezone.utc)


def exercise_runtime_review_event_sequence(
    database: str | Path,
    tmp_path: Path,
    *,
    key: str,
) -> None:
    registry, ids = seed_review_registry(
        tmp_path,
        key=key,
        database=database,
    )
    reviews = ResearchReviewService(
        registry.database,
        clock=lambda: _FIXED_TIME,
    )
    evidence_id = ids["supporting"]

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
    final_request = {
        "protocol": "research-review/v2",
        "idempotency_key": f"{key}-approve-two",
        "entity": {"kind": "evidence", "id": evidence_id},
        "action": "approve",
        "expected_state": "flagged",
    }
    applied = reviews.review(final_request)
    replay = reviews.review(final_request)

    assert applied.idempotent_replay is False
    assert replay.idempotent_replay is True
    with registry.connect() as conn:
        rows = conn.execute(
            """
            SELECT re.id, re.action, re.created_at, res.stream_position
            FROM review_events re
            JOIN review_event_stream res ON res.event_id = re.id
            WHERE re.entity_kind = 'evidence' AND re.entity_id = ?
              AND re.action IN ('approve', 'contest', 'reject', 'supersede')
            ORDER BY res.stream_position
            """,
            (evidence_id,),
        ).fetchall()
        effective_review = latest_effective_review_state(
            conn,
            entity_kind="evidence",
            entity_id=evidence_id,
            fallback="unreviewed",
        )
        effective_conflict = latest_effective_conflict_state(
            conn,
            entity_kind="evidence",
            entity_id=evidence_id,
        )
        unmapped = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM review_events re
            LEFT JOIN review_event_stream res ON res.event_id = re.id
            WHERE res.event_id IS NULL
            """
        ).fetchone()["count"]

    assert [row["action"] for row in rows] == [
        "approve",
        "contest",
        "approve",
    ]
    positions = [int(row["stream_position"]) for row in rows]
    assert positions == sorted(positions)
    assert len(set(positions)) == len(positions)
    assert {row["created_at"] for row in rows} == {_FIXED_TIME.isoformat()}
    assert effective_review == "reviewed"
    assert effective_conflict == "none"
    assert unmapped == 0


def test_runtime_same_timestamp_decisions_use_stream_order(
    tmp_path: Path,
) -> None:
    exercise_runtime_review_event_sequence(
        tmp_path / "runtime.sqlite3",
        tmp_path,
        key="sqlite-runtime-review-stream",
    )


def test_migration_backfills_previous_tie_break_and_keeps_stream_append_only(
    tmp_path: Path,
) -> None:
    service = RegistryService(tmp_path / "migration.sqlite3")
    runner = MigrationRunner(service)
    with service.connect() as conn:
        applied = runner.migrate(
            conn,
            target="0006_v2_legacy_projection_identity",
        )
    assert applied.applied_ids[-1] == "0006_v2_legacy_projection_identity"

    timestamp = "2026-07-30T00:00:00+00:00"
    with service.connect() as conn:
        for values in (
            ("rev_a", "approve", "reviewed", "human"),
            ("rev_z", "reject", "flagged", "human"),
            ("rev_0", "contest", "conflicted", "migration"),
        ):
            conn.execute(
                """
                INSERT INTO review_events (
                    id, entity_kind, entity_id, action, from_state, to_state,
                    actor_type, created_at, metadata_json
                ) VALUES (?, 'evidence', 'evd_tied', ?, 'unreviewed', ?,
                          ?, ?, '{}')
                """,
                (values[0], values[1], values[2], values[3], timestamp),
            )

    with service.connect() as conn:
        migrated = runner.migrate(conn)
    assert migrated.applied_ids == ("0007_v2_review_event_stream",)

    with service.connect() as conn:
        rows = conn.execute(
            """
            SELECT re.id, res.stream_position
            FROM review_events re
            JOIN review_event_stream res ON res.event_id = re.id
            WHERE re.entity_id = 'evd_tied'
            ORDER BY res.stream_position
            """
        ).fetchall()
        assert [row["id"] for row in rows] == ["rev_a", "rev_z", "rev_0"]
        assert latest_effective_review_state(
            conn,
            entity_kind="evidence",
            entity_id="evd_tied",
            fallback="unreviewed",
        ) == "flagged"
        assert latest_effective_conflict_state(
            conn,
            entity_kind="evidence",
            entity_id="evd_tied",
        ) == "conflicted"

        conn.execute(
            """
            INSERT INTO review_events (
                id, entity_kind, entity_id, action, from_state, to_state,
                actor_type, created_at, metadata_json
            ) VALUES (
                'rev_new', 'evidence', 'evd_tied', 'approve', 'flagged',
                'reviewed', 'human', ?, '{}'
            )
            """,
            (timestamp,),
        )
        newest = conn.execute(
            """
            SELECT re.id, res.stream_position
            FROM review_events re
            JOIN review_event_stream res ON res.event_id = re.id
            WHERE re.entity_id = 'evd_tied'
            ORDER BY res.stream_position DESC
            LIMIT 1
            """
        ).fetchone()
        assert newest["id"] == "rev_new"
        assert latest_effective_review_state(
            conn,
            entity_kind="evidence",
            entity_id="evd_tied",
            fallback="unreviewed",
        ) == "reviewed"
        assert latest_effective_conflict_state(
            conn,
            entity_kind="evidence",
            entity_id="evd_tied",
        ) == "none"

        with pytest.raises(
            sqlite3.IntegrityError,
            match="review event stream is append-only",
        ):
            conn.execute(
                """
                UPDATE review_event_stream
                SET stream_position = stream_position + 100
                WHERE event_id = 'rev_new'
                """
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="review events are append-only",
        ):
            conn.execute(
                """
                UPDATE review_events SET note = 'changed'
                WHERE id = 'rev_new'
                """
            )

    migration = next(
        item
        for item in load_sql_migrations()
        if item.migration_id == "0007_v2_review_event_stream"
    )
    assert migration.selected_files("sqlite") == ("common.sql", "sqlite.sql")
    assert migration.selected_files("postgres") == (
        "common.sql",
        "postgres.sql",
    )
    assert "review_event_stream" in V2_AUTHORITATIVE_TABLES


def test_stream_trigger_maps_direct_review_event_inserts(tmp_path: Path) -> None:
    service = RegistryService(tmp_path / "direct.sqlite3")
    service.initialize()
    with service.connect() as conn:
        conn.execute(
            """
            INSERT INTO review_events (
                id, entity_kind, entity_id, action, from_state, to_state,
                actor_type, created_at, metadata_json
            ) VALUES (
                ?, 'report', ?, 'approve', 'unreviewed', 'reviewed',
                'system', '2026-07-31T00:00:00+00:00', '{}'
            )
            """,
            (f"rev_{uuid4()}", f"rpt_{uuid4()}"),
        )
        mapped = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM review_events re
            JOIN review_event_stream res ON res.event_id = re.id
            """
        ).fetchone()["count"]
    assert mapped == 1
