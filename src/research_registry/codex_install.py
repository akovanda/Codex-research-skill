from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sysconfig
from typing import Literal

from .local_manager import (
    MANAGED_MCP_BEGIN,
    MANAGED_MCP_END,
    LocalDoctorCheck,
    codex_home,
    remove_managed_codex_block,
    repo_root,
)
from .managed_config import PRIVATE_FILE_MODE


PLUGIN_NAME = "research-registry"
CODEX_MARKETPLACE_NAME = "research-registry-local"
PLUGIN_ID = f"{PLUGIN_NAME}@{CODEX_MARKETPLACE_NAME}"
MANAGED_MARKETPLACE_DIRECTORY = "marketplaces"
INSTALL_STATE_NAME = ".research-registry-install-state.json"

_PLUGIN_FILES = (
    Path(".codex-plugin/plugin.json"),
    Path(".mcp.json"),
    Path("skills/research-recall/SKILL.md"),
    Path("skills/research-recall/agents/openai.yaml"),
    Path("skills/research-deposit/SKILL.md"),
    Path("skills/research-deposit/agents/openai.yaml"),
)
_LEGACY_SKILL_NAMES = (
    "research-capture",
    "research-memory-retrieval",
)


CodexRunner = Callable[[tuple[str, ...], Path], dict[str, object]]


@dataclass(frozen=True)
class CodexFileChange:
    action: Literal["create", "update", "remove", "restore"]
    path: Path


@dataclass(frozen=True)
class CodexInstallReport:
    operation: Literal["install", "uninstall"]
    dry_run: bool
    codex_home: Path
    file_changes: tuple[CodexFileChange, ...]
    registry_changes: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.file_changes or self.registry_changes)


@dataclass(frozen=True)
class _RegistryStatus:
    marketplace_registered: bool
    plugin_installed: bool


def _source_plugin_root() -> Path:
    checkout = repo_root() / "research-registry-plugin"
    packaged = (
        Path(sysconfig.get_path("data")).resolve()
        / "share"
        / "research-registry"
        / "research-registry-plugin"
    )
    for candidate in (checkout, packaged):
        if (candidate / ".codex-plugin" / "plugin.json").is_file():
            return candidate
    raise RuntimeError(
        "Research Registry Codex plugin assets were not found; "
        "reinstall the package or run from a source checkout"
    )


def _managed_root(home: Path) -> Path:
    return home / MANAGED_MARKETPLACE_DIRECTORY / CODEX_MARKETPLACE_NAME


def _installed_plugin_root(home: Path) -> Path:
    return _managed_root(home) / "plugins" / PLUGIN_NAME


def _marketplace_path(home: Path) -> Path:
    return _managed_root(home) / ".agents" / "plugins" / "marketplace.json"


def _state_path(home: Path) -> Path:
    return _managed_root(home) / INSTALL_STATE_NAME


def _marketplace_bytes() -> bytes:
    payload = {
        "name": CODEX_MARKETPLACE_NAME,
        "interface": {"displayName": "Research Registry Local"},
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "source": {
                    "source": "local",
                    "path": f"./plugins/{PLUGIN_NAME}",
                },
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Productivity",
            }
        ],
    }
    return (json.dumps(payload, indent=2) + "\n").encode()


def _managed_payloads(home: Path) -> dict[Path, bytes]:
    source_root = _source_plugin_root()
    installed_root = _installed_plugin_root(home)
    payloads = {
        installed_root / relative: (source_root / relative).read_bytes()
        for relative in _PLUGIN_FILES
    }
    payloads[_marketplace_path(home)] = _marketplace_bytes()
    return payloads


def _file_change(path: Path, content: bytes) -> CodexFileChange | None:
    if not path.exists():
        return CodexFileChange("create", path)
    if not path.is_file():
        raise RuntimeError(f"managed Codex path is not a regular file: {path}")
    if path.read_bytes() == content:
        return None
    return CodexFileChange("update", path)


def _atomic_write(path: Path, content: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.research-registry.tmp")
    temporary.write_bytes(content)
    if os.name != "nt":
        temporary.chmod(mode)
    os.replace(temporary, path)


def _run_codex_json(
    args: tuple[str, ...],
    home: Path,
) -> dict[str, object]:
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(home)
    try:
        result = subprocess.run(
            ("codex", *args),
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Codex CLI was not found; install Codex before running this command"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Codex command failed with exit status {exc.returncode}; "
            "run `codex doctor` for details"
        ) from exc
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Codex returned an invalid JSON response") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Codex returned an unexpected JSON response")
    return payload


def _registry_status(home: Path, runner: CodexRunner) -> _RegistryStatus:
    if not home.exists():
        return _RegistryStatus(
            marketplace_registered=False,
            plugin_installed=False,
        )
    marketplaces = runner(
        ("plugin", "marketplace", "list", "--json"),
        home,
    )
    registered = False
    expected_root = _managed_root(home).resolve()
    entries = marketplaces.get("marketplaces", [])
    if not isinstance(entries, list):
        raise RuntimeError("Codex marketplace list returned an invalid response")
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        if item.get("name") != CODEX_MARKETPLACE_NAME:
            continue
        actual_root = Path(str(item.get("root", ""))).expanduser().resolve()
        if actual_root != expected_root:
            raise RuntimeError(
                f"Codex marketplace {CODEX_MARKETPLACE_NAME!r} already "
                "refers to a different local root"
            )
        registered = True

    plugins = runner(("plugin", "list", "--json"), home)
    installed_entries = plugins.get("installed", [])
    if not isinstance(installed_entries, list):
        raise RuntimeError("Codex plugin list returned an invalid response")
    installed = any(
        isinstance(item, Mapping)
        and item.get("pluginId") == PLUGIN_ID
        and item.get("installed") is not False
        for item in installed_entries
    )
    return _RegistryStatus(
        marketplace_registered=registered,
        plugin_installed=installed,
    )


def _legacy_source_roots() -> tuple[Path, ...]:
    roots = [
        repo_root() / "skills",
        Path(sysconfig.get_path("data")).resolve()
        / "share"
        / "research-registry"
        / "skills",
    ]
    return tuple(root for root in roots if root.exists())


def _managed_legacy_links(home: Path) -> dict[Path, Path]:
    known_targets = {
        (root / name).resolve(strict=False)
        for root in _legacy_source_roots()
        for name in _LEGACY_SKILL_NAMES
    }
    links: dict[Path, Path] = {}
    for name in _LEGACY_SKILL_NAMES:
        path = home / "skills" / name
        if not path.is_symlink():
            continue
        target = path.resolve(strict=False)
        if target in known_targets:
            links[path] = target
    return links


def _extract_managed_legacy_block(content: str) -> str | None:
    if MANAGED_MCP_BEGIN not in content or MANAGED_MCP_END not in content:
        return None
    start = content.index(MANAGED_MCP_BEGIN)
    end = content.index(MANAGED_MCP_END, start) + len(MANAGED_MCP_END)
    return content[start:end] + "\n"


def _empty_state() -> dict[str, object]:
    return {
        "version": 1,
        "legacy_config_block": None,
        "legacy_skill_links": [],
    }


def _load_state(path: Path, home: Path) -> dict[str, object]:
    if path.is_symlink():
        raise RuntimeError(
            f"managed Codex install state must not be a symlink: {path}"
        )
    if not path.exists():
        return _empty_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"managed Codex install state is unreadable: {path}"
        ) from exc
    if (
        not isinstance(state, dict)
        or state.get("version") != 1
        or not isinstance(state.get("legacy_skill_links"), list)
        or (
            state.get("legacy_config_block") is not None
            and not isinstance(state.get("legacy_config_block"), str)
        )
    ):
        raise RuntimeError(
            f"managed Codex install state has an unsupported format: {path}"
        )
    block = state.get("legacy_config_block")
    if isinstance(block, str) and (
        block.count(MANAGED_MCP_BEGIN) != 1
        or block.count(MANAGED_MCP_END) != 1
        or not block.lstrip().startswith(MANAGED_MCP_BEGIN)
    ):
        raise RuntimeError(
            f"managed Codex install state contains an invalid legacy block: {path}"
        )

    allowed_paths = {
        str(home / "skills" / name)
        for name in _LEGACY_SKILL_NAMES
    }
    allowed_targets = {
        str((root / name).resolve(strict=False))
        for root in _legacy_source_roots()
        for name in _LEGACY_SKILL_NAMES
    }
    for item in state["legacy_skill_links"]:
        if (
            not isinstance(item, Mapping)
            or item.get("path") not in allowed_paths
            or item.get("target") not in allowed_targets
        ):
            raise RuntimeError(
                f"managed Codex install state contains an unsafe legacy link: {path}"
            )
    return state


def _state_bytes(state: Mapping[str, object]) -> bytes:
    return (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()


def _capture_legacy_state(
    home: Path,
    state: dict[str, object],
) -> tuple[dict[str, object], list[str]]:
    updated = dict(state)
    warnings: list[str] = []
    config_path = home / "config.toml"
    content = (
        config_path.read_text(encoding="utf-8")
        if config_path.exists()
        else ""
    )
    has_begin = MANAGED_MCP_BEGIN in content
    has_end = MANAGED_MCP_END in content
    if has_begin != has_end:
        warnings.append(
            "legacy managed MCP markers are incomplete; config was not changed"
        )
    elif has_begin and updated.get("legacy_config_block") is None:
        updated["legacy_config_block"] = _extract_managed_legacy_block(content)

    existing_links: dict[str, str] = {}
    raw_links = updated.get("legacy_skill_links", [])
    if isinstance(raw_links, list):
        for item in raw_links:
            if (
                isinstance(item, Mapping)
                and isinstance(item.get("path"), str)
                and isinstance(item.get("target"), str)
            ):
                existing_links[str(item["path"])] = str(item["target"])
    for path, target in _managed_legacy_links(home).items():
        existing_links[str(path)] = str(target)
    updated["legacy_skill_links"] = [
        {"path": path, "target": target}
        for path, target in sorted(existing_links.items())
    ]
    return updated, warnings


def _legacy_removal_changes(
    home: Path,
    warnings: list[str],
) -> list[CodexFileChange]:
    changes: list[CodexFileChange] = []
    config_path = home / "config.toml"
    if config_path.exists():
        content = config_path.read_text(encoding="utf-8")
        has_begin = MANAGED_MCP_BEGIN in content
        has_end = MANAGED_MCP_END in content
        if has_begin and has_end:
            updated = remove_managed_codex_block(content)
            changes.append(
                CodexFileChange(
                    "update" if updated else "remove",
                    config_path,
                )
            )
        elif has_begin != has_end and not any(
            "markers are incomplete" in warning for warning in warnings
        ):
            warnings.append(
                "legacy managed MCP markers are incomplete; config was not changed"
            )
    changes.extend(
        CodexFileChange("remove", path)
        for path in sorted(_managed_legacy_links(home))
    )
    return changes


def _apply_legacy_removal(home: Path) -> None:
    config_path = home / "config.toml"
    if config_path.exists():
        content = config_path.read_text(encoding="utf-8")
        if MANAGED_MCP_BEGIN in content and MANAGED_MCP_END in content:
            updated = remove_managed_codex_block(content)
            if updated:
                _atomic_write(
                    config_path,
                    updated.encode(),
                    mode=PRIVATE_FILE_MODE,
                )
            else:
                config_path.unlink()
    for path in _managed_legacy_links(home):
        path.unlink()


def install_codex(
    *,
    dry_run: bool = False,
    runner: CodexRunner = _run_codex_json,
) -> CodexInstallReport:
    home = codex_home()
    payloads = _managed_payloads(home)
    status = _registry_status(home, runner)
    state_path = _state_path(home)
    state, warnings = _capture_legacy_state(
        home,
        _load_state(state_path, home),
    )
    state_payload = _state_bytes(state)

    file_changes = [
        change
        for path, payload in sorted(payloads.items())
        if (change := _file_change(path, payload)) is not None
    ]
    state_change = _file_change(state_path, state_payload)
    if state_change is not None:
        file_changes.append(state_change)
    file_changes.extend(_legacy_removal_changes(home, warnings))

    plugin_changed = any(
        str(change.path).startswith(str(_installed_plugin_root(home)))
        for change in file_changes
    )
    registry_changes: list[str] = []
    if not status.marketplace_registered:
        registry_changes.append(
            f"register_marketplace={CODEX_MARKETPLACE_NAME}"
        )
    if not status.plugin_installed or plugin_changed:
        registry_changes.append(f"install_plugin={PLUGIN_ID}")

    report = CodexInstallReport(
        operation="install",
        dry_run=dry_run,
        codex_home=home,
        file_changes=tuple(file_changes),
        registry_changes=tuple(registry_changes),
        warnings=tuple(warnings),
    )
    if dry_run:
        return report

    home.mkdir(parents=True, exist_ok=True)
    for path, payload in payloads.items():
        _atomic_write(path, payload)
    _atomic_write(state_path, state_payload, mode=PRIVATE_FILE_MODE)
    _apply_legacy_removal(home)

    if not status.marketplace_registered:
        runner(
            (
                "plugin",
                "marketplace",
                "add",
                str(_managed_root(home)),
                "--json",
            ),
            home,
        )
    if not status.plugin_installed or plugin_changed:
        runner(("plugin", "add", PLUGIN_ID, "--json"), home)
    return report


def _config_restore_change(
    home: Path,
    block: str,
    warnings: list[str],
) -> CodexFileChange | None:
    config_path = home / "config.toml"
    current = (
        config_path.read_text(encoding="utf-8")
        if config_path.exists()
        else ""
    )
    has_begin = MANAGED_MCP_BEGIN in current
    has_end = MANAGED_MCP_END in current
    if has_begin and has_end:
        return None
    if has_begin or has_end:
        warnings.append(
            "legacy managed MCP block was not restored because "
            "the current managed markers are incomplete"
        )
        return None
    if f"[mcp_servers.researchRegistry]" in current:
        warnings.append(
            "legacy managed MCP block was not restored because "
            "researchRegistry is now configured by the user"
        )
        return None
    return CodexFileChange(
        "restore" if config_path.exists() else "create",
        config_path,
    )


def _append_config_block(content: str, block: str) -> str:
    prefix = content.rstrip()
    if not prefix:
        return block
    return f"{prefix}\n\n{block}"


def _restoration_plan(
    home: Path,
    state: dict[str, object],
    warnings: list[str],
) -> tuple[list[CodexFileChange], dict[str, object]]:
    changes: list[CodexFileChange] = []
    remaining = dict(state)
    block = state.get("legacy_config_block")
    if isinstance(block, str):
        change = _config_restore_change(home, block, warnings)
        if change is not None:
            changes.append(change)
            remaining["legacy_config_block"] = None
        else:
            config_path = home / "config.toml"
            current = (
                config_path.read_text(encoding="utf-8")
                if config_path.exists()
                else ""
            )
            if (
                MANAGED_MCP_BEGIN in current
                and MANAGED_MCP_END in current
            ):
                remaining["legacy_config_block"] = None

    remaining_links: list[dict[str, str]] = []
    raw_links = state.get("legacy_skill_links", [])
    if isinstance(raw_links, list):
        for item in raw_links:
            if not isinstance(item, Mapping):
                continue
            path_value = item.get("path")
            target_value = item.get("target")
            if not isinstance(path_value, str) or not isinstance(
                target_value, str
            ):
                continue
            path = Path(path_value)
            target = Path(target_value)
            if path.is_symlink() and path.resolve(strict=False) == target:
                continue
            if path.exists() or path.is_symlink():
                warnings.append(
                    f"legacy skill link was not restored because the path is occupied: {path}"
                )
                remaining_links.append(
                    {"path": str(path), "target": str(target)}
                )
                continue
            if not target.exists():
                warnings.append(
                    f"legacy skill link was not restored because its source is unavailable: {path}"
                )
                remaining_links.append(
                    {"path": str(path), "target": str(target)}
                )
                continue
            changes.append(CodexFileChange("restore", path))
    remaining["legacy_skill_links"] = remaining_links
    return changes, remaining


def _managed_removal_changes(home: Path) -> list[CodexFileChange]:
    paths = [
        _installed_plugin_root(home) / relative
        for relative in _PLUGIN_FILES
    ]
    paths.append(_marketplace_path(home))
    return [
        CodexFileChange("remove", path)
        for path in paths
        if path.exists() or path.is_symlink()
    ]


def _apply_restoration(
    home: Path,
    state: dict[str, object],
) -> None:
    block = state.get("legacy_config_block")
    if isinstance(block, str):
        config_path = home / "config.toml"
        current = (
            config_path.read_text(encoding="utf-8")
            if config_path.exists()
            else ""
        )
        if (
            MANAGED_MCP_BEGIN not in current
            and MANAGED_MCP_END not in current
            and "[mcp_servers.researchRegistry]" not in current
        ):
            _atomic_write(
                config_path,
                _append_config_block(current, block).encode(),
                mode=PRIVATE_FILE_MODE,
            )

    raw_links = state.get("legacy_skill_links", [])
    if not isinstance(raw_links, list):
        return
    for item in raw_links:
        if not isinstance(item, Mapping):
            continue
        path_value = item.get("path")
        target_value = item.get("target")
        if not isinstance(path_value, str) or not isinstance(target_value, str):
            continue
        path = Path(path_value)
        target = Path(target_value)
        if (
            not path.exists()
            and not path.is_symlink()
            and target.exists()
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target, target_is_directory=True)


def _remove_managed_payloads(home: Path, *, remove_state: bool) -> None:
    paths = [
        _installed_plugin_root(home) / relative
        for relative in _PLUGIN_FILES
    ]
    paths.append(_marketplace_path(home))
    if remove_state:
        paths.append(_state_path(home))
    for path in paths:
        if path.is_file() or path.is_symlink():
            path.unlink()

    root = _managed_root(home)
    directories = sorted(
        {
            parent
            for path in paths
            for parent in path.parents
            if parent == root or root in parent.parents
        },
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except (FileNotFoundError, OSError):
            continue


def uninstall_codex(
    *,
    dry_run: bool = False,
    runner: CodexRunner = _run_codex_json,
) -> CodexInstallReport:
    home = codex_home()
    status = _registry_status(home, runner)
    state_path = _state_path(home)
    state = _load_state(state_path, home)
    warnings: list[str] = []
    restoration_changes, remaining_state = _restoration_plan(
        home,
        state,
        warnings,
    )
    restoration_complete = (
        remaining_state.get("legacy_config_block") is None
        and remaining_state.get("legacy_skill_links") == []
    )

    file_changes = _managed_removal_changes(home)
    file_changes.extend(restoration_changes)
    if state_path.exists():
        file_changes.append(
            CodexFileChange(
                "remove" if restoration_complete else "update",
                state_path,
            )
        )

    registry_changes: list[str] = []
    if status.plugin_installed:
        registry_changes.append(f"remove_plugin={PLUGIN_ID}")
    if status.marketplace_registered:
        registry_changes.append(
            f"remove_marketplace={CODEX_MARKETPLACE_NAME}"
        )
    report = CodexInstallReport(
        operation="uninstall",
        dry_run=dry_run,
        codex_home=home,
        file_changes=tuple(file_changes),
        registry_changes=tuple(registry_changes),
        warnings=tuple(warnings),
    )
    if dry_run:
        return report

    if status.plugin_installed:
        runner(("plugin", "remove", PLUGIN_ID, "--json"), home)
    if status.marketplace_registered:
        runner(
            (
                "plugin",
                "marketplace",
                "remove",
                CODEX_MARKETPLACE_NAME,
                "--json",
            ),
            home,
        )
    _apply_restoration(home, state)
    _remove_managed_payloads(home, remove_state=restoration_complete)
    if not restoration_complete:
        _atomic_write(
            state_path,
            _state_bytes(remaining_state),
            mode=PRIVATE_FILE_MODE,
        )
    return report


def diagnose_codex_install(
    *,
    runner: CodexRunner = _run_codex_json,
) -> list[LocalDoctorCheck]:
    home = codex_home()
    checks: list[LocalDoctorCheck] = []
    try:
        payloads = _managed_payloads(home)
    except RuntimeError as exc:
        return [LocalDoctorCheck("codex_plugin_assets", False, str(exc))]

    mismatched = [
        path
        for path, payload in payloads.items()
        if not path.is_file() or path.read_bytes() != payload
    ]
    checks.append(
        LocalDoctorCheck(
            "codex_plugin_files",
            not mismatched,
            "managed plugin files match the installed package"
            if not mismatched
            else f"managed plugin files missing or stale: {len(mismatched)}",
        )
    )

    try:
        mcp_config = json.loads(
            (
                _installed_plugin_root(home) / ".mcp.json"
            ).read_text(encoding="utf-8")
        )
        server = mcp_config["mcpServers"]["researchRegistry"]
        stdio_ok = (
            isinstance(server, dict)
            and server.get("command") == "research-registry"
            and server.get("args") == ["mcp", "--transport", "stdio"]
            and "url" not in server
        )
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        stdio_ok = False
    checks.append(
        LocalDoctorCheck(
            "codex_plugin_mcp",
            stdio_ok,
            "bundled local STDIO MCP wiring is present"
            if stdio_ok
            else "bundled local STDIO MCP wiring is missing or invalid",
        )
    )

    try:
        status = _registry_status(home, runner)
    except RuntimeError as exc:
        checks.append(
            LocalDoctorCheck(
                "codex_plugin_registry",
                False,
                str(exc),
            )
        )
    else:
        checks.extend(
            [
                LocalDoctorCheck(
                    "codex_plugin_marketplace",
                    status.marketplace_registered,
                    "managed local marketplace registered"
                    if status.marketplace_registered
                    else "managed local marketplace is not registered",
                ),
                LocalDoctorCheck(
                    "codex_plugin_installed",
                    status.plugin_installed,
                    "research-registry plugin installed"
                    if status.plugin_installed
                    else "research-registry plugin is not installed",
                ),
            ]
        )

    legacy_present = bool(_managed_legacy_links(home))
    config_path = home / "config.toml"
    if config_path.exists():
        config_content = config_path.read_text(encoding="utf-8")
        legacy_present = legacy_present or (
            MANAGED_MCP_BEGIN in config_content
            or MANAGED_MCP_END in config_content
        )
    checks.append(
        LocalDoctorCheck(
            "codex_plugin_legacy",
            not legacy_present,
            "managed legacy Codex integration is absent"
            if not legacy_present
            else "managed legacy Codex integration still needs migration",
        )
    )
    return checks


def format_codex_install_report(report: CodexInstallReport) -> str:
    lines = [
        f"operation={report.operation}",
        f"dry_run={str(report.dry_run).lower()}",
        f"changed={str(report.changed).lower()}",
        f"codex_home={report.codex_home}",
    ]
    lines.extend(
        f"{change.action}={change.path}"
        for change in report.file_changes
    )
    lines.extend(report.registry_changes)
    lines.extend(f"warning={warning}" for warning in report.warnings)
    return "\n".join(lines)
