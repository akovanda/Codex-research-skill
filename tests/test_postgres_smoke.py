from __future__ import annotations

import os
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from research_registry.app import create_app
from research_registry.models import ApiKeyCreate, ClaimCreate, ExcerptCreate, FocusTuple, QuestionCreate, ReportCreate, ResearchSessionCreate, SourceCreate, SourceSelector
from research_registry.application.deposit import ResearchDepositService
from research_registry.application.postgres_errors import (
    postgres_deposit_error_message,
)
from research_registry.db import psycopg
from research_registry.application.migrate_v2 import run_v2_backfill
from research_registry.application.review import ResearchReviewService
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.persistence.read_adapter import (
    CurrentRetrievalAdapter,
    ReadAccess,
)
from research_registry.service import RegistryService
from research_registry.data_audit import audit_database
from research_registry.retrieval.projection import rebuild_search_documents
from tests.fixtures.v1 import populate_v1_fixture
from tests.fixtures.v2_review import seed_review_registry
from tests.test_shared_http_authorization import (
    _exercise_two_user_isolation,
    _settings,
)
from tests.test_source_version_review_state import (
    exercise_source_version_review_state_isolation,
)
from tests.test_review_event_sequence import (
    exercise_runtime_review_event_sequence,
)
from tests.test_exact_revision_state import (
    exercise_exact_revision_and_conflict_state,
)
from tests.test_publication_fail_closed import (
    exercise_public_parent_fail_closed,
)
from tests.test_v2_migration import _exercise_legacy_review_matrix
from tests.test_web_v2 import _exercise_global_admin_org_review
from tests.test_v2_deposit import _bundle
from tests.test_deposit_anchor_resolution import (
    test_postgres_deposit_anchor_resolution_and_atomic_rejection as _exercise_postgres_anchor_validation,
)


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres diagnostic classification requires TEST_DATABASE_URL",
)
def test_postgres_real_errors_receive_specific_safe_diagnostics() -> None:
    assert psycopg is not None
    service = RegistryService(os.environ["TEST_DATABASE_URL"])
    service.initialize()
    missing_table = f"rr_private_missing_{uuid4().hex}"

    with service.connect() as conn:
        with pytest.raises(psycopg.errors.UndefinedTable) as caught:
            conn.execute(f"SELECT * FROM {missing_table}")

    message = postgres_deposit_error_message(caught.value)
    assert message is not None
    assert message.startswith("DATABASE_SCHEMA_ERROR:")
    assert missing_table not in message


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres evidence-anchor validation requires TEST_DATABASE_URL",
)
def test_postgres_evidence_anchor_validation(tmp_path: Path) -> None:
    _exercise_postgres_anchor_validation(tmp_path)


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres publication boundary requires TEST_DATABASE_URL",
)
def test_postgres_public_parent_mutations_fail_closed(
    tmp_path: Path,
) -> None:
    exercise_public_parent_fail_closed(
        RegistryService(os.environ["TEST_DATABASE_URL"]),
        tmp_path,
        suffix=f"pg-{uuid4().hex[:10]}",
    )


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres exact-state matrix requires TEST_DATABASE_URL",
)
def test_postgres_exact_revision_and_conflict_state(tmp_path: Path) -> None:
    exercise_exact_revision_and_conflict_state(
        RegistryService(os.environ["TEST_DATABASE_URL"]),
        tmp_path,
        suffix=uuid4().hex[:10],
    )


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres source-version review isolation requires TEST_DATABASE_URL",
)
def test_postgres_source_version_review_state_isolation(tmp_path: Path) -> None:
    exercise_source_version_review_state_isolation(
        RegistryService(os.environ["TEST_DATABASE_URL"]),
        tmp_path,
        suffix=uuid4().hex[:10],
    )


@pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="postgres smoke test requires TEST_DATABASE_URL")
def test_postgres_backend_smoke() -> None:
    service = RegistryService(os.environ["TEST_DATABASE_URL"])
    service.initialize()

    suffix = uuid4().hex[:8]
    focus = FocusTuple(domain="memory-retrieval", object=f"postgres smoke {suffix}")
    question = service.create_question(QuestionCreate(prompt=f"Research postgres smoke {suffix}.", focus=focus))
    session = service.create_session(
        ResearchSessionCreate(
            question_id=question.id,
            prompt=question.prompt,
            model_name="gpt-5.4",
            model_version="2026-04-10",
            mode="live_research",
        )
    )
    source = service.create_source(
        SourceCreate(
            locator=f"https://example.com/postgres-smoke-{suffix}",
            title=f"Postgres smoke {suffix}",
            snippet="postgres smoke snippet",
            snapshot_present=True,
        )
    )
    excerpt = service.create_excerpt(
        ExcerptCreate(
            source_id=source.id,
            question_id=question.id,
            session_id=session.id,
            focal_label=focus.label or "postgres smoke",
            note="postgres smoke evidence",
            selector=SourceSelector(exact="postgres smoke", deep_link=f"https://example.com/postgres-smoke-{suffix}#1"),
            quote_text="postgres smoke",
        )
    )
    claim = service.create_claim(
        ClaimCreate(
            question_id=question.id,
            session_id=session.id,
            title=f"Postgres smoke claim {suffix}",
            focal_label=focus.label or "postgres smoke",
            statement=f"Postgres smoke claim {suffix} is stored.",
            excerpt_ids=[excerpt.id],
        )
    )
    report = service.create_report(
        ReportCreate(
            question_id=question.id,
            session_id=session.id,
            title=question.prompt,
            focal_label=focus.label or "postgres smoke",
            summary_md="# postgres smoke\n",
            claim_ids=[claim.id],
        )
    )

    hits = service.search(suffix, kind="report", include_private=True).hits
    assert report.id in [hit.id for hit in hits]
    assert service.database.kind == "postgres"


@pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="postgres fixture requires TEST_DATABASE_URL")
def test_postgres_representative_v1_fixture_and_read_only_audit() -> None:
    service = RegistryService(os.environ["TEST_DATABASE_URL"])
    suffix = f"postgres-fixture-{uuid4().hex[:8]}"
    ids = populate_v1_fixture(service, suffix=suffix)

    report = audit_database(os.environ["TEST_DATABASE_URL"])

    assert ids.annotation_id == ids.reviewed_excerpt_id
    assert ids.finding_id == ids.reviewed_claim_id
    assert report["database"]["kind"] == "postgres"
    assert report["row_counts"]["sources"] >= 2
    assert report["source_health"]["required_snapshot_missing"] >= 1


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres freshness parity requires TEST_DATABASE_URL",
)
def test_postgres_source_freshness_matches_sqlite_boundaries() -> None:
    service = RegistryService(os.environ["TEST_DATABASE_URL"])
    service.initialize()
    suffix = uuid4().hex[:8]
    now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    states = {
        f"src_pg_before_{suffix}": "2026-07-30T11:59:59+00:00",
        f"src_pg_exact_{suffix}": "2026-07-30T12:00:00+00:00",
        f"src_pg_after_{suffix}": "2026-07-30T12:00:01+00:00",
        f"src_pg_z_{suffix}": "2026-07-30T12:00:00Z",
        f"src_pg_west_future_{suffix}": "2026-07-30T05:30:00-07:00",
        f"src_pg_east_before_{suffix}": "2026-07-30T17:29:59+05:30",
        f"src_pg_east_after_{suffix}": "2026-07-30T17:30:01+05:30",
        f"src_pg_naive_{suffix}": "2026-07-30T12:00:00",
    }
    with service.connect() as conn:
        for source_id, due_at in states.items():
            conn.execute(
                """
                INSERT INTO sources (
                    id, locator, title, source_type, visibility,
                    refresh_due_at, created_at
                ) VALUES (?, ?, ?, 'note', 'private', ?,
                          '2026-07-30T00:00:00+00:00')
                """,
                (source_id, f"note:{source_id}", f"Freshness {source_id}", due_at),
            )
        rebuild_search_documents(conn, now=now)
        rows = conn.execute(
            "SELECT id, freshness FROM search_documents "
            "WHERE id IN ("
            + ",".join("?" for _ in states)
            + ") ORDER BY id",
            tuple(states),
        ).fetchall()
    actual = {row["id"]: row["freshness"] for row in rows}
    assert actual[f"src_pg_before_{suffix}"] == "needs_refresh"
    assert actual[f"src_pg_exact_{suffix}"] == "needs_refresh"
    assert actual[f"src_pg_after_{suffix}"] == "fresh"
    assert actual[f"src_pg_z_{suffix}"] == "needs_refresh"
    assert actual[f"src_pg_west_future_{suffix}"] == "fresh"
    assert actual[f"src_pg_east_before_{suffix}"] == "needs_refresh"
    assert actual[f"src_pg_east_after_{suffix}"] == "fresh"
    assert actual[f"src_pg_naive_{suffix}"] == "needs_refresh"


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres v2 deposit requires TEST_DATABASE_URL",
)
def test_postgres_v2_deposit_and_idempotent_replay(tmp_path) -> None:
    service = RegistryService(os.environ["TEST_DATABASE_URL"])
    service.initialize()
    suffix = uuid4().hex[:8]
    request = _bundle(key=f"postgres-deposit-{suffix}")
    request["inquiry"]["prompt"] += f" {suffix}"
    request["inquiry"]["topic_label"] += f" {suffix}"
    request["sources"][0]["identity"]["locator"] += f"-{suffix}"
    request["sources"][0]["identity"]["canonical_key"] += f"-{suffix}"
    request["sources"][0]["version"]["version_key"] += f"-{suffix}"
    request["sources"][0]["version"]["canonical_locator"] += f"-{suffix}"
    request["claims"][0]["canonical_key"] += f"-{suffix}"
    content = request["sources"][0]["version"]["snapshot"]["text"] + f" {suffix}"
    content_bytes = content.encode("utf-8")
    request["sources"][0]["version"]["content_sha256"] = sha256(
        content_bytes
    ).hexdigest()
    request["sources"][0]["version"]["snapshot"]["text"] = content
    request["sources"][0]["version"]["snapshot"]["byte_count"] = len(
        content_bytes
    )
    deposits = ResearchDepositService(
        service.database,
        FilesystemBlobStore(tmp_path / "postgres-smoke-blobs"),
    )

    committed = deposits.deposit(request)
    replay = deposits.deposit(request)

    assert committed.committed is True
    assert replay.idempotent_replay is True
    assert replay.records == committed.records


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres shared authorization requires TEST_DATABASE_URL",
)
def test_postgres_shared_http_mutation_authorization(
    tmp_path,
    monkeypatch,
) -> None:
    _exercise_two_user_isolation(
        _settings(tmp_path, os.environ["TEST_DATABASE_URL"]),
        monkeypatch,
    )


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres retained review requires TEST_DATABASE_URL",
)
def test_postgres_retained_review_idempotency_tracks_current_revision(
    tmp_path,
) -> None:
    app = create_app(_settings(tmp_path, os.environ["TEST_DATABASE_URL"]))
    service = app.state.service
    client = TestClient(app)
    suffix = uuid4().hex
    _, ids = seed_review_registry(
        tmp_path,
        key=f"postgres-retained-review-{suffix}",
        database=service.database.url,
    )
    issued = service.issue_api_key(
        ApiKeyCreate(
            label=f"postgres-reviewer-{suffix}",
            actor_user_id=f"postgres-reviewer-{suffix}",
            scopes=["admin", "read_private"],
        )
    )
    headers = {"x-api-key": issued.token}
    payload = {
        "kind": "claim",
        "record_id": ids["claim"],
        "reviewed": True,
    }
    assert client.post(
        "/api/review", headers=headers, json=payload
    ).status_code == 200
    contested = ResearchReviewService(service.database).review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"postgres-contest-{suffix}",
            "entity": {
                "kind": "claim_revision",
                "id": ids["revision"],
            },
            "action": "contest",
            "expected_revision_id": ids["revision"],
            "expected_state": "reviewed",
        }
    )
    assert client.post(
        "/api/review", headers=headers, json=payload
    ).status_code == 200
    assert client.post(
        "/api/review", headers=headers, json=payload
    ).status_code == 200
    with service.connect() as conn:
        events = conn.execute(
            """
            SELECT action FROM review_events
            WHERE entity_kind = 'claim_revision'
              AND entity_id IN (?, ?)
            """,
            (ids["revision"], contested.current_revision_id),
        ).fetchall()
    assert sorted(row["action"] for row in events) == [
        "approve",
        "approve",
        "contest",
    ]


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres retained evidence review requires TEST_DATABASE_URL",
)
def test_postgres_retained_evidence_review_uses_append_only_effective_state(
    tmp_path,
) -> None:
    app = create_app(_settings(tmp_path, os.environ["TEST_DATABASE_URL"]))
    service = app.state.service
    client = TestClient(app)
    suffix = uuid4().hex
    _, ids = seed_review_registry(
        tmp_path,
        key=f"postgres-retained-evidence-review-{suffix}",
        database=service.database.url,
    )
    issued = service.issue_api_key(
        ApiKeyCreate(
            label=f"postgres-evidence-reviewer-{suffix}",
            actor_user_id=f"postgres-evidence-reviewer-{suffix}",
            scopes=["admin", "read_private"],
        )
    )
    headers = {"x-api-key": issued.token}
    retained_payload = {
        "kind": "excerpt",
        "record_id": ids["excerpt"],
        "reviewed": True,
    }

    first = client.post(
        "/api/review", headers=headers, json=retained_payload
    )
    contested = ResearchReviewService(service.database).review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"postgres-contest-evidence-{suffix}",
            "entity": {
                "kind": "evidence",
                "id": ids["supporting"],
            },
            "action": "contest",
            "expected_state": "reviewed",
        }
    )
    second = client.post(
        "/api/review", headers=headers, json=retained_payload
    )
    replay = client.post(
        "/api/review", headers=headers, json=retained_payload
    )

    assert first.status_code == 200
    assert contested.event_id
    assert second.status_code == 200
    assert replay.status_code == 200
    with service.connect() as conn:
        events = conn.execute(
            """
            SELECT action, from_state, to_state
            FROM review_events
            WHERE entity_kind = 'evidence' AND entity_id = ?
            ORDER BY created_at, id
            """,
            (ids["supporting"],),
        ).fetchall()
        evidence = conn.execute(
            "SELECT review_state FROM evidence_spans WHERE id = ?",
            (ids["supporting"],),
        ).fetchone()
        excerpt = conn.execute(
            """
            SELECT review_state, human_reviewed
            FROM excerpts WHERE id = ?
            """,
            (ids["excerpt"],),
        ).fetchone()
    effective = CurrentRetrievalAdapter(service.database).get_record(
        ids["supporting"],
        access=ReadAccess(include_private=True, local_trusted=True),
    )

    assert [
        (row["action"], row["from_state"], row["to_state"])
        for row in events
    ] == [
        ("approve", "unreviewed", "reviewed"),
        ("contest", "reviewed", "flagged"),
        ("approve", "flagged", "reviewed"),
    ]
    assert evidence["review_state"] == "unreviewed"
    assert (excerpt["review_state"], excerpt["human_reviewed"]) == (
        "reviewed",
        1,
    )
    assert effective is not None
    assert effective.review_state == "reviewed"


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres global admin organization review requires TEST_DATABASE_URL",
)
def test_postgres_global_admin_can_review_org_records_and_refresh(
    tmp_path,
) -> None:
    _exercise_global_admin_org_review(
        _settings(tmp_path, os.environ["TEST_DATABASE_URL"]),
        tmp_path,
    )


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres legacy review matrix requires TEST_DATABASE_URL",
)
def test_postgres_backfill_normalizes_legacy_review_state_matrix() -> None:
    _exercise_legacy_review_matrix(
        RegistryService(os.environ["TEST_DATABASE_URL"]),
        suffix=f"postgres-review-matrix-{uuid4().hex[:8]}",
    )


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres pre-0006 adoption requires TEST_DATABASE_URL",
)
def test_postgres_pre_0006_multi_revision_adoption_is_stable(
    tmp_path,
) -> None:
    service = RegistryService(os.environ["TEST_DATABASE_URL"])
    service.initialize()
    suffix = uuid4().hex
    request = _bundle(key=f"postgres-adoption-first-{suffix}")
    request["inquiry"]["prompt"] += f" {suffix}"
    request["inquiry"]["topic_label"] += f" {suffix}"
    request["sources"][0]["identity"]["locator"] += f"-{suffix}"
    request["sources"][0]["identity"]["canonical_key"] += f"-{suffix}"
    request["sources"][0]["version"]["version_key"] += f"-{suffix}"
    request["sources"][0]["version"]["canonical_locator"] += f"-{suffix}"
    request["claims"][0]["canonical_key"] += f"-{suffix}"
    content = request["sources"][0]["version"]["snapshot"]["text"] + suffix
    request["sources"][0]["version"]["snapshot"]["text"] = content
    request["sources"][0]["version"]["snapshot"]["byte_count"] = len(
        content.encode()
    )
    request["sources"][0]["version"]["content_sha256"] = sha256(
        content.encode()
    ).hexdigest()
    deposits = ResearchDepositService(
        service.database,
        FilesystemBlobStore(tmp_path / f"postgres-adoption-{suffix}"),
    )
    first = deposits.deposit(request)
    claim_id = first.records.claim_ids["claim"]
    first_revision = first.records.claim_revision_ids["claim"]
    second_request = _bundle(
        key=f"postgres-adoption-second-{suffix}",
        claim_id=claim_id,
        expected_revision_id=first_revision,
        title="Postgres second native revision",
    )
    second_request["inquiry"] = request["inquiry"]
    second_request["sources"] = request["sources"]
    second_request["claims"][0]["canonical_key"] = (
        request["claims"][0]["canonical_key"]
    )
    second = deposits.deposit(second_request)
    second_revision = second.records.claim_revision_ids["claim"]
    contested = ResearchReviewService(service.database).review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"postgres-adoption-review-{suffix}",
            "entity": {
                "kind": "claim_revision",
                "id": second_revision,
            },
            "action": "contest",
            "expected_revision_id": second_revision,
            "expected_state": "unreviewed",
        }
    )
    review_revision = contested.current_revision_id
    with service.connect() as conn:
        expected_links = {
            revision_id: conn.execute(
                """
                SELECT evidence_span_id, relationship
                FROM claim_evidence
                WHERE claim_revision_id = ?
                ORDER BY evidence_span_id, relationship
                """,
                (revision_id,),
            ).fetchall()
            for revision_id in (
                first_revision,
                second_revision,
                review_revision,
            )
        }
        conn.execute(
            "DROP TRIGGER IF EXISTS review_events_assign_stream_position "
            "ON review_events"
        )
        conn.execute(
            "DROP FUNCTION IF EXISTS append_review_event_stream_position()"
        )
        conn.execute(
            "DROP TRIGGER IF EXISTS review_event_stream_immutable "
            "ON review_event_stream"
        )
        conn.execute("DROP TABLE IF EXISTS review_event_stream")
        conn.execute("DROP TABLE legacy_projection_identity")
        conn.execute(
            """
            DELETE FROM schema_migrations
            WHERE migration_id IN (
                '0006_v2_legacy_projection_identity',
                '0007_v2_review_event_stream'
            )
            """
        )
    service.initialize()
    for _ in range(2):
        run_v2_backfill(service.database_url, resume=True)
        with service.connect() as conn:
            claim = conn.execute(
                """
                SELECT current_revision_id FROM claims WHERE id = ?
                """,
                (claim_id,),
            ).fetchone()
            mapping = conn.execute(
                """
                SELECT v2_id FROM legacy_projection_identity
                WHERE legacy_kind = 'claim' AND legacy_id = ?
                """,
                (claim_id,),
            ).fetchone()
            links = {
                revision_id: conn.execute(
                    """
                    SELECT evidence_span_id, relationship
                    FROM claim_evidence
                    WHERE claim_revision_id = ?
                    ORDER BY evidence_span_id, relationship
                    """,
                    (revision_id,),
                ).fetchall()
                for revision_id in expected_links
            }
        assert claim["current_revision_id"] == review_revision
        assert mapping["v2_id"] == review_revision
        assert {
            key: [tuple(row) for row in value]
            for key, value in links.items()
        } == {
            key: [tuple(row) for row in value]
            for key, value in expected_links.items()
        }

@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres review-event stream requires TEST_DATABASE_URL",
)
def test_postgres_same_timestamp_review_events_use_stream_order(
    tmp_path: Path,
) -> None:
    exercise_runtime_review_event_sequence(
        os.environ["TEST_DATABASE_URL"],
        tmp_path,
        key=f"postgres-review-stream-{uuid4().hex}",
    )
