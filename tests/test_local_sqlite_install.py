from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from research_registry.backup import (
    BackupVerificationError,
    verify_sqlite_backup,
)
from research_registry.config import load_settings
from research_registry.local_personal import (
    LocalInitializationError,
    diagnose_personal_registry,
    initialize_personal_registry,
)
from research_registry.service import RegistryService


REPO_ROOT = Path(__file__).resolve().parents[1]


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _isolated_environment(tmp_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["HOME"] = str(tmp_path / "home")
    environment["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    environment["XDG_DATA_HOME"] = str(tmp_path / "data")
    environment["CODEX_HOME"] = str(tmp_path / "codex")
    for name in (
        "RESEARCH_REGISTRY_MANAGED_CONFIG_DIR",
        "RESEARCH_REGISTRY_MANAGED_DATA_DIR",
        "RESEARCH_REGISTRY_DATA_DIR",
        "RESEARCH_REGISTRY_DB_PATH",
        "RESEARCH_REGISTRY_DATABASE_URL",
        "RESEARCH_REGISTRY_BACKEND_URL",
        "RESEARCH_REGISTRY_DEFAULT_BACKEND_URL",
    ):
        environment.pop(name, None)
    return environment


def test_init_uses_private_xdg_paths_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _isolated_environment(tmp_path)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    for name in set(os.environ) - set(environment):
        if name.startswith("RESEARCH_REGISTRY_"):
            monkeypatch.delenv(name, raising=False)

    first = initialize_personal_registry()
    config_bytes = first.paths.config_path.read_bytes()
    second = initialize_personal_registry()

    assert first.created is True
    assert second.created is False
    assert second.migration_state == "current"
    assert first.paths.config_path == (
        tmp_path / "config" / "research-registry" / "config.toml"
    )
    assert first.paths.database_path == (
        tmp_path / "data" / "research-registry" / "registry.sqlite3"
    )
    assert first.paths.blob_root == (
        tmp_path / "data" / "research-registry" / "blobs"
    )
    assert first.paths.config_path.read_bytes() == config_bytes
    assert b"admin_token" not in config_bytes
    assert b"session_secret" not in config_bytes
    assert b"api_key" not in config_bytes
    assert _mode(first.paths.config_dir) == 0o700
    assert _mode(first.paths.data_dir) == 0o700
    assert _mode(first.paths.blob_root) == 0o700
    assert _mode(first.paths.blob_root / ".staging") == 0o700
    assert _mode(first.paths.config_path) == 0o600
    assert _mode(first.paths.database_path) == 0o600

    settings = load_settings()
    assert settings.database_url == first.paths.database_url
    assert settings.data_dir == first.paths.data_dir
    assert settings.backend_url is None
    assert settings.backend_api_key is None

    service = RegistryService(settings.database_url)
    service.check_ready()
    assert service.search(
        "nothing stored yet",
        include_private=True,
    ).hits == []


def test_init_refuses_to_replace_unknown_existing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _isolated_environment(tmp_path)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    config = (
        tmp_path / "config" / "research-registry" / "config.toml"
    )
    config.parent.mkdir(parents=True)
    original = b'[user_owned]\nvalue = "leave-me-alone"\n'
    config.write_bytes(original)
    if os.name != "nt":
        config.parent.chmod(0o755)
        config.chmod(0o644)
    original_parent_mode = _mode(config.parent)
    original_file_mode = _mode(config)

    with pytest.raises(
        LocalInitializationError,
        match="not a personal SQLite configuration",
    ):
        initialize_personal_registry()

    assert config.read_bytes() == original
    assert _mode(config.parent) == original_parent_mode
    assert _mode(config) == original_file_mode
    assert not (
        tmp_path / "data" / "research-registry"
    ).exists()


def test_init_rejects_configured_storage_outside_xdg_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _isolated_environment(tmp_path)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    state = initialize_personal_registry()
    outside = tmp_path / "outside.sqlite3"
    config_text = state.paths.config_path.read_text(encoding="utf-8")
    state.paths.config_path.write_text(
        config_text.replace(
            state.paths.database_url,
            f"sqlite:///{outside}",
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        LocalInitializationError,
        match="inside the configured data directory",
    ):
        initialize_personal_registry()

    assert not outside.exists()


def test_doctor_and_backup_report_local_health_without_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _isolated_environment(tmp_path)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    state = initialize_personal_registry()

    checks = diagnose_personal_registry()
    rendered = "\n".join(
        f"{check.name}={str(check.ok).lower()} {check.detail}"
        for check in checks
    )

    assert all(check.ok for check in checks)
    assert {
        "local_config",
        "sqlite_schema",
        "blob_storage",
        "mcp_stdio",
        "backup_ready",
    } <= {check.name for check in checks}
    assert state.paths.database_url not in rendered
    assert "admin_token" not in rendered
    assert "session_secret" not in rendered

    output = tmp_path / "backup.sqlite3"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "research_registry",
            "backup",
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    manifest = output.with_suffix(".sqlite3.manifest.json")
    config_backup = output.with_suffix(".sqlite3.config.toml")

    assert payload["status"] == "verified"
    assert payload["database_kind"] == "sqlite"
    assert payload["configuration"] == "included"
    assert output.is_file()
    assert manifest.is_file()
    assert config_backup.read_bytes() == state.paths.config_path.read_bytes()
    assert _mode(output) == 0o600
    assert _mode(manifest) == 0o600
    assert _mode(config_backup) == 0o600

    config_backup.write_bytes(config_backup.read_bytes() + b"# tampered\n")
    with pytest.raises(
        BackupVerificationError,
        match="configuration does not match",
    ):
        verify_sqlite_backup(
            output,
            manifest,
            blob_root=state.paths.blob_root,
        )


async def _first_stdio_status_and_search(
    environment: dict[str, str],
) -> tuple[dict[str, object], dict[str, object]]:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "research_registry", "mcp"],
        env=environment,
        cwd=REPO_ROOT,
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            status_result = await session.call_tool("research_status", {})
            search_result = await session.call_tool(
                "research_search",
                {
                    "query": "first local search",
                    "include_private": True,
                },
            )
    assert status_result.structuredContent is not None
    assert search_result.structuredContent is not None
    return status_result.structuredContent, search_result.structuredContent


def test_mcp_first_run_initializes_and_searches_without_token_or_docker(
    tmp_path: Path,
) -> None:
    environment = _isolated_environment(tmp_path)

    status, search = asyncio.run(
        _first_stdio_status_and_search(environment)
    )

    assert status["database_type"] == "sqlite"
    assert status["migration_state"] == "current"
    assert status["namespace"] == {"kind": "user", "id": "local"}
    assert search["hits"] == []
    assert (
        tmp_path
        / "data"
        / "research-registry"
        / "registry.sqlite3"
    ).is_file()
