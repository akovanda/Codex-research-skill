from __future__ import annotations

from pathlib import Path

from research_registry.research_pass_runner import build_seeded_service, execute_passes
from research_registry.research_pass_suite import load_research_pass_suite


def test_research_pass_runner_executes_two_rounds_with_reuse_shift(tmp_path: Path) -> None:
    service = build_seeded_service(tmp_path / "research_pass_runner.sqlite3", reset=True)
    report = execute_passes(service, load_research_pass_suite(), rounds=2)

    assert len(report.executions) == 54
    assert len(report.round_summaries) == 2
    assert all(row.actual_domain == row.expected_domain for row in report.executions)
    assert all(row.summary_contract_passed is True for row in report.executions)

    round_1, round_2 = report.round_summaries
    assert round_1.total_passes == 27
    assert round_2.total_passes == 27
    assert round_1.mode_counts.get("gap_fill", 0) >= 5
    assert round_2.mode_counts.get("gap_fill", 0) == 0
    assert round_2.reused_records > round_1.reused_records
    assert any(not item.reused_on_round_1 and item.reused_on_round_2 for item in report.transitions)
