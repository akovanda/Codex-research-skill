from __future__ import annotations

from research_registry.research_pass_suite import load_research_pass_suite, routing_check


def test_research_pass_suite_size_and_uniqueness() -> None:
    specs = load_research_pass_suite()

    assert 20 <= len(specs) <= 30
    assert len({spec.pass_id for spec in specs}) == len(specs)
    assert {spec.wave for spec in specs} == {1, 2, 3, 4}


def test_research_pass_suite_has_grounded_source_signals() -> None:
    specs = load_research_pass_suite()

    assert all(spec.source_signals for spec in specs)
    assert any("dnd2" in signal for spec in specs for signal in spec.source_signals)
    assert any("continuity-core" in signal for spec in specs for signal in spec.source_signals)
    assert any("choose-game" in signal for spec in specs for signal in spec.source_signals)


def test_research_pass_suite_matches_current_specialist_routing() -> None:
    specs = load_research_pass_suite()
    rows = routing_check(specs)

    assert [row for row in rows if row["status"] != "ok"] == []
