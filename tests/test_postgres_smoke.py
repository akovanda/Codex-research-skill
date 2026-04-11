from __future__ import annotations

import os
from uuid import uuid4

import pytest

from research_registry.models import ClaimCreate, ExcerptCreate, FocusTuple, QuestionCreate, ReportCreate, ResearchSessionCreate, SourceCreate, SourceSelector
from research_registry.service import RegistryService


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
