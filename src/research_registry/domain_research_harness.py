from __future__ import annotations

import argparse
from pathlib import Path

from . import mcp_server
from .memory_retrieval_skill import MemoryRetrievalSkillHarness, optimization_gap_fill_bundle
from .research_capture import RegistryBackendToolAdapter
from .seed_memory_retrieval import seed_memory_retrieval
from .service import RegistryService
from .specialist_domains import (
    build_domain_harness,
    inference_optimization_gap_fill_bundle,
    llm_evals_gap_fill_bundle,
    seed_specialist_domains,
)


def build_harnesses(db_path: Path):
    service = RegistryService(db_path)
    service.initialize()
    seed_memory_retrieval(service)
    seed_specialist_domains(service)
    mcp_server.service = service
    adapter = RegistryBackendToolAdapter(service)
    return {
        "memory-retrieval": MemoryRetrievalSkillHarness(mcp_server),
        "inference-optimization": build_domain_harness("inference-optimization", adapter),
        "llm-evals": build_domain_harness("llm-evals", adapter),
    }


def run_scenario(harnesses: dict[str, object], scenario: str):
    if scenario == "memory-reuse":
        return harnesses["memory-retrieval"].research("Research LLM memory retrieval optimization and reranking precision.")
    if scenario == "memory-synthesis":
        return harnesses["memory-retrieval"].research("Research LLM memory retrieval optimization failure modes and mitigation context.")
    if scenario == "memory-gap-fill":
        return harnesses["memory-retrieval"].research(
            "Research counterfactual retention scoring and temporal relevance metrics for long-term memory retrieval.",
            gap_fill=optimization_gap_fill_bundle(),
        )
    if scenario == "inference-reuse":
        return harnesses["inference-optimization"].research("Research LLM inference latency throughput tradeoffs and speculative decoding context.")
    if scenario == "inference-gap-fill":
        return harnesses["inference-optimization"].research(
            "Research quantization tail latency and interactive batching metrics for inference optimization.",
            gap_fill=inference_optimization_gap_fill_bundle(),
        )
    if scenario == "evals-reuse":
        return harnesses["llm-evals"].research("Research judge model calibration and benchmark drift in LLM evaluations.")
    if scenario == "evals-gap-fill":
        return harnesses["llm-evals"].research(
            "Research audit sampling and online regression checks for LLM eval reliability.",
            gap_fill=llm_evals_gap_fill_bundle(),
        )
    raise ValueError(f"unknown scenario: {scenario}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run specialist-domain research harness scenarios against the registry.")
    parser.add_argument(
        "--scenario",
        choices=[
            "memory-reuse",
            "memory-synthesis",
            "memory-gap-fill",
            "inference-reuse",
            "inference-gap-fill",
            "evals-reuse",
            "evals-gap-fill",
        ],
        default="memory-reuse",
        help="Built-in specialist-domain scenario to execute.",
    )
    parser.add_argument(
        "--db-path",
        default=".data/domain-research-harness.sqlite3",
        help="SQLite database path for the harness run.",
    )
    args = parser.parse_args()

    harnesses = build_harnesses(Path(args.db_path))
    result = run_scenario(harnesses, args.scenario)

    print(f"mode={result.mode}")
    print(f"passed={result.summary_check.passed}")
    print(f"created_report_id={result.created_report_id or 'none'}")
    print(result.summary_md)


if __name__ == "__main__":
    main()
