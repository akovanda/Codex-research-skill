from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
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
    capture_modes: frozenset[str] = frozenset()
    capture_snapshot_policy: str = "evidence_only"
    capture_allow_http: bool = False
    capture_git_roots: tuple[Path, ...] = ()
    capture_git_repositories: tuple[tuple[str, Path], ...] = ()


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
    capture_modes = frozenset(
        item.strip()
        for item in os.getenv("RESEARCH_REGISTRY_CAPTURE_MODES", "").split(",")
        if item.strip()
    )
    if not capture_modes <= {"capture"}:
        raise ValueError("RESEARCH_REGISTRY_CAPTURE_MODES contains an invalid mode")
    capture_snapshot_policy = os.getenv(
        "RESEARCH_REGISTRY_CAPTURE_SNAPSHOT_POLICY",
        "evidence_only",
    ).strip()
    if capture_snapshot_policy not in {
        "metadata_only",
        "evidence_only",
        "extracted_text",
        "full_content",
    }:
        raise ValueError(
            "RESEARCH_REGISTRY_CAPTURE_SNAPSHOT_POLICY is invalid"
        )
    capture_git_roots = tuple(
        Path(item).expanduser().resolve()
        for item in os.getenv(
            "RESEARCH_REGISTRY_CAPTURE_GIT_ROOTS",
            "",
        ).split(os.pathsep)
        if item.strip()
    )
    capture_git_repositories = _git_repositories_from_environment()
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
        capture_modes=capture_modes,
        capture_snapshot_policy=capture_snapshot_policy,
        capture_allow_http=os.getenv(
            "RESEARCH_REGISTRY_CAPTURE_ALLOW_HTTP",
            "",
        ).strip().lower()
        in {"1", "true", "yes"},
        capture_git_roots=capture_git_roots,
        capture_git_repositories=capture_git_repositories,
    )


def _git_repositories_from_environment() -> tuple[tuple[str, Path], ...]:
    raw = os.getenv("RESEARCH_REGISTRY_CAPTURE_GIT_REPOSITORIES", "").strip()
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "RESEARCH_REGISTRY_CAPTURE_GIT_REPOSITORIES must be a JSON object"
        ) from exc
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in parsed.items()
    ):
        raise ValueError(
            "RESEARCH_REGISTRY_CAPTURE_GIT_REPOSITORIES must map IDs to paths"
        )
    return tuple(
        (key, Path(value).expanduser().resolve())
        for key, value in sorted(parsed.items())
    )
