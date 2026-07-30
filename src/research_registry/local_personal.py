from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import sqlite3
import tomllib

from .application.source_versions import SourceVersionService
from .codex_install import _source_plugin_root
from .db import resolve_database_target
from .ingestion.blobs import FilesystemBlobStore
from .local_manager import LocalDoctorCheck
from .managed_config import (
    DEFAULT_DOCKER_DATABASE_URL,
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    ManagedLocalConfig,
    default_managed_local_config,
    load_managed_local_config,
    managed_config_dir,
    managed_data_dir,
)
from .migration_runner import MigrationRunner
from .service import RegistryService


class LocalInitializationError(RuntimeError):
    """Raised when a personal install cannot be initialized without data risk."""


@dataclass(frozen=True)
class PersonalRegistryPaths:
    config_dir: Path
    data_dir: Path
    config_path: Path
    database_path: Path
    blob_root: Path

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"


@dataclass(frozen=True)
class PersonalInitResult:
    paths: PersonalRegistryPaths
    created: bool
    migration_state: str
    applied_migrations: tuple[str, ...]


def default_personal_paths() -> PersonalRegistryPaths:
    config_dir = managed_config_dir()
    data_dir = managed_data_dir()
    return PersonalRegistryPaths(
        config_dir=config_dir,
        data_dir=data_dir,
        config_path=config_dir / "config.toml",
        database_path=data_dir / "registry.sqlite3",
        blob_root=data_dir / "blobs",
    )


def initialize_personal_registry_if_unconfigured() -> PersonalInitResult | None:
    """Initialize personal storage unless an explicit or shared backend wins."""
    if any(
        os.environ.get(name)
        for name in (
            "RESEARCH_REGISTRY_DATABASE_URL",
            "RESEARCH_REGISTRY_BACKEND_URL",
            "RESEARCH_REGISTRY_DEFAULT_BACKEND_URL",
        )
    ):
        return None
    managed = load_managed_local_config()
    if managed is not None and managed.deployment_mode == "shared":
        return None
    return initialize_personal_registry()


def initialize_personal_registry() -> PersonalInitResult:
    """Create or verify a private XDG SQLite install and apply migrations."""
    expected = default_personal_paths()

    created = False
    if expected.config_path.exists() or expected.config_path.is_symlink():
        config = _load_personal_config(expected.config_path)
        paths = _paths_from_config(config)
    else:
        _ensure_private_directory(expected.config_dir)
        _ensure_private_directory(expected.data_dir)
        config = _new_personal_config(expected)
        _write_personal_config_exclusive(config, expected.config_path)
        paths = expected
        created = True

    _ensure_private_directory(paths.config_dir)
    _ensure_private_directory(paths.data_dir)
    _ensure_private_file(paths.config_path)
    _reject_symlink(paths.database_path, label="SQLite database")

    blob_store = FilesystemBlobStore(paths.blob_root)
    service = RegistryService(paths.database_url)
    with service.connect() as connection:
        migration = MigrationRunner(service).migrate(connection)
    with sqlite3.connect(paths.database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        integrity = connection.execute("PRAGMA quick_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise LocalInitializationError(
            "the personal SQLite database failed its integrity check"
        )

    _ensure_private_file(paths.database_path)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{paths.database_path}{suffix}")
        if sidecar.exists():
            _ensure_private_file(sidecar)
    _ensure_private_directory(blob_store.root)
    _ensure_private_directory(blob_store.root / ".staging")

    return PersonalInitResult(
        paths=paths,
        created=created,
        migration_state=migration.status,
        applied_migrations=migration.applied_ids,
    )


def diagnose_personal_registry() -> list[LocalDoctorCheck]:
    """Inspect local config, schema, blobs, MCP wiring, and backup readiness."""
    checks: list[LocalDoctorCheck] = []
    expected = default_personal_paths()
    try:
        config = _load_personal_config(expected.config_path)
        paths = _paths_from_config(config)
        mode_ok = _private_mode_ok(paths.config_dir, PRIVATE_DIR_MODE)
        mode_ok = mode_ok and _private_mode_ok(
            paths.data_dir, PRIVATE_DIR_MODE
        )
        mode_ok = mode_ok and _private_mode_ok(
            paths.config_path, PRIVATE_FILE_MODE
        )
        checks.append(
            LocalDoctorCheck(
                "local_config",
                mode_ok,
                (
                    "personal XDG configuration is present with private modes"
                    if mode_ok
                    else "personal XDG configuration permissions are not private"
                ),
            )
        )
    except (LocalInitializationError, OSError, ValueError) as exc:
        return [
            LocalDoctorCheck(
                "local_config",
                False,
                f"personal configuration is unavailable: {exc}",
            ),
            LocalDoctorCheck(
                "sqlite_schema",
                False,
                "SQLite schema was not checked",
            ),
            LocalDoctorCheck(
                "blob_storage",
                False,
                "blob storage was not checked",
            ),
            _diagnose_mcp_stdio(),
            LocalDoctorCheck(
                "backup_ready",
                False,
                "SQLite backup readiness was not checked",
            ),
        ]

    try:
        service = RegistryService(paths.database_url)
        with service.connect() as connection:
            verification = MigrationRunner(service).verify(connection)
        schema_ok = (
            paths.database_path.is_file()
            and not paths.database_path.is_symlink()
            and _private_mode_ok(paths.database_path, PRIVATE_FILE_MODE)
            and not verification.pending_ids
        )
        checks.append(
            LocalDoctorCheck(
                "sqlite_schema",
                schema_ok,
                (
                    "SQLite migrations and integrity are current"
                    if schema_ok
                    else "SQLite database is missing, stale, or not private"
                ),
            )
        )
    except Exception as exc:
        checks.append(
            LocalDoctorCheck(
                "sqlite_schema",
                False,
                f"SQLite schema verification failed: {type(exc).__name__}",
            )
        )

    try:
        blob_store = FilesystemBlobStore(paths.blob_root)
        health = SourceVersionService(
            paths.database_url,
            blob_store,
        ).inspect_blob_health()
        blob_ok = (
            health.healthy
            and _private_mode_ok(paths.blob_root, PRIVATE_DIR_MODE)
            and _private_mode_ok(
                paths.blob_root / ".staging", PRIVATE_DIR_MODE
            )
        )
        checks.append(
            LocalDoctorCheck(
                "blob_storage",
                blob_ok,
                (
                    "content-addressed blob storage is healthy"
                    if blob_ok
                    else "content-addressed blob storage needs attention"
                ),
            )
        )
    except Exception as exc:
        checks.append(
            LocalDoctorCheck(
                "blob_storage",
                False,
                f"blob storage verification failed: {type(exc).__name__}",
            )
        )

    checks.append(_diagnose_mcp_stdio())
    backup_ok = (
        hasattr(sqlite3.Connection, "backup")
        and paths.database_path.is_file()
        and os.access(paths.data_dir, os.R_OK | os.W_OK)
    )
    checks.append(
        LocalDoctorCheck(
            "backup_ready",
            backup_ok,
            (
                "SQLite online backup and blob inventory are available"
                if backup_ok
                else "SQLite online backup prerequisites are unavailable"
            ),
        )
    )
    return checks


def _new_personal_config(
    paths: PersonalRegistryPaths,
) -> ManagedLocalConfig:
    base = default_managed_local_config()
    return replace(
        base,
        config_dir=paths.config_dir,
        data_dir=paths.data_dir,
        config_path=paths.config_path,
        compose_file_path=paths.config_dir / "compose.yaml",
        compose_env_path=paths.config_dir / ".env",
        backend_url=None,
        mcp_url=None,
        admin_token=None,
        session_secret=None,
        api_key=None,
        deployment_mode="personal",
        database_url=paths.database_url,
        blob_root=paths.blob_root,
    )


def _load_personal_config(path: Path) -> ManagedLocalConfig:
    _reject_symlink(path, label="local configuration")
    if not path.is_file():
        raise LocalInitializationError(
            "personal configuration is not initialized; run `research-registry init`"
        )
    try:
        config = load_managed_local_config()
    except (KeyError, OSError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise LocalInitializationError(
            "existing config is not a personal SQLite configuration and was not changed"
        ) from exc
    if (
        config is None
        or config.deployment_mode != "personal"
        or config.database_url is None
        or not config.database_url.startswith("sqlite:///")
        or config.backend_url is not None
        or config.api_key is not None
    ):
        raise LocalInitializationError(
            "existing config is not a personal SQLite configuration and was not changed"
        )
    expected = default_personal_paths()
    if (
        config.config_dir != expected.config_dir
        or config.data_dir != expected.data_dir
        or config.config_path != path.resolve()
    ):
        raise LocalInitializationError(
            "personal config/data roots do not match the active XDG paths"
        )
    _paths_from_config(config)
    return config


def _paths_from_config(
    config: ManagedLocalConfig,
) -> PersonalRegistryPaths:
    assert config.database_url is not None
    target = resolve_database_target(config.database_url)
    if target.kind != "sqlite" or target.sqlite_path is None:
        raise LocalInitializationError(
            "personal database must be a local SQLite path"
        )
    database_path = target.sqlite_path
    blob_root = (config.blob_root or config.data_dir / "blobs").resolve()
    if not _is_strictly_within(database_path, config.data_dir):
        raise LocalInitializationError(
            "personal SQLite database must be inside the configured data directory"
        )
    if not _is_strictly_within(blob_root, config.data_dir):
        raise LocalInitializationError(
            "personal blob root must be inside the configured data directory"
        )
    if not _is_strictly_within(config.config_path, config.config_dir):
        raise LocalInitializationError(
            "personal config file must be inside the configured config directory"
        )
    return PersonalRegistryPaths(
        config_dir=config.config_dir,
        data_dir=config.data_dir,
        config_path=config.config_path,
        database_path=database_path,
        blob_root=blob_root,
    )


def _write_personal_config_exclusive(
    config: ManagedLocalConfig,
    path: Path,
) -> None:
    assert config.database_url is not None
    assert config.blob_root is not None
    content = (
        "[server]\n"
        f"port = {config.port}\n"
        f"public_base_url = {_toml_string(config.public_base_url)}\n"
        f"docker_database_url = {_toml_string(DEFAULT_DOCKER_DATABASE_URL)}\n"
        "\n"
        "[auth]\n"
        "\n"
        "[local]\n"
        'deployment_mode = "personal"\n'
        f"database_url = {_toml_string(config.database_url)}\n"
        f"compose_project_name = {_toml_string(config.compose_project_name)}\n"
        f"image_tag = {_toml_string(config.image_tag)}\n"
        "\n"
        "[paths]\n"
        f"config_dir = {_toml_string(str(config.config_dir))}\n"
        f"data_dir = {_toml_string(str(config.data_dir))}\n"
        f"database_path = {_toml_string(config.database_url.removeprefix('sqlite:///'))}\n"
        f"blob_root = {_toml_string(str(config.blob_root))}\n"
        f"compose_file_path = {_toml_string(str(config.compose_file_path))}\n"
        f"compose_env_path = {_toml_string(str(config.compose_env_path))}\n"
    )
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            PRIVATE_FILE_MODE,
        )
    except FileExistsError:
        raise LocalInitializationError(
            "existing config appeared during initialization and was not changed"
        ) from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    _ensure_private_file(path)


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _diagnose_mcp_stdio() -> LocalDoctorCheck:
    try:
        payload = json.loads(
            (_source_plugin_root() / ".mcp.json").read_text(
                encoding="utf-8"
            )
        )
        server = payload["mcpServers"]["researchRegistry"]
        ok = (
            server.get("command") == "research-registry"
            and server.get("args") == [
                "mcp",
                "--transport",
                "stdio",
            ]
            and "url" not in server
            and "env" not in server
        )
    except (FileNotFoundError, KeyError, TypeError, json.JSONDecodeError):
        ok = False
    return LocalDoctorCheck(
        "mcp_stdio",
        ok,
        (
            "tokenless local STDIO MCP wiring is available"
            if ok
            else "tokenless local STDIO MCP wiring is missing or invalid"
        ),
    )


def _ensure_private_directory(path: Path) -> None:
    _reject_symlink(path, label="local directory")
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    if not path.is_dir():
        raise LocalInitializationError(
            f"local path is not a directory: {path}"
        )
    if os.name != "nt":
        path.chmod(PRIVATE_DIR_MODE)


def _ensure_private_file(path: Path) -> None:
    _reject_symlink(path, label="local file")
    if not path.is_file():
        raise LocalInitializationError(f"local file is unavailable: {path}")
    if os.name != "nt":
        path.chmod(PRIVATE_FILE_MODE)


def _reject_symlink(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise LocalInitializationError(f"{label} must not be a symlink: {path}")


def _private_mode_ok(path: Path, expected: int) -> bool:
    if not path.exists() or path.is_symlink():
        return False
    if os.name == "nt":
        return True
    return (path.stat().st_mode & 0o777) == expected


def _is_strictly_within(path: Path, parent: Path) -> bool:
    try:
        relative = path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return relative != Path(".")
