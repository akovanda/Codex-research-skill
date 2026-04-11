from __future__ import annotations

from pathlib import Path

from research_registry.research_capture import (
    format_capture_summary,
    is_research_request,
    run_implicit_research_capture,
    specialized_domain_for_prompt,
    specialized_skill_for_prompt,
)
from research_registry.service import RegistryService


def make_service(tmp_path: Path) -> RegistryService:
    service = RegistryService(tmp_path / "implicit.sqlite3")
    service.initialize()
    return service


def make_branch_private_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "choose-game"
    (repo / "src" / "choose_game").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "README.md").write_text(
        "Branch-private memory keeps divergent narrative and coding branches isolated while preserving typed anchors.\n",
        encoding="utf-8",
    )
    (repo / "src" / "choose_game" / "freeform_regression.py").write_text(
        "def verify_branch_private_memory():\n"
        "    note = 'coding branch isolation remains branch-private and does not leak across divergent branches'\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_branch_private.py").write_text(
        "def test_branch_private_isolation():\n"
        "    assert 'branch-private' in 'branch-private memory isolation'\n",
        encoding="utf-8",
    )
    return repo


def test_research_prompt_classification_and_specialized_delegation() -> None:
    assert is_research_request("Please research RAG retrieval quality.")
    assert is_research_request("Look into long-term memory tradeoffs.")
    assert not is_research_request("Fix this failing unit test.")

    assert specialized_skill_for_prompt("Research long-term memory structure for LLMs.") == "research-memory-retrieval"
    assert specialized_domain_for_prompt("Research long-term memory structure for LLMs.") == "memory-retrieval"
    assert specialized_domain_for_prompt("Research judge model calibration and benchmark drift.") == "llm-evals"
    assert specialized_domain_for_prompt("Research LLM inference latency and batching tradeoffs.") == "inference-optimization"
    assert specialized_skill_for_prompt("Research restaurant options in Boston.") is None


def test_implicit_capture_runs_live_research_then_reuses_on_second_pass(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    repo = make_branch_private_repo(tmp_path)

    first = run_implicit_research_capture(
        "Research branch-private memory isolation strategies for divergent narrative and coding branches.",
        backend=service,
        source_signals=["choose-game: full stack verification checks coding branch isolation"],
        source_roots=[repo],
    )
    assert first.specialized_domain == "memory-retrieval"
    assert first.specialist_mode == "live_research"
    assert first.capture_summary.stored_question_id is not None
    assert first.capture_summary.stored_report_id is not None
    assert first.capture_summary.stored_claim_ids
    assert first.summary_contract_passed is True

    second = run_implicit_research_capture(
        "Research branch-private memory isolation strategies for divergent narrative and coding branches.",
        backend=service,
        source_signals=["choose-game: full stack verification checks coding branch isolation"],
        source_roots=[repo],
    )
    assert second.specialist_mode == "reuse"
    assert second.capture_summary.reused_record_ids
    assert second.capture_summary.stored_session_id is not None
    assert second.summary_contract_passed is True
    assert "Stored session" in format_capture_summary(second.capture_summary)


def test_implicit_capture_records_insufficient_evidence_without_report(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    empty_repo = tmp_path / "empty"
    empty_repo.mkdir()

    outcome = run_implicit_research_capture(
        "Research temporal provenance reconciliation for branch-scoped memory invalidation.",
        backend=service,
        source_signals=["empty: no matching evidence here"],
        source_roots=[empty_repo],
    )

    assert outcome.specialist_mode == "insufficient_evidence"
    assert outcome.capture_summary.stored_question_id is not None
    assert outcome.capture_summary.stored_session_id is not None
    assert outcome.capture_summary.stored_report_id is None
    assert outcome.summary_contract_passed is True
