from __future__ import annotations

from .config import load_settings
from .models import AnnotationCreate, FindingCreate, PublishRequest, ReportCompileCreate, ReviewRequest, RunCreate, SourceCreate, SourceSelector
from .service import RegistryService


def seed_memory_retrieval(service: RegistryService) -> dict[str, str]:
    existing = service.search("reranking", kind="finding", include_private=True).hits
    if any(hit.title == "Reranking should follow broad retrieval" for hit in existing):
        return {}

    run = service.create_run(
        RunCreate(
            question="What patterns matter most in memory and retrieval systems for LLM applications?",
            model_name="gpt-5.4",
            model_version="2026-04-10",
            notes="Seed corpus for the research-memory-retrieval skill dry run.",
        )
    )

    rerank_source = service.create_source(
        SourceCreate(
            canonical_url="https://example.org/rag-reranking-study",
            title="Retriever recall and reranker precision in RAG pipelines",
            source_type="paper",
            site_name="Example IR Lab",
            author="I. Researcher",
            snippet="Broad retrievers maximize recall while rerankers reduce irrelevant chunks before generation.",
            content_sha256=service._hash_text("retriever recall reranker precision"),
            snapshot_url="https://archive.example.org/rag-reranking-study",
            snapshot_required=True,
            snapshot_present=True,
            visibility="private",
        )
    )
    stale_index_source = service.create_source(
        SourceCreate(
            canonical_url="https://example.org/stale-index-memory",
            title="Stale indexes cause retrieval failures in long-lived memory systems",
            source_type="paper",
            site_name="Example Systems Journal",
            author="S. Engineer",
            snippet="Embedding drift and delayed reindexing reduce both recall and trust in retrieved memories.",
            content_sha256=service._hash_text("stale indexes embedding drift delayed reindexing"),
            snapshot_url="https://archive.example.org/stale-index-memory",
            snapshot_required=True,
            snapshot_present=True,
            visibility="private",
        )
    )
    provenance_source = service.create_source(
        SourceCreate(
            canonical_url="https://example.org/provenance-memory-rag",
            title="Why provenance matters for memory retrieval",
            source_type="official-docs",
            site_name="Example AI Docs",
            snippet="Retrieved memories should preserve source anchors and freshness metadata so downstream systems can judge trust.",
            content_sha256=service._hash_text("retrieved memories preserve source anchors freshness metadata"),
            snapshot_url="https://archive.example.org/provenance-memory-rag",
            snapshot_required=True,
            snapshot_present=True,
            visibility="private",
        )
    )

    rerank_annotation_one = service.create_annotation(
        AnnotationCreate(
            source_id=rerank_source.id,
            run_id=run.id,
            subject="vector retrieval and reranking",
            note="This passage supports using a broad retriever for recall and reranking for precision before context assembly.",
            selector=SourceSelector(
                exact="Broad retrievers maximize recall while rerankers reduce irrelevant chunks before generation.",
                deep_link="https://example.org/rag-reranking-study#results",
            ),
            confidence=0.9,
            model_name="gpt-5.4",
            model_version="2026-04-10",
            tags=["rag", "retrieval", "reranking", "precision", "recall"],
        )
    )
    rerank_annotation_two = service.create_annotation(
        AnnotationCreate(
            source_id=rerank_source.id,
            run_id=run.id,
            subject="vector retrieval and reranking",
            note="The result implies that retrieval and reranking should be tuned as separate stages rather than collapsed into one heuristic.",
            selector=SourceSelector(
                exact="Broad retrievers maximize recall while rerankers reduce irrelevant chunks before generation.",
                deep_link="https://example.org/rag-reranking-study#discussion",
                start=24,
                end=108,
            ),
            confidence=0.83,
            model_name="gpt-5.4",
            model_version="2026-04-10",
            tags=["rag", "reranking"],
        )
    )
    stale_annotation = service.create_annotation(
        AnnotationCreate(
            source_id=stale_index_source.id,
            run_id=run.id,
            subject="stale index retrieval failures",
            note="This evidence links stale embeddings and delayed reindexing to retrieval drift in persistent memory systems.",
            selector=SourceSelector(
                exact="Embedding drift and delayed reindexing reduce both recall and trust in retrieved memories.",
                deep_link="https://example.org/stale-index-memory#findings",
            ),
            confidence=0.88,
            model_name="gpt-5.4",
            model_version="2026-04-10",
            tags=["memory", "indexing", "freshness"],
        )
    )
    provenance_annotation = service.create_annotation(
        AnnotationCreate(
            source_id=provenance_source.id,
            run_id=run.id,
            subject="memory provenance",
            note="This supports requiring freshness and source anchors in memory retrieval so reused information remains auditable.",
            selector=SourceSelector(
                exact="Retrieved memories should preserve source anchors and freshness metadata so downstream systems can judge trust.",
                deep_link="https://example.org/provenance-memory-rag#guidance",
            ),
            confidence=0.86,
            model_name="gpt-5.4",
            model_version="2026-04-10",
            tags=["memory", "provenance", "freshness"],
        )
    )

    rerank_finding = service.create_finding(
        FindingCreate(
            title="Reranking should follow broad retrieval",
            subject="vector retrieval and reranking",
            claim="High-recall retrieval should run first, with reranking applied after retrieval to improve precision before final context selection.",
            annotation_ids=[rerank_annotation_one.id, rerank_annotation_two.id],
            model_name="gpt-5.4",
            model_version="2026-04-10",
            run_id=run.id,
        )
    )
    failure_finding = service.create_finding(
        FindingCreate(
            title="Stale indexes and weak provenance make memory retrieval unreliable",
            subject="memory retrieval failures",
            claim="Long-lived memory systems fail when indexes drift or freshness/provenance metadata is missing, because retrieval quality and trust both degrade.",
            annotation_ids=[stale_annotation.id, provenance_annotation.id],
            model_name="gpt-5.4",
            model_version="2026-04-10",
            run_id=run.id,
        )
    )
    failure_report = service.compile_report(
        ReportCompileCreate(
            question="What causes memory retrieval failures in long-lived LLM systems?",
            subject="memory retrieval failures",
            finding_ids=[failure_finding.id],
            model_name="gpt-5.4",
            model_version="2026-04-10",
            run_id=run.id,
        )
    )

    for kind, record_id in [
        ("annotation", rerank_annotation_one.id),
        ("annotation", rerank_annotation_two.id),
        ("annotation", stale_annotation.id),
        ("annotation", provenance_annotation.id),
        ("finding", rerank_finding.id),
        ("finding", failure_finding.id),
        ("report", failure_report.id),
    ]:
        service.review(ReviewRequest(kind=kind, record_id=record_id))

    service.publish(PublishRequest(kind="finding", record_id=rerank_finding.id))
    service.publish(PublishRequest(kind="report", record_id=failure_report.id))

    return {
        "run_id": run.id,
        "rerank_finding_id": rerank_finding.id,
        "failure_finding_id": failure_finding.id,
        "failure_report_id": failure_report.id,
    }


def main() -> None:
    settings = load_settings()
    service = RegistryService(settings.db_path)
    service.initialize()
    seeded = seed_memory_retrieval(service)
    if seeded:
        print(f"seeded memory/retrieval report {seeded['failure_report_id']}")
    else:
        print("memory/retrieval seed data already present")


if __name__ == "__main__":
    main()
