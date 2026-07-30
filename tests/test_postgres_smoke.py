from __future__ import annotations

import os
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

import pytest

from research_registry.models import ClaimCreate, ExcerptCreate, FocusTuple, QuestionCreate, ReportCreate, ResearchSessionCreate, SourceCreate, SourceSelector
from research_registry.application.deposit import ResearchDepositService
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.service import RegistryService
from research_registry.data_audit import audit_database
from research_registry.retrieval.projection import rebuild_search_documents
from tests.fixtures.v1 import populate_v1_fixture
from tests.test_v2_deposit import _bundle


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
