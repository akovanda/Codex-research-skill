from __future__ import annotations

import pytest

from research_registry.legacy_feature import LEGACY_HEURISTICS_ENV


@pytest.fixture(autouse=True)
def enable_marked_legacy_heuristics(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if request.node.get_closest_marker("legacy") is not None:
        monkeypatch.setenv(LEGACY_HEURISTICS_ENV, "1")
