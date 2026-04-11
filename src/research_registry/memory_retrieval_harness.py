from __future__ import annotations

import argparse
from pathlib import Path

from .research_capture import format_capture_summary, run_implicit_research_capture
from .service import RegistryService


def build_service(db_path: Path) -> RegistryService:
    service = RegistryService(db_path)
    service.initialize()
    return service


def scenario_prompt(scenario: str) -> str:
    if scenario == "reuse-optimization":
        return "Research LLM memory retrieval optimization and reranking precision."
    if scenario == "synthesis-failures":
        return "Research LLM memory retrieval optimization failure modes and mitigation context."
    if scenario == "gap-fill-metrics":
        return "Research optimization metrics for long-term memory retrieval."
    raise ValueError(f"unknown scenario: {scenario}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a memory/retrieval research scenario against the registry.")
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

    service = build_service(Path(args.db_path))
    result = run_implicit_research_capture(scenario_prompt(args.scenario), backend=service)

    print(f"mode={result.specialist_mode}")
    print(f"passed={result.summary_contract_passed}")
    print(format_capture_summary(result.capture_summary))
    if result.narrative_summary_md:
        print(result.narrative_summary_md)


if __name__ == "__main__":
    main()
