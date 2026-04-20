from __future__ import annotations

from pathlib import Path

import research_registry.local_research as local_research
from research_registry.local_research import build_focus, build_query_terms, run_local_research


def test_build_focus_avoids_generic_object_names() -> None:
    focus = build_focus(
        "Research benchmark window sampling strategy for candidate comparisons under CPU budget constraints.",
        domain="llm-evals",
        source_signals=["continuity-benchmarks: start-index slicing helps benchmark window sampling under CPU budget constraints"],
    )

    assert focus.object == "benchmark window sampling"
    assert focus.constraint == "candidate comparisons"
    assert "cpu budget constraints" in (focus.concern or "") or "cpu budget constraints" in (focus.constraint or "")


def test_build_query_terms_prunes_repo_labels_and_generic_fragments() -> None:
    prompt = "Research evaluation artifact schemas that make benchmark history useful for post-run diagnosis and regression review."
    source_signals = [
        "dnd2: overnight batch adds --record-history to official and subset runs",
        "continuity-benchmarks: artifacts and reporting pipeline",
    ]

    focus = build_focus(prompt, domain="llm-evals", source_signals=source_signals)
    terms = build_query_terms(prompt, focus=focus, source_signals=source_signals)

    assert focus.object == "evaluation artifact schemas"
    assert "continuity-benchmarks" not in terms
    assert "post-run" not in terms
    assert "record-history" in terms
    assert "evaluation artifact schemas" in terms


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


def test_local_research_skips_oversized_artifact_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "artifacts").mkdir()
    (repo / "docs" / "memory.md").write_text(
        "Typed-anchor memory models keep rivalry and obligation threads separate.\n",
        encoding="utf-8",
    )
    oversized = repo / "artifacts" / "memory_dump.json"
    oversized.write_text("typed-anchor memory models\n" * 120_000, encoding="utf-8")

    result = run_local_research(
        "Research typed-anchor memory models for rivalry and obligation threads.",
        domain="memory-retrieval",
        source_signals=["repo: typed-anchor memory models keep rivalry and obligation threads separate"],
        source_roots=[repo],
    )

    assert result.hits
    assert all("memory_dump.json" not in hit.file_path for hit in result.hits)
    assert any(hit.file_path.endswith("docs/memory.md") for hit in result.hits)


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
