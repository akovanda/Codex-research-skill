from __future__ import annotations

from pathlib import Path

from research_registry import mcp_server
from research_registry.memory_retrieval_skill import MemoryRetrievalSkillHarness, optimization_gap_fill_bundle
from research_registry.seed_memory_retrieval import seed_memory_retrieval
from research_registry.service import RegistryService


def make_service(tmp_path: Path) -> RegistryService:
    service = RegistryService(tmp_path / "memory_retrieval_harness.sqlite3")
    service.initialize()
    return service


def test_reuse_scenario_preserves_claims_and_context(tmp_path: Path, monkeypatch) -> None:
    service = make_service(tmp_path)
    seeded = seed_memory_retrieval(service)
    monkeypatch.setattr(mcp_server, "service", service)
    harness = MemoryRetrievalSkillHarness(mcp_server)

    result = harness.research("Research LLM memory retrieval optimization and reranking precision.")

    assert result.mode == "reuse"
    assert seeded["rerank_finding_id"] in result.reused_finding_ids
    assert "## Knowledge To Reuse" in result.summary_md
    assert "High-recall retrieval should run first" in result.summary_md
    assert any("recall and precision" in point.lower() for point in result.context_points)
    assert result.summary_check.passed


def test_synthesis_scenario_builds_combined_report_for_optimization_context(tmp_path: Path, monkeypatch) -> None:
    service = make_service(tmp_path)
    seeded = seed_memory_retrieval(service)
    monkeypatch.setattr(mcp_server, "service", service)
    harness = MemoryRetrievalSkillHarness(mcp_server)

    result = harness.research("Research LLM memory retrieval optimization failure modes and mitigation context.")

    assert result.mode == "synthesis"
    assert seeded["rerank_finding_id"] in result.reused_finding_ids
    assert seeded["failure_report_id"] in result.reused_report_ids
    assert result.created_report_id is not None
    assert "stale indexes" in result.summary_md.lower()
    assert "broad retrieval and reranking" in result.summary_md.lower()
    assert result.summary_check.passed


def test_gap_fill_scenario_creates_artifacts_and_context_rich_summary(tmp_path: Path, monkeypatch) -> None:
    service = make_service(tmp_path)
    seed_memory_retrieval(service)
    monkeypatch.setattr(mcp_server, "service", service)
    harness = MemoryRetrievalSkillHarness(mcp_server)

    result = harness.research(
        "Research counterfactual retention scoring and temporal relevance metrics for long-term memory retrieval.",
        gap_fill=optimization_gap_fill_bundle(),
    )

    assert result.mode == "gap_fill"
    assert result.created_run_id is not None
    assert len(result.created_annotation_ids) == 2
    assert len(result.created_finding_ids) == 1
    assert result.created_report_id is not None
    assert "temporal relevance" in result.summary_md.lower()
    assert "recall, precision" in result.summary_md.lower()
    assert "https://example.org/optimization-recall-precision" in result.summary_md
    assert result.summary_check.passed
