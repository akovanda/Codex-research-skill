from __future__ import annotations

from pathlib import Path

import research_registry.local_research as local_research
from research_registry.research_pass_runner import build_seeded_service, execute_passes
from research_registry.research_pass_suite import ResearchPassSpec


def make_grounded_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "grounded"
    (repo / "docs").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "docs" / "benchmarks.md").write_text(
        "Judge model calibration matters when the answer model and judge model are the same local model.\n"
        "Start-index slicing helps benchmark window sampling under CPU budget constraints.\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_memory.md").write_text(
        "Branch-private memory isolation is verified in coding branch scenarios.\n"
        "Typed-anchor memory models keep rivalry and obligation threads separate.\n",
        encoding="utf-8",
    )
    return repo


def test_research_pass_runner_executes_two_rounds_with_reuse_shift(tmp_path: Path) -> None:
    repo = make_grounded_repo(tmp_path)
    specs = [
        ResearchPassSpec(
            pass_id="memory-branch-private-isolation",
            wave=1,
            theme="Retrieval Mechanics",
            prompt="Research branch-private memory isolation strategies for divergent narrative and coding branches.",
            why_it_matters="Branch isolation is a concrete product requirement.",
            expected_domain="memory-retrieval",
            expected_initial_outcome="live_research",
            source_signals=["grounded: branch-private memory isolation is verified in coding branch scenarios"],
        ),
        ResearchPassSpec(
            pass_id="eval-judge-model-calibration-local",
            wave=1,
            theme="Benchmark Fit",
            prompt="Research judge model calibration risks when the answer model and judge model are the same local model in benchmark runs.",
            why_it_matters="Same-model judging can distort release decisions.",
            expected_domain="llm-evals",
            expected_initial_outcome="live_research",
            source_signals=["grounded: judge model calibration matters when the answer model and judge model are the same local model"],
        ),
    ]

    service = build_seeded_service(tmp_path / "research_pass_runner.sqlite3", reset=True)
    report = execute_passes(service, specs, rounds=2, source_roots=[repo])

    assert len(report.executions) == 4
    assert len(report.round_summaries) == 2
    assert all(row.actual_domain == row.expected_domain for row in report.executions)
    assert all(row.summary_contract_passed is True for row in report.executions)

    round_1, round_2 = report.round_summaries
    assert round_1.total_passes == 2
    assert round_2.total_passes == 2
    assert round_1.mode_counts.get("live_research", 0) == 2
    assert round_2.mode_counts.get("reuse", 0) == 2
    assert round_2.reused_records > round_1.reused_records
    assert all(item.mode_changed for item in report.transitions)


def test_research_pass_runner_executes_two_rounds_without_rg(tmp_path: Path, monkeypatch) -> None:
    repo = make_grounded_repo(tmp_path)
    specs = [
        ResearchPassSpec(
            pass_id="memory-branch-private-isolation",
            wave=1,
            theme="Retrieval Mechanics",
            prompt="Research branch-private memory isolation strategies for divergent narrative and coding branches.",
            why_it_matters="Branch isolation is a concrete product requirement.",
            expected_domain="memory-retrieval",
            expected_initial_outcome="live_research",
            source_signals=["grounded: branch-private memory isolation is verified in coding branch scenarios"],
        ),
        ResearchPassSpec(
            pass_id="eval-judge-model-calibration-local",
            wave=1,
            theme="Benchmark Fit",
            prompt="Research judge model calibration risks when the answer model and judge model are the same local model in benchmark runs.",
            why_it_matters="Same-model judging can distort release decisions.",
            expected_domain="llm-evals",
            expected_initial_outcome="live_research",
            source_signals=["grounded: judge model calibration matters when the answer model and judge model are the same local model"],
        ),
    ]

    def missing_rg(term: str, root: Path):
        raise FileNotFoundError("rg not installed")

    monkeypatch.setattr(local_research, "run_rg", missing_rg)

    service = build_seeded_service(tmp_path / "research_pass_runner.sqlite3", reset=True)
    report = execute_passes(service, specs, rounds=2, source_roots=[repo])

    round_1, round_2 = report.round_summaries
    assert round_1.mode_counts.get("live_research", 0) == 2
    assert round_2.mode_counts.get("reuse", 0) == 2
    assert all(item.mode_changed for item in report.transitions)
