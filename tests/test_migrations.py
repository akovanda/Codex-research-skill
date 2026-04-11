from __future__ import annotations

from pathlib import Path

from research_registry.config import load_settings
from research_registry.managed_config import default_managed_local_config, write_managed_local_config
from research_registry.service import RegistryService


def test_initialize_applies_sql_migrations_and_records_checksums(tmp_path: Path) -> None:
    service = RegistryService(tmp_path / "fresh.sqlite3")
    service.initialize()

    with service.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        migrations = conn.execute(
            "SELECT migration_id, checksum_sha256 FROM schema_migrations ORDER BY migration_id"
        ).fetchall()

    assert "topics" in tables
    assert "questions" in tables
    assert "schema_migrations" in tables
    assert "schema_meta" not in tables
    assert migrations
    assert migrations[0]["migration_id"] == "0001_initial"
    assert migrations[0]["checksum_sha256"]


def test_initialize_adopts_existing_legacy_schema(tmp_path: Path) -> None:
    service = RegistryService(tmp_path / "legacy.sqlite3")
    with service.connect() as conn:
        service._create_schema_legacy(conn)

    service.initialize()

    with service.connect() as conn:
        migrations = conn.execute("SELECT migration_id FROM schema_migrations ORDER BY migration_id").fetchall()
        schema_meta = conn.execute("SELECT version FROM schema_meta").fetchone()

    assert [row["migration_id"] for row in migrations] == ["0001_initial"]
    assert schema_meta["version"] == 3


def test_load_settings_prefers_managed_local_config_for_client_defaults(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    managed = default_managed_local_config(
        port=8019,
        admin_token="managed-admin",
        session_secret="managed-session",
        api_key="managed-api-key",
    )
    managed = managed.__class__(
        config_dir=config_dir,
        data_dir=data_dir,
        config_path=config_dir / "config.toml",
        compose_file_path=config_dir / "compose.yaml",
        compose_env_path=config_dir / ".env",
        compose_project_name=managed.compose_project_name,
        image_tag=managed.image_tag,
        port=managed.port,
        public_base_url=managed.public_base_url.replace(":8010", ":8019"),
        backend_url=managed.backend_url.replace(":8010", ":8019"),
        mcp_url=managed.mcp_url.replace(":8010", ":8019"),
        admin_token=managed.admin_token,
        session_secret=managed.session_secret,
        api_key=managed.api_key,
        docker_database_url=managed.docker_database_url,
    )
    write_managed_local_config(managed)

    monkeypatch.setenv("RESEARCH_REGISTRY_MANAGED_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("RESEARCH_REGISTRY_MANAGED_DATA_DIR", str(data_dir))
    monkeypatch.delenv("RESEARCH_REGISTRY_BACKEND_URL", raising=False)
    monkeypatch.delenv("RESEARCH_REGISTRY_API_KEY", raising=False)
    monkeypatch.delenv("RESEARCH_REGISTRY_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("RESEARCH_REGISTRY_SESSION_SECRET", raising=False)
    monkeypatch.delenv("RESEARCH_REGISTRY_PORT", raising=False)
    monkeypatch.delenv("RESEARCH_REGISTRY_PUBLIC_BASE_URL", raising=False)

    settings = load_settings()

    assert settings.port == 8019
    assert settings.public_base_url == "http://127.0.0.1:8019"
    assert settings.backend_url == "http://127.0.0.1:8019"
    assert settings.backend_api_key == "managed-api-key"
    assert settings.admin_token == "managed-admin"
    assert settings.session_secret == "managed-session"
    assert settings.capture_queue_path == data_dir / "pending-research-captures.jsonl"
