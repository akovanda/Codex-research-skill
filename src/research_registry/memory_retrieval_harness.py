from __future__ import annotations

import argparse
from pathlib import Path

from . import mcp_server
from .memory_retrieval_skill import MemoryRetrievalSkillHarness, optimization_gap_fill_bundle
from .seed_memory_retrieval import seed_memory_retrieval
from .service import RegistryService


def build_harness(db_path: Path) -> MemoryRetrievalSkillHarness:
    service = RegistryService(db_path)
    service.initialize()
    seed_memory_retrieval(service)
    mcp_server.service = service
    return MemoryRetrievalSkillHarness(mcp_server)


def run_scenario(harness: MemoryRetrievalSkillHarness, scenario: str):
    if scenario == "reuse-optimization":
        return harness.research("Research LLM memory retrieval optimization and reranking precision.")
    if scenario == "synthesis-failures":
        return harness.research("Research LLM memory retrieval optimization failure modes and mitigation context.")
    if scenario == "gap-fill-metrics":
        return harness.research(
            "Research optimization metrics for long-term memory retrieval.",
            gap_fill=optimization_gap_fill_bundle(),
        )
    raise ValueError(f"unknown scenario: {scenario}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the memory/retrieval specialist skill harness against the registry.")
    parser.add_argument(
        "--scenario",
        choices=["reuse-optimization", "synthesis-failures", "gap-fill-metrics"],
        default="reuse-optimization",
        help="Built-in harness scenario to execute.",
    )
    parser.add_argument(
        "--db-path",
        default=".data/memory-retrieval-harness.sqlite3",
        help="SQLite database path for the harness run.",
    )
    args = parser.parse_args()

    harness = build_harness(Path(args.db_path))
    result = run_scenario(harness, args.scenario)

    print(f"mode={result.mode}")
    print(f"passed={result.summary_check.passed}")
    print(f"created_report_id={result.created_report_id or 'none'}")
    print(result.summary_md)


if __name__ == "__main__":
    main()
