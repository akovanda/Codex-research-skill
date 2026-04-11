from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import secrets


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


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.getenv("RESEARCH_REGISTRY_DATA_DIR", project_root / ".data"))
    db_path = Path(os.getenv("RESEARCH_REGISTRY_DB_PATH", data_dir / "registry.sqlite3"))
    database_url = os.getenv("RESEARCH_REGISTRY_DATABASE_URL", f"sqlite:///{db_path.expanduser().resolve()}")
    capture_queue_path = Path(
        os.getenv(
            "RESEARCH_REGISTRY_CAPTURE_QUEUE_PATH",
            data_dir / "pending-research-captures.jsonl",
        )
    )
    backend_profile_path = Path(
        os.getenv(
            "RESEARCH_REGISTRY_BACKEND_PROFILE_PATH",
            data_dir / "backend-profiles.json",
        )
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    capture_queue_path.parent.mkdir(parents=True, exist_ok=True)
    backend_profile_path.parent.mkdir(parents=True, exist_ok=True)
    host = os.getenv("RESEARCH_REGISTRY_HOST", "127.0.0.1")
    port = int(os.getenv("RESEARCH_REGISTRY_PORT", "8000"))
    return Settings(
        data_dir=data_dir,
        db_path=db_path,
        database_url=database_url,
        capture_queue_path=capture_queue_path,
        backend_profile_path=backend_profile_path,
        admin_token=os.getenv("RESEARCH_REGISTRY_ADMIN_TOKEN"),
        session_secret=os.getenv("RESEARCH_REGISTRY_SESSION_SECRET", secrets.token_hex(32)),
        host=host,
        port=port,
        default_backend_url=os.getenv("RESEARCH_REGISTRY_DEFAULT_BACKEND_URL"),
        backend_url=os.getenv("RESEARCH_REGISTRY_BACKEND_URL"),
        backend_api_key=os.getenv("RESEARCH_REGISTRY_API_KEY"),
        backend_org=os.getenv("RESEARCH_REGISTRY_ORG"),
        backend_profile=os.getenv("RESEARCH_REGISTRY_BACKEND_PROFILE"),
        public_base_url=os.getenv("RESEARCH_REGISTRY_PUBLIC_BASE_URL", f"http://{host}:{port}"),
    )
