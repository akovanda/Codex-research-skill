from __future__ import annotations

from pathlib import Path

import research_registry.local_research as local_research
from research_registry.local_research import build_focus, run_local_research


def test_build_focus_avoids_generic_object_names() -> None:
    focus = build_focus(
        "Research benchmark window sampling strategy for candidate comparisons under CPU budget constraints.",
        domain="llm-evals",
        source_signals=["continuity-benchmarks: start-index slicing helps benchmark window sampling under CPU budget constraints"],
    )

    assert focus.object == "benchmark window sampling"
    assert focus.constraint == "candidate comparisons"
    assert "cpu budget constraints" in (focus.concern or "") or "cpu budget constraints" in (focus.constraint or "")


def test_local_research_collects_real_evidence_from_temp_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "memory.md").write_text(
        "Typed-anchor memory models keep rivalry and obligation threads separate.\n"
        "Branch-private memory isolation prevents coding branch leakage.\n",
        encoding="utf-8",
    )

    result = run_local_research(
        "Research typed-anchor memory models for rivalry and obligation threads.",
        domain="memory-retrieval",
        source_signals=["repo: typed-anchor memory models keep rivalry and obligation threads separate"],
        source_roots=[repo],
    )

    assert result.hits
    assert result.claim_drafts
    assert result.report_md is not None
    assert "typed-anchor memory" in result.focus.label


def test_local_research_collects_real_evidence_without_rg(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "memory.md").write_text(
        "Typed-anchor memory models keep rivalry and obligation threads separate.\n"
        "Branch-private memory isolation prevents coding branch leakage.\n",
        encoding="utf-8",
    )

    def missing_rg(term: str, root: Path):
        raise FileNotFoundError("rg not installed")

    monkeypatch.setattr(local_research, "run_rg", missing_rg)

    result = run_local_research(
        "Research typed-anchor memory models for rivalry and obligation threads.",
        domain="memory-retrieval",
        source_signals=["repo: typed-anchor memory models keep rivalry and obligation threads separate"],
        source_roots=[repo],
    )

    assert result.hits
    assert result.claim_drafts
    assert result.report_md is not None
    assert "typed-anchor memory" in result.focus.label
