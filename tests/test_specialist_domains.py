from __future__ import annotations

from pathlib import Path

from research_registry.research_capture import RegistryBackendToolAdapter
from research_registry.service import RegistryService
from research_registry.specialist_domains import (
    build_domain_harness,
    inference_optimization_gap_fill_bundle,
    llm_evals_gap_fill_bundle,
    seed_specialist_domains,
)


def make_service(tmp_path: Path) -> RegistryService:
    service = RegistryService(tmp_path / "specialist_domains.sqlite3")
    service.initialize()
    return service


def test_seed_specialist_domains_exposes_reports(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    seeded = seed_specialist_domains(service)

    assert seeded["inference-optimization"]["report_id"]
    assert seeded["llm-evals"]["report_id"]

    inference_hits = service.search("speculative decoding latency", kind="finding", include_private=True)
    eval_hits = service.search("judge model calibration", kind="finding", include_private=True)

    assert any(hit.title == "Speculative decoding depends on acceptance rate" for hit in inference_hits.hits)
    assert any(hit.title == "Judge-model evals require human calibration" for hit in eval_hits.hits)


def test_inference_domain_specialist_reuses_existing_context(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    seed_specialist_domains(service)
    harness = build_domain_harness("inference-optimization", RegistryBackendToolAdapter(service))

    result = harness.research("Research LLM inference latency throughput tradeoffs and speculative decoding context.")

    assert result.mode in {"reuse", "synthesis"}
    assert result.summary_check.passed
    assert "## Knowledge To Reuse" in result.summary_md
    assert "acceptance rate" in result.summary_md.lower()
    assert any("tail-latency" in point.lower() or "tail latency" in point.lower() for point in result.context_points)


def test_llm_evals_domain_specialist_can_gap_fill_reliability_context(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    seed_specialist_domains(service)
    harness = build_domain_harness("llm-evals", RegistryBackendToolAdapter(service))

    result = harness.research(
        "Research audit sampling and online regression checks for LLM eval reliability.",
        gap_fill=llm_evals_gap_fill_bundle(),
    )

    assert result.mode == "gap_fill"
    assert result.created_run_id is not None
    assert result.created_report_id is not None
    assert result.summary_check.passed
    assert "audit sampling" in result.summary_md.lower()
    assert "online regression checks" in result.summary_md.lower()


def test_inference_domain_gap_fill_handles_quantization_metrics(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    seed_specialist_domains(service)
    harness = build_domain_harness("inference-optimization", RegistryBackendToolAdapter(service))

    result = harness.research(
        "Research quantization tail latency and interactive batching metrics for inference optimization.",
        gap_fill=inference_optimization_gap_fill_bundle(),
    )

    assert result.mode == "gap_fill"
    assert result.created_run_id is not None
    assert result.created_report_id is not None
    assert result.summary_check.passed
    assert "quantization" in result.summary_md.lower()
    assert "tail latency" in result.summary_md.lower()
