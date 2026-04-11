from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import stat

from research_registry.local_manager import (
    MANAGED_MCP_BEGIN,
    MANAGED_MCP_END,
    build_local_config,
    ensure_codex_mcp_config,
    render_codex_mcp_block,
    render_compose_env,
    render_compose_yaml,
    upsert_managed_codex_config,
    write_local_runtime_files,
)
from research_registry.managed_config import PRIVATE_DIR_MODE, PRIVATE_FILE_MODE


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
