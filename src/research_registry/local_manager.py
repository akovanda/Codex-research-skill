from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import httpx

from .managed_config import DEFAULT_PORT, ManagedLocalConfig, default_managed_local_config, load_managed_local_config, write_managed_local_config


LOCAL_MCP_SERVER_NAME = "researchRegistry"
MANAGED_MCP_BEGIN = "# BEGIN research-registry managed mcp"
MANAGED_MCP_END = "# END research-registry managed mcp"
DEFAULT_API_KEY_LABEL = "local-codex"
DEFAULT_API_KEY_USER = "codex-local"
DEFAULT_READY_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class LocalRuntimeStatus:
    configured: bool
    ready: bool
    base_url: str | None
    mcp_url: str | None
    api_key_configured: bool
    codex_config_path: Path
    codex_mcp_managed: bool
    compose_file_path: Path | None
    docker_status: str | None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def codex_home() -> Path:
    return Path(os.getenv("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()


def codex_config_path() -> Path:
    return codex_home() / "config.toml"


def build_local_config(*, port: int | None = None, existing: ManagedLocalConfig | None = None) -> ManagedLocalConfig:
    selected_port = port or (existing.port if existing else DEFAULT_PORT)
    config = default_managed_local_config(
        port=selected_port,
        admin_token=existing.admin_token if existing else None,
        session_secret=existing.session_secret if existing else None,
        api_key=existing.api_key if existing else None,
    )
    if existing is None:
        return config
    return replace(
        config,
        compose_project_name=existing.compose_project_name,
        image_tag=existing.image_tag,
        docker_database_url=existing.docker_database_url,
    )


def render_compose_yaml(config: ManagedLocalConfig) -> str:
    return f"""services:
  postgres:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_DB: registry
      POSTGRES_USER: registry
      POSTGRES_PASSWORD: registry
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U registry -d registry"]
      interval: 5s
      timeout: 5s
      retries: 10

  app:
    image: {config.image_tag}
    restart: unless-stopped
    env_file:
      - ./.env
    ports:
      - "127.0.0.1:{config.port}:8000"
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  postgres-data:
"""


def render_compose_env(config: ManagedLocalConfig) -> str:
    return (
        f"RESEARCH_REGISTRY_DATABASE_URL={config.docker_database_url}\n"
        f"RESEARCH_REGISTRY_ADMIN_TOKEN={config.admin_token}\n"
        f"RESEARCH_REGISTRY_SESSION_SECRET={config.session_secret}\n"
        "RESEARCH_REGISTRY_HOST=0.0.0.0\n"
        "RESEARCH_REGISTRY_PORT=8000\n"
        f"RESEARCH_REGISTRY_PUBLIC_BASE_URL={config.public_base_url}\n"
    )


def write_local_runtime_files(config: ManagedLocalConfig) -> None:
    config.config_dir.mkdir(parents=True, exist_ok=True)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.compose_file_path.write_text(render_compose_yaml(config), encoding="utf-8")
    config.compose_env_path.write_text(render_compose_env(config), encoding="utf-8")


def render_codex_mcp_block(config: ManagedLocalConfig) -> str:
    if not config.api_key:
        raise RuntimeError("managed local config is missing api_key")
    return (
        f"{MANAGED_MCP_BEGIN}\n"
        f"[mcp_servers.{LOCAL_MCP_SERVER_NAME}]\n"
        f'url = "{config.mcp_url}"\n'
        "enabled = true\n"
        "startup_timeout_sec = 20\n"
        "tool_timeout_sec = 60\n"
        "\n"
        f"[mcp_servers.{LOCAL_MCP_SERVER_NAME}.http_headers]\n"
        f'"x-api-key" = "{config.api_key}"\n'
        f"{MANAGED_MCP_END}\n"
    )


def upsert_managed_codex_config(content: str, config: ManagedLocalConfig) -> str:
    block = render_codex_mcp_block(config)
    if MANAGED_MCP_BEGIN in content and MANAGED_MCP_END in content:
        start = content.index(MANAGED_MCP_BEGIN)
        end = content.index(MANAGED_MCP_END) + len(MANAGED_MCP_END)
        prefix = content[:start].rstrip()
        suffix = content[end:].lstrip("\n")
        pieces = [piece for piece in [prefix, block.rstrip(), suffix] if piece]
        return "\n\n".join(pieces) + "\n"

    unmanaged_marker = f"[mcp_servers.{LOCAL_MCP_SERVER_NAME}]"
    if unmanaged_marker in content:
        raise RuntimeError(
            f"{codex_config_path()} already defines {unmanaged_marker}; remove it or convert it to the managed block"
        )

    if content and not content.endswith("\n"):
        content += "\n"
    if content.strip():
        content += "\n"
    return content + block


def ensure_codex_mcp_config(config: ManagedLocalConfig) -> Path:
    path = codex_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    updated = upsert_managed_codex_config(current, config)
    if path.exists() and current != updated:
        backup_path = path.with_name(f"{path.name}.research-registry.bak")
        backup_path.write_text(current, encoding="utf-8")
    path.write_text(updated, encoding="utf-8")
    return path


def managed_skill_sources() -> dict[str, Path]:
    root = repo_root() / "skills"
    return {
        "research-capture": root / "research-capture",
        "research-memory-retrieval": root / "research-memory-retrieval",
    }


def ensure_skill_links() -> list[Path]:
    installed: list[Path] = []
    skills_dir = codex_home() / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    for name, source in managed_skill_sources().items():
        target = skills_dir / name
        if target.is_symlink():
            if target.resolve() == source.resolve():
                installed.append(target)
                continue
            target.unlink()
        elif target.exists():
            installed.append(target)
            continue

        target.symlink_to(source, target_is_directory=True)
        installed.append(target)
    return installed


def compose_command(config: ManagedLocalConfig, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        config.compose_project_name,
        "-f",
        str(config.compose_file_path),
        "--env-file",
        str(config.compose_env_path),
        *args,
    ]


def run_checked(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=True)


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def probe_ready(base_url: str) -> bool:
    try:
        response = httpx.get(f"{base_url}/readyz", timeout=2.0)
        return response.status_code == 200 and response.json().get("status") == "ready"
    except httpx.HTTPError:
        return False


def wait_for_ready(base_url: str, *, timeout_seconds: float = DEFAULT_READY_TIMEOUT_SECONDS) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if probe_ready(base_url):
            return
        time.sleep(1.0)
    raise RuntimeError(f"local registry did not become ready at {base_url} within {timeout_seconds:.0f}s")


def validate_existing_api_key(config: ManagedLocalConfig) -> bool:
    if not config.api_key:
        return False
    try:
        response = httpx.get(
            f"{config.public_base_url}/api/backend/status",
            headers={"x-api-key": config.api_key},
            timeout=5.0,
        )
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def issue_local_api_key(config: ManagedLocalConfig) -> str:
    response = httpx.post(
        f"{config.public_base_url}/api/admin/api-keys",
        headers={"x-admin-token": config.admin_token},
        json={
            "label": DEFAULT_API_KEY_LABEL,
            "actor_user_id": DEFAULT_API_KEY_USER,
            "namespace_kind": "user",
            "namespace_id": "local",
            "scopes": ["ingest", "publish", "read_private"],
        },
        timeout=10.0,
    )
    response.raise_for_status()
    return str(response.json()["token"])


def ensure_local_api_key(config: ManagedLocalConfig) -> ManagedLocalConfig:
    if validate_existing_api_key(config):
        return config
    api_key = issue_local_api_key(config)
    return replace(config, api_key=api_key)


def ensure_port_available(config: ManagedLocalConfig, *, existing: ManagedLocalConfig | None = None) -> None:
    if port_is_free(config.port):
        return
    if existing and existing.port == config.port and probe_ready(config.public_base_url):
        return
    raise RuntimeError(f"localhost port {config.port} is already in use")


def build_local_image(config: ManagedLocalConfig) -> None:
    run_checked(["docker", "build", "-t", config.image_tag, "."], cwd=repo_root())


def start_local_stack(config: ManagedLocalConfig) -> None:
    run_checked(compose_command(config, "up", "-d"))


def stop_local_stack(config: ManagedLocalConfig) -> None:
    run_checked(compose_command(config, "down"))


def docker_status_text(config: ManagedLocalConfig) -> str | None:
    try:
        result = run_checked(compose_command(config, "ps"))
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def install_local_runtime(
    *,
    port: int | None = None,
    build_image: bool = True,
    start_stack: bool = True,
    configure_codex: bool = True,
    install_skills: bool = True,
) -> ManagedLocalConfig:
    existing = load_managed_local_config()
    config = build_local_config(port=port, existing=existing)
    ensure_port_available(config, existing=existing)

    write_local_runtime_files(config)
    write_managed_local_config(config)

    if build_image:
        build_local_image(config)
    if start_stack:
        start_local_stack(config)
        wait_for_ready(config.public_base_url)
        config = ensure_local_api_key(config)
        write_managed_local_config(config)

    if configure_codex and config.api_key:
        ensure_codex_mcp_config(config)
    if install_skills:
        ensure_skill_links()
    return config


def local_runtime_status() -> LocalRuntimeStatus:
    config = load_managed_local_config()
    path = codex_config_path()
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    if config is None:
        return LocalRuntimeStatus(
            configured=False,
            ready=False,
            base_url=None,
            mcp_url=None,
            api_key_configured=False,
            codex_config_path=path,
            codex_mcp_managed=MANAGED_MCP_BEGIN in content and MANAGED_MCP_END in content,
            compose_file_path=None,
            docker_status=None,
        )
    return LocalRuntimeStatus(
        configured=True,
        ready=probe_ready(config.public_base_url),
        base_url=config.public_base_url,
        mcp_url=config.mcp_url,
        api_key_configured=bool(config.api_key),
        codex_config_path=path,
        codex_mcp_managed=MANAGED_MCP_BEGIN in content and MANAGED_MCP_END in content,
        compose_file_path=config.compose_file_path,
        docker_status=docker_status_text(config),
    )


def stop_local_runtime() -> ManagedLocalConfig:
    config = load_managed_local_config()
    if config is None:
        raise RuntimeError("managed local config not found; nothing to stop")
    stop_local_stack(config)
    return config


def ensure_prerequisites() -> None:
    run_checked(["docker", "--version"])
    run_checked(["docker", "compose", "version"])


def format_status(status: LocalRuntimeStatus) -> str:
    lines = [
        f"configured={str(status.configured).lower()}",
        f"ready={str(status.ready).lower()}",
        f"api_key_configured={str(status.api_key_configured).lower()}",
        f"codex_mcp_managed={str(status.codex_mcp_managed).lower()}",
        f"codex_config={status.codex_config_path}",
    ]
    if status.base_url:
        lines.append(f"base_url={status.base_url}")
    if status.mcp_url:
        lines.append(f"mcp_url={status.mcp_url}")
    if status.compose_file_path:
        lines.append(f"compose_file={status.compose_file_path}")
    if status.docker_status:
        lines.append("docker_ps=")
        lines.append(status.docker_status)
    return "\n".join(lines)
