from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import site
import stat
import subprocess
import sys
import sysconfig
import tarfile
import zipfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_FILES = (
    Path(".codex-plugin/plugin.json"),
    Path(".mcp.json"),
    Path("skills/research-recall/SKILL.md"),
    Path("skills/research-recall/agents/openai.yaml"),
    Path("skills/research-deposit/SKILL.md"),
    Path("skills/research-deposit/agents/openai.yaml"),
)
PLUGIN_TOOLS = {
    "research_status",
    "research_search",
    "research_get",
    "research_deposit",
    "research_review",
    "research_refresh",
}
SKILL_TOOLS = {
    "research-recall": {
        "research_status",
        "research_search",
        "research_get",
    },
    "research-deposit": {
        "research_status",
        "research_search",
        "research_deposit",
    },
}


def _dependency_site_paths() -> str:
    candidates = [
        sysconfig.get_path(name)
        for name in ("purelib", "platlib")
    ]
    candidates.extend(site.getsitepackages())
    user_site = site.getusersitepackages()
    if isinstance(user_site, str):
        candidates.append(user_site)
    else:
        candidates.extend(user_site)

    existing: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        resolved = str(Path(candidate).resolve())
        if Path(resolved).is_dir() and resolved not in existing:
            existing.append(resolved)
    if not existing:
        raise RuntimeError(
            f"no dependency site-packages found for {sys.executable}"
        )
    return os.pathsep.join(existing)


def _installed_data_root(python: Path) -> Path:
    result = subprocess.run(
        [
            str(python),
            "-c",
            "import sysconfig; print(sysconfig.get_path('data'))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve()


def _installed_package_root(
    python: Path,
    environment: dict[str, str],
) -> Path:
    result = subprocess.run(
        [
            str(python),
            "-c",
            (
                "from pathlib import Path; import research_registry; "
                "print(Path(research_registry.__file__).resolve().parent)"
            ),
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve()


def _documented_tools(skill: Path) -> set[str]:
    return set(
        re.findall(
            r"`(research_[a-z_]+)`",
            skill.read_text(encoding="utf-8"),
        )
    )


@pytest.fixture(scope="module")
def distribution_artifacts(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    output = tmp_path_factory.mktemp("rr2-dist")
    subprocess.run(
        [
            sys.executable,
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


def test_distribution_excludes_operator_local_evaluation_file(
    distribution_artifacts: tuple[Path, Path],
) -> None:
    wheel, sdist = distribution_artifacts
    with zipfile.ZipFile(wheel) as archive:
        assert not any(
            name.endswith("/codex_research_eval.py")
            or name == "research_registry/codex_research_eval.py"
            for name in archive.namelist()
        )
    with tarfile.open(sdist, "r:gz") as archive:
        assert not any(
            name.endswith("/src/research_registry/codex_research_eval.py")
            for name in archive.getnames()
        )


async def _call_installed_mcp(
    executable: Path,
    environment: dict[str, str],
) -> tuple[dict[str, object], dict[str, object], set[str]]:
    parameters = StdioServerParameters(
        command=str(executable),
        args=["mcp"],
        env=environment,
        cwd=executable.parent,
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
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
    return (
        status.structuredContent,
        search.structuredContent,
        {tool.name for tool in tools.tools},
    )


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
            sys.executable,
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
            "PYTHONPATH": _dependency_site_paths(),
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
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--upgrade",
            "--force-reinstall",
            str(artifact),
        ],
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
    status, search, tool_names = asyncio.run(
        _call_installed_mcp(cli, environment)
    )

    first_payload = json.loads(initialized.stdout)
    second_payload = json.loads(initialized_again.stdout)
    packaged_plugin = (
        _installed_data_root(python)
        / "share"
        / "research-registry"
        / "research-registry-plugin"
    )
    package_root = _installed_package_root(python, environment)
    database = (
        home
        / ".local"
        / "share"
        / "research-registry"
        / "registry.sqlite3"
    )
    config = home / ".config" / "research-registry" / "config.toml"
    installed_plugin = (
        codex_home
        / "marketplaces"
        / "research-registry-local"
        / "plugins"
        / "research-registry"
    )
    plugin_mcp = installed_plugin / ".mcp.json"

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
    assert package_root.is_relative_to(environment_root.resolve())
    assert database.is_file()
    assert config.is_file()
    for relative in PLUGIN_FILES:
        packaged = packaged_plugin / relative
        installed = installed_plugin / relative
        assert packaged.is_file()
        assert installed.read_bytes() == packaged.read_bytes()
    for skill_name, expected_tools in SKILL_TOOLS.items():
        assert _documented_tools(
            installed_plugin / "skills" / skill_name / "SKILL.md"
        ) == expected_tools
        assert expected_tools <= tool_names
    assert PLUGIN_TOOLS <= tool_names
    assert json.loads(plugin_mcp.read_text(encoding="utf-8")) == {
        "mcpServers": {
            "researchRegistry": {
                "command": "research-registry",
                "args": ["mcp", "--transport", "stdio"],
            }
        }
    }
    assert "x-api-key" not in plugin_mcp.read_text(encoding="utf-8")
    if os.name != "nt":
        assert stat.S_IMODE(database.stat().st_mode) == 0o600
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
