from __future__ import annotations

import json
from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "research-registry-plugin"
RECALL_ROOT = PLUGIN_ROOT / "skills" / "research-recall"
DEPOSIT_ROOT = PLUGIN_ROOT / "skills" / "research-deposit"


def test_plugin_manifest_declares_only_documented_bundled_components() -> None:
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["name"] == "research-registry"
    assert manifest["version"] == "0.2.0a1"
    assert manifest["license"] == "Apache-2.0"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert "apps" not in manifest
    assert "hooks" not in manifest
    assert manifest["interface"]["capabilities"] == ["Read", "Write"]
    assert "[TODO:" not in json.dumps(manifest)


def test_plugin_bundles_local_stdio_mcp_without_remote_or_credentials() -> None:
    mcp_config = json.loads(
        (PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8")
    )

    assert set(mcp_config) == {"mcpServers"}
    assert set(mcp_config["mcpServers"]) == {"researchRegistry"}
    server = mcp_config["mcpServers"]["researchRegistry"]
    assert server == {
        "command": "research-registry",
        "args": ["mcp", "--transport", "stdio"],
    }
    assert "url" not in json.dumps(mcp_config).lower()
    assert "token" not in json.dumps(mcp_config).lower()
    assert "secret" not in json.dumps(mcp_config).lower()


def test_recall_skill_is_implicitly_discoverable_and_strictly_read_only() -> None:
    skill = (RECALL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    agent = (RECALL_ROOT / "agents" / "openai.yaml").read_text(
        encoding="utf-8"
    )

    assert "name: research-recall" in skill
    for tool in ("research_status", "research_search", "research_get"):
        assert f"`{tool}`" in skill
    assert "read-only" in skill.lower()
    assert "never deposit" in skill.lower()
    assert "untrusted" in skill.lower()
    assert "allow_implicit_invocation: true" in agent
    assert "$research-recall" in agent


def test_deposit_skill_is_explicit_only_private_and_never_publishes() -> None:
    skill = (DEPOSIT_ROOT / "SKILL.md").read_text(encoding="utf-8")
    agent = (DEPOSIT_ROOT / "agents" / "openai.yaml").read_text(
        encoding="utf-8"
    )

    assert "name: research-deposit" in skill
    assert "`research_status`" in skill
    assert "`research_search`" in skill
    assert "`research_deposit`" in skill
    assert "validate_only" in skill
    assert "private" in skill.lower()
    assert "unreviewed" in skill.lower()
    assert "never publish" in skill.lower()
    assert "untrusted" in skill.lower()
    assert "allow_implicit_invocation: false" in agent
    assert "$research-deposit" in agent


def test_default_plugin_does_not_bundle_legacy_specialist_skills() -> None:
    skill_names = {
        path.parent.name for path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md")
    }

    assert skill_names == {"research-recall", "research-deposit"}
    assert "research-capture" not in skill_names
    assert "research-memory-retrieval" not in skill_names


def test_distribution_includes_the_complete_plugin_package() -> None:
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    data_files = pyproject["tool"]["setuptools"]["data-files"]
    packaged_files = {path for paths in data_files.values() for path in paths}

    assert {
        "research-registry-plugin/.codex-plugin/plugin.json",
        "research-registry-plugin/.mcp.json",
        "research-registry-plugin/skills/research-recall/SKILL.md",
        "research-registry-plugin/skills/research-recall/agents/openai.yaml",
        "research-registry-plugin/skills/research-deposit/SKILL.md",
        "research-registry-plugin/skills/research-deposit/agents/openai.yaml",
    } <= packaged_files
