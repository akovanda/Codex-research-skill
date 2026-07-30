from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from research_registry.legacy_feature import (
    LEGACY_HEURISTICS_ENV,
    MCP_LEGACY_ENV,
    LegacyFeatureDisabledError,
)
from research_registry.mcp_tools import create_mcp_server
from research_registry.models import BriefResolveRequest, FocusTuple, QuestionCreate
from research_registry.research_capture import is_research_request
from research_registry.service import RegistryService


REPO_ROOT = Path(__file__).resolve().parents[1]


def _subprocess_env(**updates: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env.pop(LEGACY_HEURISTICS_ENV, None)
    env.update(updates)
    return env


def test_default_startup_does_not_import_legacy_heuristics() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import research_registry.app; "
                "import research_registry.mcp_server; "
                "forbidden = {"
                "'research_registry.local_research', "
                "'research_registry.repo_intelligence', "
                "'research_registry.research_capture', "
                "'research_registry.specialist_domains'"
                "}; "
                "loaded = sorted("
                " name for name in sys.modules "
                " if name in forbidden "
                " or name == 'research_registry.legacy' "
                " or name.startswith('research_registry.legacy.')"
                "); "
                "assert not loaded, loaded"
            ),
        ],
        cwd=REPO_ROOT,
        env=_subprocess_env(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_legacy_heuristics_are_disabled_without_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LEGACY_HEURISTICS_ENV, raising=False)

    with pytest.raises(
        LegacyFeatureDisabledError,
        match=LEGACY_HEURISTICS_ENV,
    ):
        is_research_request("Research retrieval quality.")


def test_default_service_cannot_invoke_heuristic_brief_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LEGACY_HEURISTICS_ENV, raising=False)
    service = RegistryService(tmp_path / "registry.sqlite3")
    service.initialize()
    service.create_question(
        QuestionCreate(
            prompt="How is retrieval quality measured?",
            focus=FocusTuple(label="retrieval quality"),
        )
    )

    with pytest.raises(LegacyFeatureDisabledError):
        service.resolve_brief(
            BriefResolveRequest(prompt="Research retrieval quality.")
        )


def test_explicit_legacy_flag_preserves_behavior_and_warns_once() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "always::DeprecationWarning",
            "-c",
            """
import warnings
from research_registry.research_capture import is_research_request

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    assert is_research_request("Research retrieval quality.")
    assert is_research_request("Research retrieval quality.")

messages = [str(item.message) for item in caught]
assert len(messages) == 1, messages
message = messages[0]
assert "legacy heuristic research" in message
assert "research-recall and research-deposit" in message
assert "deprecated since 0.2.0" in message
assert "removal is not scheduled" in message.lower()
assert "docs/implicit-research-capture.md" in message
""",
        ],
        cwd=REPO_ROOT,
        env=_subprocess_env(**{LEGACY_HEURISTICS_ENV: "1"}),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_explicit_mcp_legacy_flag_restores_v1_tool_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MCP_LEGACY_ENV, "1")

    tools = {
        tool.name
        for tool in create_mcp_server(object())._tool_manager.list_tools()
    }

    assert {
        "search",
        "create_question",
        "create_research_bundle",
        "publish",
    } <= tools


def test_tracked_application_sources_have_no_hardcoded_current_model() -> None:
    paths = [
        REPO_ROOT / "src" / "research_registry" / "memory_retrieval_skill.py",
        REPO_ROOT / "src" / "research_registry" / "research_capture.py",
        REPO_ROOT / "src" / "research_registry" / "seed_memory_retrieval.py",
        REPO_ROOT / "src" / "research_registry" / "service.py",
        REPO_ROOT / "src" / "research_registry" / "specialist_domains.py",
    ]

    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in paths
        if "gpt-5.4" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
