from __future__ import annotations

import argparse
from pathlib import Path

from .research_capture import format_capture_summary, run_implicit_research_capture
from .service import RegistryService


SCENARIOS = (
    "memory-reuse",
    "memory-synthesis",
    "memory-gap-fill",
    "inference-reuse",
    "inference-gap-fill",
    "evals-reuse",
    "evals-gap-fill",
)


def build_service(db_path: Path) -> RegistryService:
    service = RegistryService(db_path)
    service.initialize()
    return service


def scenario_prompt(scenario: str) -> str:
    prompts = {
        "memory-reuse": "Research LLM memory retrieval optimization and reranking precision.",
        "memory-synthesis": "Research LLM memory retrieval optimization failure modes and mitigation context.",
        "memory-gap-fill": "Research counterfactual retention scoring and temporal relevance metrics for long-term memory retrieval.",
        "inference-reuse": "Research LLM inference latency throughput tradeoffs and speculative decoding context.",
        "inference-gap-fill": "Research quantization tail latency and interactive batching metrics for inference optimization.",
        "evals-reuse": "Research judge model calibration and benchmark drift in LLM evaluations.",
        "evals-gap-fill": "Research audit sampling and online regression checks for LLM eval reliability.",
    }
    return prompts[scenario]


def run_scenario(service: RegistryService, scenario: str) -> None:
    result = run_implicit_research_capture(scenario_prompt(scenario), backend=service)

    print(f"scenario={scenario}")
    print(f"domain={result.specialized_domain}")
    print(f"mode={result.specialist_mode}")
    print(f"passed={result.summary_contract_passed}")
    print(format_capture_summary(result.capture_summary))
    if result.narrative_summary_md:
        print(result.narrative_summary_md)


def reset_db(db_path: Path) -> None:
    if db_path.exists():
        db_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run specialist-domain research scenarios against the registry.")
    parser.add_argument(
        "--scenario",
        choices=SCENARIOS,
        default="memory-reuse",
        help="Built-in specialist-domain scenario to execute.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Execute all built-in scenarios against the same registry database.",
    )
    parser.add_argument(
        "--db-path",
        default=".data/domain-research-harness.sqlite3",
        help="SQLite database path for the harness run.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the target database before running the harness.",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if args.reset:
        reset_db(db_path)
    service = build_service(db_path)
    scenarios = SCENARIOS if args.all else (args.scenario,)

    for index, scenario in enumerate(scenarios, start=1):
        if index > 1:
            print("\n" + "=" * 80 + "\n")
        run_scenario(service, scenario)

    if args.all:
        print("\n" + "=" * 80 + "\n")
        print(f"completed={len(scenarios)}")
        print(f"database={db_path}")


if __name__ == "__main__":
    main()
