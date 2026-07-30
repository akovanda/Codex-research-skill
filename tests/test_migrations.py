from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import threading
from types import SimpleNamespace

import pytest

from research_registry.config import load_settings
from research_registry.db import DatabaseTarget, DbConnection, split_sql_script
from research_registry.managed_config import (
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    default_managed_local_config,
    write_managed_local_config,
)
from research_registry.migration_runner import MigrationRunner, load_sql_migrations
from research_registry.service import RegistryService


MIGRATION_FIXTURES = Path(__file__).parent / "fixtures" / "migrations"
LEGACY_CHECKSUMS = {
    "0001_initial": "ea4c8ad9e9773fee7f19adc31a247ddc2aa4cbd14fd544418c80b802c6cf278e",
    "0002_workflows_and_trust": "f360daa9ab2fc3a35b4157ab0766961e7ec7cf1699f4c144a21692f7b5dda54c",
}
V2_EVIDENCE_CHECKSUM = (
    "b36deb7be0d73c96a3a7df3fcf74d9b8974141495fa1d23d970b9a6b8da87f77"
)
V2_EVIDENCE_INVARIANTS_CHECKSUM = (
    "d984570d25da485b6ddee72b252c256d141d5fb3b51e88b4f9250fa4968cd306"
)


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
    assert migrations[1]["migration_id"] == "0002_workflows_and_trust"
    assert migrations[1]["checksum_sha256"]


def test_existing_flat_migration_checksums_are_stable() -> None:
    migrations = load_sql_migrations()

    assert {
        migration.migration_id: migration.checksum_sha256
        for migration in migrations
        if migration.source_kind == "flat"
    } == LEGACY_CHECKSUMS
    v2 = next(
        migration
        for migration in migrations
        if migration.migration_id == "0003_v2_evidence"
    )
    assert v2.source_kind == "bundle"
    assert v2.checksum_sha256 == V2_EVIDENCE_CHECKSUM
    assert v2.selected_files("sqlite") == ("common.sql", "sqlite.sql")
    assert v2.selected_files("postgres") == ("common.sql", "postgres.sql")
    invariants = next(
        migration
        for migration in migrations
        if migration.migration_id == "0003_v2_evidence_invariants"
    )
    assert invariants.source_kind == "bundle"
    assert invariants.checksum_sha256 == V2_EVIDENCE_INVARIANTS_CHECKSUM


def test_loads_dialect_bundle_with_one_logical_checksum() -> None:
    migrations = load_sql_migrations(MIGRATION_FIXTURES)

    bundle = migrations[1]
    assert bundle.migration_id == "0002_dialect_bundle"
    assert bundle.source_kind == "bundle"
    assert bundle.selected_files("sqlite") == ("common.sql", "sqlite.sql")
    assert bundle.selected_files("postgres") == ("common.sql", "postgres.sql")
    assert len(bundle.checksum_sha256) == 64
    assert bundle.sql_for("sqlite").endswith("END;")
    assert bundle.sql_for("postgres").endswith(
        "ON fixture_bundle USING btree (dialect);"
    )


def test_dialect_sql_splitter_preserves_triggers_and_dollar_quotes() -> None:
    sqlite_statements = split_sql_script(
        """
        CREATE TABLE sample (id TEXT, value TEXT);
        CREATE TRIGGER sample_trigger AFTER INSERT ON sample
        BEGIN
            UPDATE sample SET value = 'normalized;value' WHERE id = NEW.id;
        END;
        """,
        dialect="sqlite",
    )
    postgres_statements = split_sql_script(
        """
        CREATE FUNCTION sample_fn() RETURNS void AS $body$
        BEGIN
            PERFORM 'value;still-in-function';
        END;
        $body$ LANGUAGE plpgsql;
        CREATE TABLE sample (id TEXT);
        """,
        dialect="postgres",
    )

    assert len(sqlite_statements) == 2
    assert sqlite_statements[1].endswith("END;")
    assert len(postgres_statements) == 2
    assert "$body$ LANGUAGE plpgsql" in postgres_statements[0]


def test_postgres_migration_sql_does_not_translate_json_operators() -> None:
    class RecordingRawConnection:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, sql, params):
            self.calls.append((sql, params))
            return SimpleNamespace()

    raw = RecordingRawConnection()
    conn = DbConnection(
        DatabaseTarget(url="postgresql://redacted", kind="postgres"),
        raw,
    )

    conn.execute("SELECT payload ? 'key'")
    conn.execute("SELECT * FROM records WHERE id = ?", ("record-id",))

    assert raw.calls[0] == ("SELECT payload ? 'key'", ())
    assert raw.calls[1] == (
        "SELECT * FROM records WHERE id = %s",
        ("record-id",),
    )


def test_bundle_checksum_changes_when_any_dialect_component_changes(tmp_path: Path) -> None:
    bundle = tmp_path / "0001_bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(
        json.dumps({"migration_id": "0001_bundle", "transactional": True}),
        encoding="utf-8",
    )
    (bundle / "common.sql").write_text(
        "CREATE TABLE common_table (\n    id TEXT\n);\n",
        encoding="utf-8",
    )
    (bundle / "sqlite.sql").write_text(
        "CREATE INDEX sqlite_idx\nON common_table (id);\n",
        encoding="utf-8",
    )
    (bundle / "postgres.sql").write_text(
        "CREATE INDEX postgres_idx\nON common_table (id);\n",
        encoding="utf-8",
    )

    original = load_sql_migrations(tmp_path)[0].checksum_sha256
    for component in bundle.iterdir():
        component.write_bytes(
            component.read_bytes().replace(b"\n", b"\r\n")
        )
    assert load_sql_migrations(tmp_path)[0].checksum_sha256 == original

    (bundle / "postgres.sql").write_text(
        "CREATE INDEX postgres_changed_idx ON common_table (id);",
        encoding="utf-8",
    )

    assert load_sql_migrations(tmp_path)[0].checksum_sha256 != original


def test_rejects_duplicate_ids_and_incomplete_bundles(tmp_path: Path) -> None:
    (tmp_path / "0001_duplicate.sql").write_text("SELECT 1;", encoding="utf-8")
    duplicate = tmp_path / "0001_duplicate"
    duplicate.mkdir()
    for name, content in {
        "manifest.json": json.dumps(
            {"migration_id": "0001_duplicate", "transactional": True}
        ),
        "common.sql": "SELECT 1;",
        "sqlite.sql": "SELECT 1;",
        "postgres.sql": "SELECT 1;",
    }.items():
        (duplicate / name).write_text(content, encoding="utf-8")

    with pytest.raises(RuntimeError, match="duplicate migration id: 0001_duplicate"):
        load_sql_migrations(tmp_path)

    (tmp_path / "0001_duplicate.sql").unlink()
    (duplicate / "postgres.sql").unlink()
    with pytest.raises(
        RuntimeError,
        match=r"incomplete migration bundle: 0001_duplicate.*postgres\.sql",
    ):
        load_sql_migrations(tmp_path)

    (duplicate / "postgres.sql").write_text("SELECT 1;", encoding="utf-8")
    (duplicate / "manifest.json").write_text(
        json.dumps({"migration_id": "0001_duplicate"}),
        encoding="utf-8",
    )
    with pytest.raises(
        RuntimeError,
        match=r"invalid migration manifest: 0001_duplicate.*transactional",
    ):
        load_sql_migrations(tmp_path)


def test_dry_run_reports_and_skips_non_transactional_bundle(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "0001_non_transactional"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "migration_id": "0001_non_transactional",
                "transactional": {"sqlite": False, "postgres": True},
            }
        ),
        encoding="utf-8",
    )
    (bundle / "common.sql").write_text(
        "CREATE TABLE must_not_exist (id TEXT); "
        "-- private-source-body-sentinel",
        encoding="utf-8",
    )
    (bundle / "sqlite.sql").write_text("", encoding="utf-8")
    (bundle / "postgres.sql").write_text("", encoding="utf-8")
    service = RegistryService(tmp_path / "non-transactional.sqlite3")
    runner = MigrationRunner(service, migrations_path=tmp_path)

    with service.connect() as conn:
        result = runner.migrate(conn, dry_run=True)
        tables = service._list_tables(conn)

    assert result.skipped_non_transactional_ids == (
        "0001_non_transactional",
    )
    assert result.skipped_non_transactional_files == (
        "0001_non_transactional/common.sql",
        "0001_non_transactional/sqlite.sql",
    )
    assert "private-source-body-sentinel" not in json.dumps(result.to_dict())
    assert "must_not_exist" not in tables

    with service.connect() as conn:
        with pytest.raises(
            RuntimeError,
            match=(
                "non-transactional migration requires explicit operator "
                "recovery: 0001_non_transactional"
            ),
        ):
            runner.migrate(conn)


def test_plan_target_apply_verify_and_dry_run_are_structured(tmp_path: Path) -> None:
    service = RegistryService(tmp_path / "dialect.sqlite3")
    runner = MigrationRunner(service, migrations_path=MIGRATION_FIXTURES)

    with service.connect() as conn:
        planned = runner.plan(conn, target="0001_fixture")
        tables_after_plan = service._list_tables(conn)

    assert planned.operation == "plan"
    assert planned.database_kind == "sqlite"
    assert planned.pending_ids == ("0001_fixture",)
    assert planned.migrations[0].selected_files == ("0001_fixture.sql",)
    assert tables_after_plan == set()

    with service.connect() as conn:
        dry_run = runner.migrate(conn, dry_run=True)
        tables_after_dry_run = service._list_tables(conn)

    assert dry_run.operation == "dry_run"
    assert dry_run.rolled_back is True
    assert dry_run.applied_ids == ()
    assert dry_run.pending_ids == ("0001_fixture", "0002_dialect_bundle")
    assert tables_after_dry_run == set()

    with service.connect() as conn:
        applied = runner.migrate(conn, target="0001_fixture")
    assert applied.applied_ids == ("0001_fixture",)
    assert applied.pending_ids == ()

    with service.connect() as conn:
        verified = runner.verify(conn)
        remaining = runner.migrate(conn)
        tables = service._list_tables(conn)
        trigger = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' "
            "AND name = 'fixture_bundle_normalize_dialect'"
        ).fetchone()

    assert verified.operation == "verify"
    assert verified.verified_ids == ("0001_fixture",)
    assert verified.pending_ids == ("0002_dialect_bundle",)
    assert remaining.applied_ids == ("0002_dialect_bundle",)
    assert "fixture_base" in tables
    assert "fixture_bundle" in tables
    assert trigger["name"] == "fixture_bundle_normalize_dialect"


def test_verify_detects_checksum_drift(tmp_path: Path) -> None:
    service = RegistryService(tmp_path / "drift.sqlite3")
    runner = MigrationRunner(service)
    service.initialize()

    with service.connect() as conn:
        conn.execute(
            "UPDATE schema_migrations SET checksum_sha256 = ? WHERE migration_id = ?",
            ("0" * 64, "0001_initial"),
        )

    with service.connect() as conn:
        with pytest.raises(RuntimeError, match="migration checksum mismatch: 0001_initial"):
            runner.verify(conn)


def test_unknown_managed_tables_without_history_fail_closed(tmp_path: Path) -> None:
    service = RegistryService(tmp_path / "unmanaged.sqlite3")
    with service.connect() as conn:
        conn.execute("CREATE TABLE topics (id TEXT PRIMARY KEY)")

    with service.connect() as conn:
        with pytest.raises(
            RuntimeError,
            match="database contains managed tables but no schema history",
        ):
            MigrationRunner(service).plan(conn)

    with pytest.raises(
        RuntimeError,
        match="database contains managed tables but no schema history",
    ):
        service.initialize()

    with service.connect() as conn:
        assert "schema_migrations" not in service._list_tables(conn)


def test_concurrent_sqlite_migrators_apply_each_migration_once(tmp_path: Path) -> None:
    database = tmp_path / "concurrent.sqlite3"
    start = threading.Barrier(2)
    results = []
    errors = []

    def migrate() -> None:
        service = RegistryService(database)
        runner = MigrationRunner(service, migrations_path=MIGRATION_FIXTURES)
        try:
            start.wait(timeout=5)
            with service.connect() as conn:
                results.append(runner.migrate(conn))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=migrate), threading.Thread(target=migrate)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(len(result.applied_ids) for result in results) == [0, 2]

    service = RegistryService(database)
    with service.connect() as conn:
        rows = conn.execute(
            "SELECT migration_id, COUNT(*) AS count FROM schema_migrations GROUP BY migration_id"
        ).fetchall()
    assert {row["migration_id"]: row["count"] for row in rows} == {
        "0001_fixture": 1,
        "0002_dialect_bundle": 1,
    }


def test_postgres_migration_lock_uses_session_advisory_lock(
    tmp_path: Path,
) -> None:
    service = RegistryService(tmp_path / "lock-construction.sqlite3")
    runner = MigrationRunner(service)

    class RecordingConnection:
        target = SimpleNamespace(kind="postgres")

        def __init__(self) -> None:
            self.calls = []

        def execute(self, sql, params=()):
            self.calls.append((sql, params))

    conn = RecordingConnection()
    with runner._migration_lock(conn):
        assert len(conn.calls) == 1

    assert "pg_advisory_lock" in conn.calls[0][0]
    assert "pg_advisory_unlock" in conn.calls[1][0]
    assert conn.calls[0][1] == conn.calls[1][1]


def test_initialize_adopts_existing_legacy_schema(tmp_path: Path) -> None:
    service = RegistryService(tmp_path / "legacy.sqlite3")
    with service.connect() as conn:
        service._create_schema_legacy(conn)

    service.initialize()

    with service.connect() as conn:
        migrations = conn.execute("SELECT migration_id FROM schema_migrations ORDER BY migration_id").fetchall()
        schema_meta = conn.execute("SELECT version FROM schema_meta").fetchone()

    assert [row["migration_id"] for row in migrations] == [
        "0001_initial",
        "0002_workflows_and_trust",
        "0003_v2_evidence",
        "0003_v2_evidence_invariants",
    ]
    assert schema_meta["version"] == 4


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


def test_write_managed_local_config_sets_private_permissions(tmp_path: Path, monkeypatch) -> None:
    if os.name == "nt":
        return

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("RESEARCH_REGISTRY_MANAGED_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("RESEARCH_REGISTRY_MANAGED_DATA_DIR", str(data_dir))

    config = default_managed_local_config(
        port=8023,
        admin_token="managed-admin",
        session_secret="managed-session",
        api_key="managed-api-key",
    )
    write_managed_local_config(config)

    assert stat.S_IMODE(config.config_dir.stat().st_mode) == PRIVATE_DIR_MODE
    assert stat.S_IMODE(config.data_dir.stat().st_mode) == PRIVATE_DIR_MODE
    assert stat.S_IMODE(config.config_path.stat().st_mode) == PRIVATE_FILE_MODE
