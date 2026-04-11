from __future__ import annotations

from pathlib import Path

from research_registry.research_capture import run_implicit_research_capture
from research_registry.service import RegistryService


def test_memory_research_summary_contract_preserves_reuse_sections(tmp_path: Path) -> None:
    repo = tmp_path / "memory"
    repo.mkdir()
    (repo / "README.md").write_text(
        "Long-term memory retrieval needs provenance and freshness metadata.\n"
        "Reranking precision matters when multiple memory candidates overlap.\n",
        encoding="utf-8",
    )

    service = RegistryService(tmp_path / "memory.sqlite3")
    service.initialize()
    outcome = run_implicit_research_capture(
        "Research long-term memory retrieval provenance and freshness requirements.",
        backend=service,
        source_signals=["memory: long-term memory retrieval needs provenance and freshness metadata"],
        source_roots=[repo],
    )

    assert outcome.specialized_skill == "research-memory-retrieval"
    assert outcome.summary_contract_passed is True
    assert outcome.narrative_summary_md is not None
    assert "## Knowledge To Reuse" in outcome.narrative_summary_md
    assert "## Context To Carry Forward" in outcome.narrative_summary_md
    assert "## Evidence" in outcome.narrative_summary_md
    assert "## Registry State" in outcome.narrative_summary_md
