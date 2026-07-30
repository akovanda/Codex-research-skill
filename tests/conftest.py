from __future__ import annotations

import os
from pathlib import Path
import tempfile

import pytest

# Importing the ASGI module creates its default app. Keep collection isolated
# from an operator's live registry and from stale pre-release migration state.
_TEST_DATA = tempfile.TemporaryDirectory(prefix="research-registry-tests-")
os.environ.setdefault(
    "RESEARCH_REGISTRY_DATA_DIR",
    str(Path(_TEST_DATA.name) / "data"),
)

from research_registry.legacy_feature import LEGACY_HEURISTICS_ENV


@pytest.fixture(autouse=True)
def enable_marked_legacy_heuristics(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if request.node.get_closest_marker("legacy") is not None:
        monkeypatch.setenv(LEGACY_HEURISTICS_ENV, "1")
