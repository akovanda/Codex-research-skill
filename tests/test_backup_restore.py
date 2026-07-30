from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from research_registry.backup import (
    BackupVerificationError,
    backup_sqlite,
    plan_postgres_backup,
    restore_sqlite_backup,
    verify_sqlite_backup,
)
from research_registry.data_audit import audit_database
from research_registry.service import RegistryService
from tests.fixtures.v1 import populate_v1_fixture


def test_sqlite_backup_restores_and_verifies_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    manifest = tmp_path / "backup.manifest.json"
    restored = tmp_path / "restored.sqlite3"
    service = RegistryService(source)
    populate_v1_fixture(service, suffix="backup")

    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            "INSERT INTO users (id, display_name, created_at) VALUES (?, ?, ?)",
            ("wal-user", "WAL fixture user", "2026-07-30T00:00:00+00:00"),
        )

    created_manifest = backup_sqlite(source, backup, manifest_path=manifest)
    verified = verify_sqlite_backup(backup, manifest)
    restored_verification = restore_sqlite_backup(
        backup,
        restored,
        manifest_path=manifest,
        verify=True,
    )

    assert json.loads(manifest.read_text(encoding="utf-8")) == created_manifest
    assert verified["verified"] is True
    assert restored_verification["verified"] is True
    assert audit_database(source)["row_counts"] == audit_database(restored)["row_counts"]
    assert created_manifest["verification"]["source_matches_backup"] is True
    assert created_manifest["verification"]["integrity_check"] == "ok"
    assert created_manifest["verification"]["foreign_key_violations"] == 0
    assert created_manifest["blob_inventory"]["status"] == "not_configured_v1"
    assert created_manifest["artifacts"][0]["sha256"]


def test_sqlite_backup_verification_rejects_tampering_and_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    manifest = tmp_path / "backup.manifest.json"
    service = RegistryService(source)
    service.initialize()
    backup_sqlite(source, backup, manifest_path=manifest)

    with backup.open("ab") as stream:
        stream.write(b"tampered")

    with pytest.raises(BackupVerificationError, match="SHA-256"):
        verify_sqlite_backup(backup, manifest)
    with pytest.raises(FileExistsError):
        backup_sqlite(source, backup, manifest_path=tmp_path / "other.json")


def test_sqlite_backup_rejects_shared_artifact_and_manifest_path(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    output = tmp_path / "ambiguous-output"
    RegistryService(source).initialize()

    with pytest.raises(ValueError, match="must differ"):
        backup_sqlite(source, output, manifest_path=output)

    assert not output.exists()


def test_postgres_backup_plan_redacts_credentials_and_uses_argv() -> None:
    plan = plan_postgres_backup(
        "postgresql://private_user:private_password@db.example:5432/registry"
        "?sslmode=require&token=private_query_token",
        dump_path=Path("/safe/backups/registry.dump"),
        restore_database_url="postgresql://restore_user:restore_password@restore.example/registry_restore"
        "?application_name=private_restore_query",
    )

    rendered = json.dumps(plan, sort_keys=True)
    for secret in (
        "private_user",
        "private_password",
        "private_query_token",
        "restore_user",
        "restore_password",
        "private_restore_query",
    ):
        assert secret not in rendered
    assert plan["dump"]["argv"][0] == "pg_dump"
    assert plan["verify_dump"]["argv"][0] == "pg_restore"
    assert plan["restore"]["argv"][0] == "pg_restore"
    assert all(isinstance(command["argv"], list) for command in plan["commands"])
    assert all(
        command["execution"] == "display_only_non_executable"
        for command in plan["commands"]
    )
    assert plan["credential_handling"]["executable"] is False
    assert "shell_command" not in rendered
