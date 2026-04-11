from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import secrets

from .managed_config import load_managed_local_config


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    database_url: str
    capture_queue_path: Path
    backend_profile_path: Path
    admin_token: str | None
    session_secret: str
    host: str
    port: int
    default_backend_url: str | None
    backend_url: str | None
    backend_api_key: str | None
    backend_org: str | None
    backend_profile: str | None
    public_base_url: str


def _optional_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        value = default
    if value is None:
        return None
    value = value.strip()
    return value or None


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[2]
    managed = load_managed_local_config()

    data_dir = Path(
        os.getenv(
            "RESEARCH_REGISTRY_DATA_DIR",
            managed.data_dir if managed else project_root / ".data",
        )
    )
    db_path = Path(os.getenv("RESEARCH_REGISTRY_DB_PATH", data_dir / "registry.sqlite3"))
    database_url = os.getenv("RESEARCH_REGISTRY_DATABASE_URL", f"sqlite:///{db_path.expanduser().resolve()}")
    capture_queue_path = Path(
        os.getenv(
            "RESEARCH_REGISTRY_CAPTURE_QUEUE_PATH",
            managed.capture_queue_path if managed else data_dir / "pending-research-captures.jsonl",
        )
    )
    backend_profile_path = Path(
        os.getenv(
            "RESEARCH_REGISTRY_BACKEND_PROFILE_PATH",
            managed.backend_profile_path if managed else data_dir / "backend-profiles.json",
        )
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    capture_queue_path.parent.mkdir(parents=True, exist_ok=True)
    backend_profile_path.parent.mkdir(parents=True, exist_ok=True)
    host = os.getenv("RESEARCH_REGISTRY_HOST", "127.0.0.1")
    port = int(os.getenv("RESEARCH_REGISTRY_PORT", str(managed.port if managed else 8000)))
    return Settings(
        data_dir=data_dir,
        db_path=db_path,
        database_url=database_url,
        capture_queue_path=capture_queue_path,
        backend_profile_path=backend_profile_path,
        admin_token=_optional_env("RESEARCH_REGISTRY_ADMIN_TOKEN", managed.admin_token if managed else None),
        session_secret=os.getenv("RESEARCH_REGISTRY_SESSION_SECRET", managed.session_secret if managed else secrets.token_hex(32)),
        host=host,
        port=port,
        default_backend_url=_optional_env("RESEARCH_REGISTRY_DEFAULT_BACKEND_URL"),
        backend_url=_optional_env("RESEARCH_REGISTRY_BACKEND_URL", managed.backend_url if managed else None),
        backend_api_key=_optional_env("RESEARCH_REGISTRY_API_KEY", managed.api_key if managed else None),
        backend_org=_optional_env("RESEARCH_REGISTRY_ORG"),
        backend_profile=_optional_env("RESEARCH_REGISTRY_BACKEND_PROFILE"),
        public_base_url=os.getenv(
            "RESEARCH_REGISTRY_PUBLIC_BASE_URL",
            managed.public_base_url if managed else f"http://{host}:{port}",
        ),
    )
