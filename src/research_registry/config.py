from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import secrets


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    capture_queue_path: Path
    admin_token: str | None
    session_secret: str
    host: str
    port: int


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.getenv("RESEARCH_REGISTRY_DATA_DIR", project_root / ".data"))
    db_path = Path(os.getenv("RESEARCH_REGISTRY_DB_PATH", data_dir / "registry.sqlite3"))
    capture_queue_path = Path(
        os.getenv(
            "RESEARCH_REGISTRY_CAPTURE_QUEUE_PATH",
            data_dir / "pending-research-captures.jsonl",
        )
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    capture_queue_path.parent.mkdir(parents=True, exist_ok=True)
    return Settings(
        data_dir=data_dir,
        db_path=db_path,
        capture_queue_path=capture_queue_path,
        admin_token=os.getenv("RESEARCH_REGISTRY_ADMIN_TOKEN"),
        session_secret=os.getenv("RESEARCH_REGISTRY_SESSION_SECRET", secrets.token_hex(32)),
        host=os.getenv("RESEARCH_REGISTRY_HOST", "127.0.0.1"),
        port=int(os.getenv("RESEARCH_REGISTRY_PORT", "8000")),
    )
