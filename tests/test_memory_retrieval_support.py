from __future__ import annotations

from pathlib import Path

from research_registry import mcp_server
from research_registry.models import SourceCreate
from research_registry.seed_memory_retrieval import seed_memory_retrieval
from research_registry.service import RegistryService


def make_service(tmp_path: Path) -> RegistryService:
    service = RegistryService(tmp_path / "memory_retrieval.sqlite3")
    service.initialize()
    return service


def test_seed_memory_retrieval_exposes_existing_topics(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    seeded = seed_memory_retrieval(service)

    assert seeded["rerank_finding_id"]
    rerank_hits = service.search("vector retrieval reranking", kind="finding", include_private=True)
    failure_hits = service.search("stale indexes memory retrieval failures", kind="report", include_private=True)

    assert any(hit.title == "Reranking should follow broad retrieval" for hit in rerank_hits.hits)
    assert any(hit.title == "What causes memory retrieval failures in long-lived LLM systems?" for hit in failure_hits.hits)


def test_mcp_supports_run_creation_record_fetch_and_provenance(tmp_path: Path, monkeypatch) -> None:
    service = make_service(tmp_path)
    monkeypatch.setattr(mcp_server, "service", service)

    run = mcp_server.create_run(
        {
            "question": "How should agent memory be evaluated?",
            "model_name": "gpt-5.4",
            "model_version": "2026-04-10",
        }
    )
    annotation = mcp_server.add_annotation(
        {
            "run_id": run["id"],
            "source": SourceCreate(
                canonical_url="https://example.org/agent-memory-metrics",
                title="Evaluating memory in agents",
                source_type="paper",
                snapshot_required=True,
                snapshot_present=True,
            ).model_dump(mode="json"),
            "subject": "agent memory evaluation",
            "note": "Recall and temporal relevance both matter when evaluating memory systems.",
            "selector": {
                "exact": "Recall and temporal relevance both matter when evaluating memory systems.",
                "deep_link": "https://example.org/agent-memory-metrics#results",
            },
            "model_name": "gpt-5.4",
            "model_version": "2026-04-10",
            "tags": ["memory", "evaluation"],
        }
    )
    finding = mcp_server.create_finding(
        {
            "title": "Memory evaluation needs recall and temporal relevance",
            "subject": "agent memory evaluation",
            "claim": "Evaluating memory quality requires at least recall and temporal relevance, not just retrieval latency.",
            "annotation_ids": [annotation["id"]],
            "run_id": run["id"],
            "model_name": "gpt-5.4",
            "model_version": "2026-04-10",
        }
    )
    report = mcp_server.compile_report(
        {
            "question": "How should agent memory systems be evaluated?",
            "subject": "agent memory evaluation",
            "finding_ids": [finding["id"]],
            "run_id": run["id"],
            "model_name": "gpt-5.4",
            "model_version": "2026-04-10",
        }
    )

    fetched_annotation = mcp_server.get_annotation(annotation["id"])
    fetched_finding = mcp_server.get_finding(finding["id"])
    fetched_report = mcp_server.get_report(report["id"])

    assert run["id"].startswith("run_")
    assert fetched_annotation["run_id"] == run["id"]
    assert fetched_annotation["anchor_fingerprint"]
    assert fetched_annotation["quote_hash"]
    assert fetched_annotation["selector"]["deep_link"].endswith("#results")
    assert fetched_finding["annotation_ids"] == [annotation["id"]]
    assert fetched_report["finding_ids"] == [finding["id"]]
    assert fetched_report["annotation_ids"] == [annotation["id"]]


def test_gap_filling_topic_can_create_new_memory_research_artifacts(tmp_path: Path, monkeypatch) -> None:
    service = make_service(tmp_path)
    seed_memory_retrieval(service)
    monkeypatch.setattr(mcp_server, "service", service)

    existing = mcp_server.search("counterfactual retention scoring", kind="finding", include_private=True)
    assert existing["hits"] == []

    run = mcp_server.create_run(
        {
            "question": "Which metrics matter for agent memory evaluation?",
            "model_name": "gpt-5.4",
            "model_version": "2026-04-10",
        }
    )
    first_annotation = mcp_server.add_annotation(
        {
            "run_id": run["id"],
            "source": {
                "canonical_url": "https://example.org/agent-memory-eval-metrics",
                "title": "Measuring memory retrieval in agents",
                "source_type": "paper",
                "snapshot_required": True,
                "snapshot_present": True,
            },
            "subject": "agent memory evaluation",
            "note": "Recall and precision should be tracked separately because a fast memory system can still retrieve the wrong items.",
            "selector": {
                "exact": "Recall and precision should be tracked separately because a fast memory system can still retrieve the wrong items.",
                "deep_link": "https://example.org/agent-memory-eval-metrics#evaluation",
            },
            "model_name": "gpt-5.4",
            "model_version": "2026-04-10",
            "tags": ["memory", "precision", "recall"],
        }
    )
    second_annotation = mcp_server.add_annotation(
        {
            "run_id": run["id"],
            "source": {
                "canonical_url": "https://example.org/agent-memory-temporal-relevance",
                "title": "Temporal relevance in long-term memory evaluation",
                "source_type": "paper",
                "snapshot_required": True,
                "snapshot_present": True,
            },
            "subject": "agent memory evaluation",
            "note": "Temporal relevance matters because stale memories can be correctly retrieved yet still harm reasoning.",
            "selector": {
                "exact": "Temporal relevance matters because stale memories can be correctly retrieved yet still harm reasoning.",
                "deep_link": "https://example.org/agent-memory-temporal-relevance#discussion",
            },
            "model_name": "gpt-5.4",
            "model_version": "2026-04-10",
            "tags": ["memory", "freshness"],
        }
    )
    finding = mcp_server.create_finding(
        {
            "title": "Agent memory evaluation needs more than latency",
            "subject": "agent memory evaluation",
            "claim": "A useful memory evaluation suite should include recall, precision, and temporal relevance instead of latency alone.",
            "annotation_ids": [first_annotation["id"], second_annotation["id"]],
            "run_id": run["id"],
            "model_name": "gpt-5.4",
            "model_version": "2026-04-10",
        }
    )

    created_hits = mcp_server.search("agent memory evaluation", kind="finding", include_private=True)
    assert any(hit["id"] == finding["id"] for hit in created_hits["hits"])
