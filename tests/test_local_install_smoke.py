from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import stat
import subprocess

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPENDENCY_SITE = next(
    (REPO_ROOT / ".venv" / "lib").glob("python*/site-packages")
)


@pytest.fixture(scope="module")
def distribution_artifacts(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    output = tmp_path_factory.mktemp("rr2-dist")
    subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "python"),
            "-m",
            "build",
            "--outdir",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(output.glob("research_registry-*.whl"))
    sdist = next(output.glob("research_registry-*.tar.gz"))
    return wheel, sdist


def _write_fake_codex(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
home = Path(os.environ["CODEX_HOME"])
root = home / "marketplaces" / "research-registry-local"
plugin = root / "plugins" / "research-registry" / ".mcp.json"
if args[:4] == ["plugin", "marketplace", "list", "--json"]:
    entries = (
        [{"name": "research-registry-local", "root": str(root.resolve())}]
        if root.exists()
        else []
    )
    print(json.dumps({"marketplaces": entries}))
elif args[:3] == ["plugin", "list", "--json"]:
    installed = (
        [{
            "pluginId": "research-registry@research-registry-local",
            "installed": True,
            "enabled": True,
        }]
        if plugin.exists()
        else []
    )
    print(json.dumps({"installed": installed, "available": []}))
else:
    print(json.dumps({"ok": True}))
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


async def _call_installed_mcp(
    executable: Path,
    environment: dict[str, str],
) -> tuple[dict[str, object], dict[str, object]]:
    parameters = StdioServerParameters(
        command=str(executable),
        args=["mcp"],
        env=environment,
        cwd=executable.parent,
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            status = await session.call_tool("research_status", {})
            search = await session.call_tool(
                "research_search",
                {
                    "query": "clean home package smoke",
                    "include_private": True,
                },
            )
    assert status.structuredContent is not None
    assert search.structuredContent is not None
    return status.structuredContent, search.structuredContent


@pytest.mark.parametrize("artifact_index", [0, 1], ids=["wheel", "sdist"])
def test_clean_home_package_init_plugin_and_stdio_search(
    artifact_index: int,
    distribution_artifacts: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    artifact = distribution_artifacts[artifact_index]
    environment_root = tmp_path / "environment"
    subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "python"),
            "-m",
            "venv",
            str(environment_root),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    python = environment_root / "bin" / "python"
    cli = environment_root / "bin" / "research-registry"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--ignore-installed",
            str(artifact),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    home = tmp_path / "home"
    codex_home = tmp_path / "codex"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _write_fake_codex(fake_bin / "codex")
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
            "PYTHONPATH": str(DEPENDENCY_SITE),
        }
    )
    for name in tuple(environment):
        if name.startswith("RESEARCH_REGISTRY_"):
            environment.pop(name)

    initialized = subprocess.run(
        [str(cli), "init", "--json"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    initialized_again = subprocess.run(
        [str(cli), "init", "--json"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    plugin = subprocess.run(
        [str(cli), "install-codex"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    doctor = subprocess.run(
        [str(cli), "doctor"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    status, search = asyncio.run(
        _call_installed_mcp(cli, environment)
    )

    first_payload = json.loads(initialized.stdout)
    second_payload = json.loads(initialized_again.stdout)
    database = home / ".local" / "share" / "research-registry" / "registry.sqlite3"
    config = home / ".config" / "research-registry" / "config.toml"
    plugin_mcp = (
        codex_home
        / "marketplaces"
        / "research-registry-local"
        / "plugins"
        / "research-registry"
        / ".mcp.json"
    )

    assert first_payload["status"] == "initialized"
    assert second_payload["status"] == "current"
    assert "install_plugin=research-registry@research-registry-local" in (
        plugin.stdout
    )
    assert doctor.stdout.splitlines()[0] == "ok=true"
    assert "codex_plugin_files=true" in doctor.stdout
    assert "codex_plugin_marketplace=true" in doctor.stdout
    assert "codex_plugin_installed=true" in doctor.stdout
    assert status["database_type"] == "sqlite"
    assert status["migration_state"] == "current"
    assert search["hits"] == []
    assert database.is_file()
    assert config.is_file()
    assert plugin_mcp.is_file()
    assert "x-api-key" not in plugin_mcp.read_text(encoding="utf-8")
    if os.name != "nt":
        assert stat.S_IMODE(database.stat().st_mode) == 0o600
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
