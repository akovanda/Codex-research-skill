from __future__ import annotations

from .config import load_settings
from .models import ClaimCreate, ExcerptCreate, FocusTuple, PublishRequest, QuestionCreate, ReportCreate, ResearchSessionCreate, ReviewRequest, SourceCreate, SourceSelector
from .service import RegistryService


def seed_memory_retrieval(service: RegistryService) -> dict[str, str]:
    existing = service.search("provenance freshness", kind="claim", include_private=True).hits
    if existing:
        return {}

    focus = FocusTuple(domain="memory-retrieval", object="provenance and freshness")
    question = service.create_question(
        QuestionCreate(
            prompt="What patterns matter most in memory and retrieval systems for LLM applications?",
            focus=focus,
        )
    )
    session = service.create_session(
        ResearchSessionCreate(
            question_id=question.id,
            prompt=question.prompt,
            model_name="gpt-5.4",
            model_version="2026-04-10",
            mode="live_research",
            notes="Seed corpus for the research-memory-retrieval flow.",
        )
    )

    rerank_source = service.create_source(
        SourceCreate(
            locator="https://example.org/rag-reranking-study",
            title="Retriever recall and reranker precision in RAG pipelines",
            source_type="paper",
            site_name="Example IR Lab",
            author="I. Researcher",
            snippet="Broad retrievers maximize recall while rerankers reduce irrelevant chunks before generation.",
            snapshot_required=True,
            snapshot_present=True,
        )
    )
    provenance_source = service.create_source(
        SourceCreate(
            locator="https://example.org/provenance-memory-rag",
            title="Why provenance matters for memory retrieval",
            source_type="official-docs",
            site_name="Example AI Docs",
            snippet="Retrieved memories should preserve source anchors and freshness metadata so downstream systems can judge trust.",
            snapshot_required=True,
            snapshot_present=True,
        )
    )

    rerank_excerpt = service.create_excerpt(
        ExcerptCreate(
            source_id=rerank_source.id,
            question_id=question.id,
            session_id=session.id,
            focal_label="vector retrieval and reranking",
            note="Broad retrieval and reranking should stay separate stages.",
            selector=SourceSelector(
                exact="Broad retrievers maximize recall while rerankers reduce irrelevant chunks before generation.",
                deep_link="https://example.org/rag-reranking-study#results",
            ),
            quote_text="Broad retrievers maximize recall while rerankers reduce irrelevant chunks before generation.",
            tags=["rag", "retrieval", "reranking"],
        )
    )
    provenance_excerpt = service.create_excerpt(
        ExcerptCreate(
            source_id=provenance_source.id,
            question_id=question.id,
            session_id=session.id,
            focal_label=focus.label or "memory provenance",
            note="Freshness and provenance metadata are required for trustworthy reuse.",
            selector=SourceSelector(
                exact="Retrieved memories should preserve source anchors and freshness metadata so downstream systems can judge trust.",
                deep_link="https://example.org/provenance-memory-rag#guidance",
            ),
            quote_text="Retrieved memories should preserve source anchors and freshness metadata so downstream systems can judge trust.",
            tags=["memory", "provenance", "freshness"],
        )
    )

    rerank_claim = service.create_claim(
        ClaimCreate(
            question_id=question.id,
            session_id=session.id,
            title="Reranking should follow broad retrieval",
            focal_label="vector retrieval and reranking",
            statement="High-recall retrieval should run first, with reranking applied after retrieval to improve precision before final context selection.",
            excerpt_ids=[rerank_excerpt.id],
        )
    )
    provenance_claim = service.create_claim(
        ClaimCreate(
            question_id=question.id,
            session_id=session.id,
            title="Memory retrieval needs provenance and freshness",
            focal_label=focus.label or "memory provenance",
            statement="Long-lived memory systems become unreliable when freshness and provenance metadata are missing from retrieved memories.",
            excerpt_ids=[provenance_excerpt.id],
        )
    )
    report = service.create_report(
        ReportCreate(
            question_id=question.id,
            session_id=session.id,
            title="What causes memory retrieval failures in long-lived LLM systems?",
            focal_label=focus.label or "memory provenance",
            summary_md=(
                "# What causes memory retrieval failures in long-lived LLM systems?\n\n"
                "## Direct Answer\n"
                "Missing provenance metadata and weak retrieval-stage separation both degrade trust and precision.\n"
            ),
            claim_ids=[provenance_claim.id, rerank_claim.id],
        )
    )

    for kind, record_id in [
        ("excerpt", rerank_excerpt.id),
        ("excerpt", provenance_excerpt.id),
        ("claim", rerank_claim.id),
        ("claim", provenance_claim.id),
        ("report", report.id),
    ]:
        service.review(ReviewRequest(kind=kind, record_id=record_id))

    service.publish(PublishRequest(kind="claim", record_id=rerank_claim.id, include_in_global_index=True))
    service.publish(PublishRequest(kind="report", record_id=report.id, include_in_global_index=True))

    return {
        "question_id": question.id,
        "rerank_claim_id": rerank_claim.id,
        "provenance_claim_id": provenance_claim.id,
        "report_id": report.id,
    }


def main() -> None:
    settings = load_settings()
    service = RegistryService(settings.db_path)
    service.initialize()
    seeded = seed_memory_retrieval(service)
    if seeded:
        print(f"seeded memory/retrieval report {seeded['report_id']}")
    else:
        print("memory/retrieval seed data already present")


if __name__ == "__main__":
    main()
