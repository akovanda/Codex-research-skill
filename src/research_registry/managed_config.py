from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import secrets
import tomllib


DEFAULT_PORT = 8010
DEFAULT_PROJECT_NAME = "research-registry-local"
DEFAULT_IMAGE_TAG = "ghcr.io/akovanda/codex-research-skill:0.1.0"
DEFAULT_DOCKER_DATABASE_URL = "postgresql://registry:registry@postgres:5432/registry"
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


@dataclass(frozen=True)
class ManagedLocalConfig:
    config_dir: Path
    data_dir: Path
    config_path: Path
    compose_file_path: Path
    compose_env_path: Path
    compose_project_name: str
    image_tag: str
    port: int
    public_base_url: str
    backend_url: str | None
    mcp_url: str | None
    admin_token: str | None
    session_secret: str | None
    api_key: str | None = None
    docker_database_url: str = DEFAULT_DOCKER_DATABASE_URL
    deployment_mode: str = "shared"
    database_url: str | None = None
    blob_root: Path | None = None

    @property
    def capture_queue_path(self) -> Path:
        return self.data_dir / "pending-research-captures.jsonl"

    @property
    def backend_profile_path(self) -> Path:
        return self.data_dir / "backend-profiles.json"


def managed_config_dir() -> Path:
    override = os.getenv("RESEARCH_REGISTRY_MANAGED_CONFIG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    root = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return (root / "research-registry").expanduser().resolve()


def managed_data_dir() -> Path:
    override = os.getenv("RESEARCH_REGISTRY_MANAGED_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    root = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return (root / "research-registry").expanduser().resolve()


def _chmod_if_possible(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        return


def default_managed_local_config(
    *,
    port: int = DEFAULT_PORT,
    image_tag: str | None = None,
    admin_token: str | None = None,
    session_secret: str | None = None,
    api_key: str | None = None,
) -> ManagedLocalConfig:
    config_dir = managed_config_dir()
    data_dir = managed_data_dir()
    public_base_url = f"http://127.0.0.1:{port}"
    return ManagedLocalConfig(
        config_dir=config_dir,
        data_dir=data_dir,
        config_path=config_dir / "config.toml",
        compose_file_path=config_dir / "compose.yaml",
        compose_env_path=config_dir / ".env",
        compose_project_name=DEFAULT_PROJECT_NAME,
        image_tag=image_tag or os.getenv("RESEARCH_REGISTRY_IMAGE", DEFAULT_IMAGE_TAG),
        port=port,
        public_base_url=public_base_url,
        backend_url=public_base_url,
        mcp_url=f"{public_base_url}/mcp/",
        admin_token=admin_token or secrets.token_urlsafe(32),
        session_secret=session_secret or secrets.token_urlsafe(32),
        api_key=api_key,
        blob_root=data_dir / "blobs",
    )


def load_managed_local_config() -> ManagedLocalConfig | None:
    config = default_managed_local_config()
    if not config.config_path.exists():
        return None
    raw = tomllib.loads(config.config_path.read_text(encoding="utf-8"))
    server = raw.get("server", {})
    auth = raw.get("auth", {})
    local = raw.get("local", {})
    paths = raw.get("paths", {})

    port = int(server.get("port", config.port))
    public_base_url = server.get("public_base_url", f"http://127.0.0.1:{port}")
    deployment_mode = str(local.get("deployment_mode", "shared"))
    if deployment_mode not in {"personal", "shared"}:
        raise ValueError(
            "managed local configuration has an invalid deployment_mode"
        )
    backend_url = local.get("backend_url")
    mcp_url = local.get("mcp_url")
    if deployment_mode == "shared":
        backend_url = backend_url or public_base_url
        mcp_url = mcp_url or f"{public_base_url}/mcp/"
    config_dir = Path(paths.get("config_dir", config.config_dir)).expanduser().resolve()
    data_dir = Path(paths.get("data_dir", config.data_dir)).expanduser().resolve()
    database_path = Path(
        paths.get("database_path", data_dir / "registry.sqlite3")
    ).expanduser().resolve()
    database_url = local.get("database_url")
    if database_url is None and deployment_mode == "personal":
        database_url = f"sqlite:///{database_path}"
    blob_root = Path(
        paths.get("blob_root", data_dir / "blobs")
    ).expanduser().resolve()
    admin_token = auth.get("admin_token")
    session_secret = auth.get("session_secret")
    if deployment_mode == "shared" and (
        not isinstance(admin_token, str)
        or not isinstance(session_secret, str)
    ):
        raise ValueError(
            "shared managed configuration requires auth secrets"
        )
    return ManagedLocalConfig(
        config_dir=config_dir,
        data_dir=data_dir,
        config_path=config_dir / "config.toml",
        compose_file_path=Path(paths.get("compose_file_path", config_dir / "compose.yaml")).expanduser().resolve(),
        compose_env_path=Path(paths.get("compose_env_path", config_dir / ".env")).expanduser().resolve(),
        compose_project_name=local.get("compose_project_name", DEFAULT_PROJECT_NAME),
        image_tag=local.get("image_tag", DEFAULT_IMAGE_TAG),
        port=port,
        public_base_url=public_base_url,
        backend_url=backend_url,
        mcp_url=mcp_url,
        admin_token=admin_token,
        session_secret=session_secret,
        api_key=auth.get("api_key"),
        docker_database_url=server.get("docker_database_url", DEFAULT_DOCKER_DATABASE_URL),
        deployment_mode=deployment_mode,
        database_url=database_url,
        blob_root=blob_root,
    )


def write_managed_local_config(config: ManagedLocalConfig) -> None:
    config.config_dir.mkdir(parents=True, exist_ok=True)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    _chmod_if_possible(config.config_dir, PRIVATE_DIR_MODE)
    _chmod_if_possible(config.data_dir, PRIVATE_DIR_MODE)
    auth_lines: list[str] = []
    if config.admin_token is not None:
        auth_lines.append(
            f"admin_token = {_toml_string(config.admin_token)}"
        )
    if config.session_secret is not None:
        auth_lines.append(
            f"session_secret = {_toml_string(config.session_secret)}"
        )
    if config.api_key is not None:
        auth_lines.append(f"api_key = {_toml_string(config.api_key)}")
    local_endpoint_lines: list[str] = []
    if config.backend_url is not None:
        local_endpoint_lines.append(
            f"backend_url = {_toml_string(config.backend_url)}"
        )
    if config.mcp_url is not None:
        local_endpoint_lines.append(
            f"mcp_url = {_toml_string(config.mcp_url)}"
        )
    if config.database_url is not None:
        local_endpoint_lines.append(
            f"database_url = {_toml_string(config.database_url)}"
        )
    database_path = config.data_dir / "registry.sqlite3"
    if (
        config.database_url is not None
        and config.database_url.startswith("sqlite:///")
    ):
        database_path = Path(
            config.database_url.removeprefix("sqlite:///")
        )
    lines = [
        "[server]",
        f"port = {config.port}",
        f"public_base_url = {_toml_string(config.public_base_url)}",
        f"docker_database_url = {_toml_string(config.docker_database_url)}",
        "",
        "[auth]",
        *auth_lines,
        "",
        "[local]",
        f"deployment_mode = {_toml_string(config.deployment_mode)}",
        *local_endpoint_lines,
        f"compose_project_name = {_toml_string(config.compose_project_name)}",
        f"image_tag = {_toml_string(config.image_tag)}",
        "",
        "[paths]",
        f"config_dir = {_toml_string(str(config.config_dir))}",
        f"data_dir = {_toml_string(str(config.data_dir))}",
        "database_path = " + _toml_string(str(database_path)),
        "blob_root = "
        + _toml_string(str(config.blob_root or config.data_dir / "blobs")),
        "compose_file_path = "
        + _toml_string(str(config.compose_file_path)),
        "compose_env_path = "
        + _toml_string(str(config.compose_env_path)),
    ]
    content = "\n".join(lines) + "\n"
    config.config_path.write_text(content, encoding="utf-8")
    _chmod_if_possible(config.config_path, PRIVATE_FILE_MODE)


def _toml_string(value: str) -> str:
    return json.dumps(value)
