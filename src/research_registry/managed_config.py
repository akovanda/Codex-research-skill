from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import secrets
import tomllib


DEFAULT_PORT = 8010
DEFAULT_PROJECT_NAME = "research-registry-local"
DEFAULT_IMAGE_TAG = "research-registry-local:latest"
DEFAULT_DOCKER_DATABASE_URL = "postgresql://registry:registry@postgres:5432/registry"


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
    backend_url: str
    mcp_url: str
    admin_token: str
    session_secret: str
    api_key: str | None = None
    docker_database_url: str = DEFAULT_DOCKER_DATABASE_URL

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


def default_managed_local_config(
    *,
    port: int = DEFAULT_PORT,
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
        image_tag=DEFAULT_IMAGE_TAG,
        port=port,
        public_base_url=public_base_url,
        backend_url=public_base_url,
        mcp_url=f"{public_base_url}/mcp",
        admin_token=admin_token or secrets.token_urlsafe(32),
        session_secret=session_secret or secrets.token_urlsafe(32),
        api_key=api_key,
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
    backend_url = local.get("backend_url", public_base_url)
    mcp_url = local.get("mcp_url", f"{public_base_url}/mcp")
    config_dir = Path(paths.get("config_dir", config.config_dir)).expanduser().resolve()
    data_dir = Path(paths.get("data_dir", config.data_dir)).expanduser().resolve()
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
        admin_token=auth["admin_token"],
        session_secret=auth["session_secret"],
        api_key=auth.get("api_key"),
        docker_database_url=server.get("docker_database_url", DEFAULT_DOCKER_DATABASE_URL),
    )


def write_managed_local_config(config: ManagedLocalConfig) -> None:
    config.config_dir.mkdir(parents=True, exist_ok=True)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    api_key_line = f'api_key = "{config.api_key}"\n' if config.api_key else ""
    content = (
        "[server]\n"
        f"port = {config.port}\n"
        f'public_base_url = "{config.public_base_url}"\n'
        f'docker_database_url = "{config.docker_database_url}"\n'
        "\n"
        "[auth]\n"
        f'admin_token = "{config.admin_token}"\n'
        f'session_secret = "{config.session_secret}"\n'
        f"{api_key_line}"
        "\n"
        "[local]\n"
        f'backend_url = "{config.backend_url}"\n'
        f'mcp_url = "{config.mcp_url}"\n'
        f'compose_project_name = "{config.compose_project_name}"\n'
        f'image_tag = "{config.image_tag}"\n'
        "\n"
        "[paths]\n"
        f'config_dir = "{config.config_dir}"\n'
        f'data_dir = "{config.data_dir}"\n'
        f'compose_file_path = "{config.compose_file_path}"\n'
        f'compose_env_path = "{config.compose_env_path}"\n'
    )
    config.config_path.write_text(content, encoding="utf-8")
