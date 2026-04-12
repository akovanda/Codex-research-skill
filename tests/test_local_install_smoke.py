from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest

from research_registry.local_manager import MANAGED_MCP_BEGIN
from research_registry.managed_config import default_managed_local_config, write_managed_local_config


REPO_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LOCAL_INSTALL_SMOKE") != "1",
    reason="local install smoke runs only in dedicated CI or explicit opt-in runs",
)


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def test_local_install_cli_smoke(tmp_path: Path, monkeypatch) -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is required for local install smoke")

    port = _free_port()
    codex_home = tmp_path / "codex-home"
    xdg_config_home = tmp_path / "xdg-config"
    xdg_data_home = tmp_path / "xdg-data"

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data_home))

    managed = default_managed_local_config(
        port=port,
        admin_token="smoke-admin-token",
        session_secret="smoke-session-secret",
        api_key="smoke-api-key",
    )
    write_managed_local_config(managed)

    env = os.environ.copy()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "research_registry.local_install",
            "--port",
            str(port),
            "--skip-build",
            "--skip-start",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    status = subprocess.run(
        [sys.executable, "-m", "research_registry.local_status"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    token_result = subprocess.run(
        [sys.executable, "-m", "research_registry.local_token"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    config_dir = xdg_config_home / "research-registry"
    codex_config = codex_home / "config.toml"
    skills_dir = codex_home / "skills"
    codex_config_text = codex_config.read_text(encoding="utf-8")

    assert "configured=true" in result.stdout
    assert "ready=false" in result.stdout
    assert "api_key_configured=true" in result.stdout
    assert "codex_mcp_managed=true" in result.stdout
    assert f"base_url=http://127.0.0.1:{port}" in status.stdout
    assert f"base_url=http://127.0.0.1:{port}" in token_result.stdout
    assert "admin_token=smoke-admin-token" in token_result.stdout
    assert "api_key=smoke-api-key" in token_result.stdout
    assert (config_dir / "config.toml").exists()
    assert (config_dir / "compose.yaml").exists()
    assert (config_dir / ".env").exists()
    assert f'url = "http://127.0.0.1:{port}/mcp/"' in codex_config_text
    assert '"x-api-key" = "smoke-api-key"' in codex_config_text
    assert (skills_dir / "research-capture").is_symlink()
    assert (skills_dir / "research-memory-retrieval").is_symlink()

    uninstall = subprocess.run(
        [sys.executable, "-m", "research_registry.local_uninstall"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "codex_block_removed=true" in uninstall.stdout
    assert "removed_skill_links=2" in uninstall.stdout
    assert "configured=true" in uninstall.stdout
    assert "codex_mcp_managed=false" in uninstall.stdout
    if codex_config.exists():
        assert MANAGED_MCP_BEGIN not in codex_config.read_text(encoding="utf-8")
    assert not (skills_dir / "research-capture").exists()
    assert not (skills_dir / "research-memory-retrieval").exists()
