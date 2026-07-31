from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from research_registry.codex_install import (
    CODEX_MARKETPLACE_NAME,
    CodexInstallReport,
    diagnose_codex_install,
    format_codex_install_report,
    install_codex,
    uninstall_codex,
)
from research_registry.local_manager import (
    MANAGED_MCP_BEGIN,
    MANAGED_MCP_END,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeCodex:
    def __init__(self) -> None:
        self.marketplaces: dict[str, str] = {}
        self.installed: set[str] = set()
        self.mutations: list[tuple[str, ...]] = []

    def __call__(
        self, args: tuple[str, ...], codex_home: Path
    ) -> dict[str, object]:
        if args == ("plugin", "marketplace", "list", "--json"):
            return {
                "marketplaces": [
                    {"name": name, "root": root}
                    for name, root in sorted(self.marketplaces.items())
                ]
            }
        if args == ("plugin", "list", "--json"):
            return {
                "installed": [
                    {
                        "pluginId": plugin_id,
                        "name": "research-registry",
                        "marketplaceName": CODEX_MARKETPLACE_NAME,
                        "installed": True,
                        "enabled": True,
                    }
                    for plugin_id in sorted(self.installed)
                ],
                "available": [],
            }
        self.mutations.append(args)
        if args[:3] == ("plugin", "marketplace", "add"):
            manifest = json.loads(
                (
                    Path(args[3])
                    / ".agents"
                    / "plugins"
                    / "marketplace.json"
                ).read_text(encoding="utf-8")
            )
            self.marketplaces[str(manifest["name"])] = str(
                Path(args[3]).resolve()
            )
            return {
                "marketplaceName": manifest["name"],
                "installedRoot": str(Path(args[3]).resolve()),
                "alreadyAdded": False,
            }
        if args[:2] == ("plugin", "add"):
            self.installed.add(str(args[2]))
            return {"pluginId": args[2]}
        if args[:2] == ("plugin", "remove"):
            self.installed.discard(str(args[2]))
            return {"pluginId": args[2]}
        if args[:3] == ("plugin", "marketplace", "remove"):
            self.marketplaces.pop(str(args[3]), None)
            return {"marketplaceName": args[3]}
        raise AssertionError(f"unexpected Codex command: {args}")


def _legacy_config(secret: str) -> str:
    return (
        'model = "gpt-test"\n\n'
        f"{MANAGED_MCP_BEGIN}\n"
        "[mcp_servers.researchRegistry]\n"
        'url = "http://127.0.0.1:8010/mcp/"\n'
        "[mcp_servers.researchRegistry.http_headers]\n"
        f'"x-api-key" = "{secret}"\n'
        f"{MANAGED_MCP_END}\n\n"
        "[profiles.keep]\n"
        'model = "gpt-test-mini"\n'
    )


def _make_legacy_links(codex_home: Path) -> tuple[Path, Path]:
    skills_dir = codex_home / "skills"
    skills_dir.mkdir(parents=True)
    capture = skills_dir / "research-capture"
    memory = skills_dir / "research-memory-retrieval"
    capture.symlink_to(REPO_ROOT / "skills" / "research-capture")
    memory.symlink_to(REPO_ROOT / "skills" / "research-memory-retrieval")
    return capture, memory


def test_dry_run_lists_exact_changes_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "custom-codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    fake = FakeCodex()

    result = install_codex(dry_run=True, runner=fake)

    assert result.dry_run is True
    assert result.changed is True
    assert not (codex_home / "marketplaces").exists()
    assert fake.mutations == []
    assert {change.action for change in result.file_changes} == {"create"}
    assert all(
        str(change.path).startswith(str(codex_home))
        for change in result.file_changes
    )
    output = format_codex_install_report(result)
    assert f"codex_home={codex_home}" in output
    assert "register_marketplace=research-registry-local" in output
    assert "install_plugin=research-registry@research-registry-local" in output


def test_install_is_idempotent_and_uses_custom_codex_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "custom-codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    fake = FakeCodex()

    first = install_codex(runner=fake)
    first_mutations = list(fake.mutations)
    second = install_codex(runner=fake)

    plugin_root = (
        codex_home
        / "marketplaces"
        / CODEX_MARKETPLACE_NAME
        / "plugins"
        / "research-registry"
    )
    assert first.changed is True
    assert (plugin_root / ".codex-plugin" / "plugin.json").exists()
    assert (plugin_root / ".mcp.json").exists()
    assert fake.marketplaces[CODEX_MARKETPLACE_NAME] == str(
        plugin_root.parents[1].resolve()
    )
    assert "research-registry@research-registry-local" in fake.installed
    assert second == CodexInstallReport(
        operation="install",
        dry_run=False,
        codex_home=codex_home,
        file_changes=(),
        registry_changes=(),
        warnings=(),
    )
    assert fake.mutations == first_mutations


def test_uninstall_rejects_tampered_state_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex"
    state_path = (
        codex_home
        / "marketplaces"
        / CODEX_MARKETPLACE_NAME
        / ".research-registry-install-state.json"
    )
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "legacy_config_block": None,
                "legacy_skill_links": [
                    {
                        "path": str(tmp_path / "outside"),
                        "target": str(REPO_ROOT / "skills" / "research-capture"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    with pytest.raises(RuntimeError, match="unsafe legacy link"):
        uninstall_codex(runner=FakeCodex())

    assert state_path.exists()
    assert not (tmp_path / "outside").exists()


def test_install_migrates_only_managed_legacy_state_and_preserves_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    fake = FakeCodex()
    secret = "do-not-print-this-token"
    config = codex_home / "config.toml"
    config.write_text(_legacy_config(secret), encoding="utf-8")
    managed_links = _make_legacy_links(codex_home)
    unrelated = codex_home / "skills" / "custom"
    unrelated.mkdir()
    unrelated_link = codex_home / "skills" / "research-capture-custom"
    unrelated_link.symlink_to(unrelated)

    result = install_codex(runner=fake)
    output = format_codex_install_report(result)
    updated = config.read_text(encoding="utf-8")

    assert MANAGED_MCP_BEGIN not in updated
    assert MANAGED_MCP_END not in updated
    assert 'model = "gpt-test"' in updated
    assert "[profiles.keep]" in updated
    assert all(not path.exists() for path in managed_links)
    assert unrelated_link.is_symlink()
    assert unrelated.exists()
    assert secret not in output
    assert secret not in repr(result)
    state_path = (
        codex_home
        / "marketplaces"
        / CODEX_MARKETPLACE_NAME
        / ".research-registry-install-state.json"
    )
    if os.name != "nt":
        assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_install_leaves_unmanaged_legacy_named_paths_and_config_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex"
    skills = codex_home / "skills"
    skills.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    fake = FakeCodex()
    unmanaged_skill = skills / "research-capture"
    unmanaged_skill.mkdir()
    config = codex_home / "config.toml"
    unmanaged_config = (
        "[mcp_servers.researchRegistry]\n"
        'command = "someone-elses-server"\n'
    )
    config.write_text(unmanaged_config, encoding="utf-8")

    install_codex(runner=fake)

    assert unmanaged_skill.is_dir()
    assert config.read_text(encoding="utf-8") == unmanaged_config


def test_uninstall_removes_plugin_state_and_restores_only_migrated_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    fake = FakeCodex()
    config = codex_home / "config.toml"
    original = _legacy_config("stored-secret")
    config.write_text(original, encoding="utf-8")
    managed_links = _make_legacy_links(codex_home)

    install_codex(runner=fake)
    config.write_text(
        config.read_text(encoding="utf-8")
        + '\n[profiles.added_later]\nmodel = "gpt-later"\n',
        encoding="utf-8",
    )
    result = uninstall_codex(runner=fake)
    restored = config.read_text(encoding="utf-8")

    assert result.changed is True
    assert CODEX_MARKETPLACE_NAME not in fake.marketplaces
    assert "research-registry@research-registry-local" not in fake.installed
    assert not (codex_home / "marketplaces" / CODEX_MARKETPLACE_NAME).exists()
    assert all(path.is_symlink() for path in managed_links)
    assert MANAGED_MCP_BEGIN in restored
    assert "stored-secret" in restored
    assert "[profiles.added_later]" in restored


def test_uninstall_does_not_overwrite_new_conflicting_user_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    fake = FakeCodex()
    config = codex_home / "config.toml"
    config.write_text(_legacy_config("stored-secret"), encoding="utf-8")

    install_codex(runner=fake)
    config.write_text(
        "[mcp_servers.researchRegistry]\n"
        'command = "new-user-server"\n',
        encoding="utf-8",
    )
    result = uninstall_codex(runner=fake)

    assert 'command = "new-user-server"' in config.read_text(
        encoding="utf-8"
    )
    assert "stored-secret" not in config.read_text(encoding="utf-8")
    assert any("not restored" in warning for warning in result.warnings)


def test_doctor_reports_plugin_and_stdio_health_without_secret_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv(
        "RESEARCH_REGISTRY_TEST_SECRET", "doctor-must-not-print-this"
    )
    fake = FakeCodex()
    install_codex(runner=fake)

    checks = diagnose_codex_install(runner=fake)
    output = "\n".join(check.detail for check in checks)

    assert checks
    assert all(check.ok for check in checks)
    assert "doctor-must-not-print-this" not in output
    assert "stdio" in output.lower()
