from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import stat

import research_registry.local_manager as local_manager
from research_registry.local_manager import (
    MANAGED_MCP_BEGIN,
    MANAGED_MCP_END,
    build_local_config,
    ensure_codex_mcp_config,
    ensure_skill_links,
    local_runtime_tokens,
    remove_managed_codex_block,
    remove_managed_skill_links,
    render_codex_mcp_block,
    render_compose_env,
    render_compose_yaml,
    restore_codex_config_backup,
    uninstall_local_runtime,
    upsert_managed_codex_config,
    write_local_runtime_files,
)
from research_registry.managed_config import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE, write_managed_local_config


def _assert_mode(path: Path, expected: int) -> None:
    if os.name == "nt":
        return
    assert stat.S_IMODE(path.stat().st_mode) == expected


def test_render_compose_files_include_localhost_port_and_image() -> None:
    config = build_local_config(port=8017)

    compose_yaml = render_compose_yaml(config)
    compose_env = render_compose_env(config)

    assert config.image_tag in compose_yaml
    assert '127.0.0.1:8017:8000' in compose_yaml
    assert "RESEARCH_REGISTRY_PORT=8000" in compose_env
    assert "RESEARCH_REGISTRY_PUBLIC_BASE_URL=http://127.0.0.1:8017" in compose_env


def test_upsert_managed_codex_config_appends_and_replaces_block() -> None:
    config = replace(build_local_config(port=8018), api_key="test-key")

    initial = 'model = "gpt-5.4"\n'
    updated = upsert_managed_codex_config(initial, config)
    replaced = upsert_managed_codex_config(updated, replace(config, api_key="new-key"))

    assert MANAGED_MCP_BEGIN in updated
    assert MANAGED_MCP_END in updated
    assert '[mcp_servers.researchRegistry]' in updated
    assert '"x-api-key" = "test-key"' in updated
    assert '"x-api-key" = "new-key"' in replaced
    assert replaced.count(MANAGED_MCP_BEGIN) == 1


def test_render_codex_mcp_block_requires_api_key() -> None:
    config = build_local_config(port=8020)
    try:
        render_codex_mcp_block(config)
    except RuntimeError as exc:
        assert "api_key" in str(exc)
    else:
        raise AssertionError("expected render_codex_mcp_block to require api_key")


def test_write_local_runtime_files_sets_private_permissions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_REGISTRY_MANAGED_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("RESEARCH_REGISTRY_MANAGED_DATA_DIR", str(tmp_path / "data"))

    config = build_local_config(port=8021)
    write_local_runtime_files(config)

    assert config.compose_file_path.exists()
    assert config.compose_env_path.exists()
    _assert_mode(config.config_dir, PRIVATE_DIR_MODE)
    _assert_mode(config.data_dir, PRIVATE_DIR_MODE)
    _assert_mode(config.compose_file_path, PRIVATE_FILE_MODE)
    _assert_mode(config.compose_env_path, PRIVATE_FILE_MODE)


def test_ensure_codex_mcp_config_writes_backup_and_private_permissions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    config = replace(build_local_config(port=8022), api_key="test-key")
    codex_config = tmp_path / "codex" / "config.toml"
    codex_config.parent.mkdir(parents=True, exist_ok=True)
    codex_config.write_text('model = "gpt-5.4"\n', encoding="utf-8")

    updated_path = ensure_codex_mcp_config(config)
    backup_path = updated_path.with_name(f"{updated_path.name}.research-registry.bak")

    assert MANAGED_MCP_BEGIN in updated_path.read_text(encoding="utf-8")
    assert "model = \"gpt-5.4\"" in backup_path.read_text(encoding="utf-8")
    _assert_mode(updated_path, PRIVATE_FILE_MODE)
    _assert_mode(backup_path, PRIVATE_FILE_MODE)


def test_remove_managed_codex_block_preserves_surrounding_content() -> None:
    config = replace(build_local_config(port=8023), api_key="test-key")
    content = (
        'model = "gpt-5.4"\n\n'
        + upsert_managed_codex_config("", config)
        + '\n[profiles.default]\nmodel = "gpt-5.4-mini"\n'
    )

    updated = remove_managed_codex_block(content)

    assert MANAGED_MCP_BEGIN not in updated
    assert MANAGED_MCP_END not in updated
    assert 'model = "gpt-5.4"' in updated
    assert '[profiles.default]' in updated


def test_restore_codex_config_backup_restores_previous_content(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    codex_config = tmp_path / "codex" / "config.toml"
    codex_config.parent.mkdir(parents=True, exist_ok=True)
    codex_config.write_text("managed config\n", encoding="utf-8")
    backup_path = codex_config.with_name(f"{codex_config.name}.research-registry.bak")
    backup_path.write_text('model = "gpt-5.4"\n', encoding="utf-8")

    assert restore_codex_config_backup() is True
    assert codex_config.read_text(encoding="utf-8") == 'model = "gpt-5.4"\n'
    _assert_mode(codex_config, PRIVATE_FILE_MODE)


def test_remove_managed_skill_links_only_removes_managed_symlinks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    skills_dir = tmp_path / "codex" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    installed = ensure_skill_links()
    unmanaged = skills_dir / "custom-skill"
    unmanaged.mkdir()

    removed = remove_managed_skill_links()

    assert sorted(path.name for path in installed) == ["research-capture", "research-memory-retrieval"]
    assert sorted(path.name for path in removed) == ["research-capture", "research-memory-retrieval"]
    assert unmanaged.exists()


def test_local_runtime_tokens_reads_managed_admin_token_and_api_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_REGISTRY_MANAGED_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("RESEARCH_REGISTRY_MANAGED_DATA_DIR", str(tmp_path / "data"))

    config = replace(build_local_config(port=8024), admin_token="admin-token", api_key="api-key")
    write_managed_local_config(config)

    tokens = local_runtime_tokens()

    assert tokens.base_url == "http://127.0.0.1:8024"
    assert tokens.admin_token == "admin-token"
    assert tokens.api_key == "api-key"


def test_uninstall_local_runtime_removes_managed_codex_block_and_skill_links(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("RESEARCH_REGISTRY_MANAGED_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("RESEARCH_REGISTRY_MANAGED_DATA_DIR", str(tmp_path / "data"))

    config = replace(build_local_config(port=8025), api_key="test-key")
    write_managed_local_config(config)
    ensure_codex_mcp_config(config)
    ensure_skill_links()

    codex_config = tmp_path / "codex" / "config.toml"
    codex_config.write_text(upsert_managed_codex_config('model = "gpt-5.4"\n', config), encoding="utf-8")

    calls: list[bool] = []

    def fake_stop_local_stack(managed, *, remove_volumes: bool = False) -> None:
        assert managed == config
        calls.append(remove_volumes)

    monkeypatch.setattr(local_manager, "stop_local_stack", fake_stop_local_stack)

    result = uninstall_local_runtime()

    assert calls == [False]
    assert result.stack_stop_attempted is True
    assert result.stack_stopped is True
    assert result.codex_backup_restored is False
    assert result.codex_block_removed is True
    assert sorted(path.name for path in result.removed_skill_links) == ["research-capture", "research-memory-retrieval"]
    assert MANAGED_MCP_BEGIN not in codex_config.read_text(encoding="utf-8")
    assert 'model = "gpt-5.4"' in codex_config.read_text(encoding="utf-8")
    assert not (tmp_path / "codex" / "skills" / "research-capture").exists()
    assert not (tmp_path / "codex" / "skills" / "research-memory-retrieval").exists()
    assert (tmp_path / "config" / "config.toml").exists()


def test_uninstall_local_runtime_can_restore_backup_and_purge_managed_dirs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("RESEARCH_REGISTRY_MANAGED_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("RESEARCH_REGISTRY_MANAGED_DATA_DIR", str(tmp_path / "data"))

    config = replace(build_local_config(port=8026), api_key="test-key")
    write_local_runtime_files(config)
    write_managed_local_config(config)
    ensure_codex_mcp_config(config)

    codex_config = tmp_path / "codex" / "config.toml"
    backup_path = codex_config.with_name(f"{codex_config.name}.research-registry.bak")
    backup_path.write_text('model = "gpt-5.4"\n', encoding="utf-8")
    (config.data_dir / "pending-research-captures.jsonl").write_text("{}", encoding="utf-8")

    calls: list[bool] = []

    def fake_stop_local_stack(managed, *, remove_volumes: bool = False) -> None:
        assert managed == config
        calls.append(remove_volumes)

    monkeypatch.setattr(local_manager, "stop_local_stack", fake_stop_local_stack)

    result = uninstall_local_runtime(restore_codex_backup=True, purge_data=True)

    assert calls == [True]
    assert result.codex_backup_restored is True
    assert result.codex_block_removed is False
    assert result.purged_config_dir is True
    assert result.purged_data_dir is True
    assert codex_config.read_text(encoding="utf-8") == 'model = "gpt-5.4"\n'
    assert not config.config_dir.exists()
    assert not config.data_dir.exists()
