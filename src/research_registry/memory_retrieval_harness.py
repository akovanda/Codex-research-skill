from __future__ import annotations

import argparse
from pathlib import Path

from .research_capture import format_capture_summary, run_implicit_research_capture
from .service import RegistryService


SCENARIOS = (
    "reuse-optimization",
    "synthesis-failures",
    "gap-fill-metrics",
)


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


def run_scenario(service: RegistryService, scenario: str, *, source_roots: list[Path] | None = None) -> None:
    result = run_implicit_research_capture(scenario_prompt(scenario), backend=service, source_roots=source_roots)

    print(f"scenario={scenario}")
    print(f"mode={result.specialist_mode}")
    print(f"passed={result.summary_contract_passed}")
    print(format_capture_summary(result.capture_summary))
    if result.narrative_summary_md:
        print(result.narrative_summary_md)


def reset_db(db_path: Path) -> None:
    if db_path.exists():
        db_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a memory/retrieval research scenario against the registry.")
    parser.add_argument(
        "--scenario",
        choices=SCENARIOS,
        default="reuse-optimization",
        help="Built-in harness scenario to execute.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Execute all built-in memory/retrieval scenarios against the same registry database.",
    )
    parser.add_argument(
        "--db-path",
        default=".data/memory-retrieval-harness.sqlite3",
        help="SQLite database path for the harness run.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the target database before running the harness.",
    )
    parser.add_argument(
        "--source-root",
        action="append",
        help="Optional local repo or corpus root to search. Repeat for multiple roots.",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if args.reset:
        reset_db(db_path)
    service = build_service(db_path)
    scenarios = SCENARIOS if args.all else (args.scenario,)
    source_roots = [Path(path).expanduser().resolve() for path in args.source_root or []]

    for index, scenario in enumerate(scenarios, start=1):
        if index > 1:
            print("\n" + "=" * 80 + "\n")
        run_scenario(service, scenario, source_roots=source_roots or None)

    if args.all:
        print("\n" + "=" * 80 + "\n")
        print(f"completed={len(scenarios)}")
        print(f"database={db_path}")


if __name__ == "__main__":
    main()
