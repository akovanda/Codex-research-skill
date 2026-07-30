from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest

from research_registry.backup import (
    AUTHORITATIVE_BACKUP_TABLES,
    REBUILDABLE_SEARCH_TABLES,
    BackupVerificationError,
    backup_sqlite,
    plan_postgres_backup,
    restore_sqlite_backup,
    verify_sqlite_backup,
    sqlite_database_inventory,
)
from research_registry.application.deposit import ResearchDepositService
from research_registry.application.source_versions import SourceVersionService
from research_registry.data_audit import audit_database
from research_registry.domain.sources import SourceVersionSpec
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.service import RegistryService
from tests.fixtures.v1 import populate_v1_fixture
from tests.test_v2_deposit import _bundle


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


def test_sqlite_backup_inventories_referenced_blobs_without_content(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    manifest = tmp_path / "backup.manifest.json"
    blob_root = tmp_path / "blobs"
    service = RegistryService(source)
    service.initialize()
    with service.connect() as conn:
        conn.execute(
            """
            INSERT INTO sources (
                id, locator, title, source_type, visibility, created_at
            ) VALUES ('src_backup_blob', 'note:backup', 'Backup blob', 'note',
                      'private', '2026-07-30T00:00:00+00:00')
            """
        )
    content = b"private-backup-blob-body-sentinel"
    digest = sha256(content).hexdigest()
    SourceVersionService(
        service.database,
        FilesystemBlobStore(blob_root),
    ).create_or_reuse(
        SourceVersionSpec(
            source_id="src_backup_blob",
            version_key=None,
            version_kind="note",
            retrieved_at="2026-07-30T00:00:00+00:00",
            content_sha256=digest,
            canonical_locator="note:backup",
            snapshot_policy="full_content",
            snapshot_bytes=content,
            media_type="text/plain",
            byte_count=len(content),
        )
    )

    created = backup_sqlite(
        source,
        backup,
        manifest_path=manifest,
        blob_root=blob_root,
    )
    verified = verify_sqlite_backup(backup, manifest, blob_root=blob_root)
    rendered = manifest.read_text(encoding="utf-8")

    assert created["blob_inventory"]["status"] == "verified"
    assert created["blob_inventory"]["referenced_objects"] == 1
    assert created["blob_inventory"]["objects"] == [
        {
            "byte_count": len(content),
            "media_type": "text/plain",
            "sha256": digest,
            "storage_key": f"sha256/{digest[:2]}/{digest[2:4]}/{digest}",
        }
    ]
    assert verified["blob_references"] == 1
    assert "private-backup-blob-body-sentinel" not in rendered


def test_v2_authoritative_bundle_round_trips_with_explicit_search_policy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    manifest = tmp_path / "backup.manifest.json"
    blob_root = tmp_path / "blobs"
    service = RegistryService(source)
    service.initialize()
    ResearchDepositService(
        service.database,
        FilesystemBlobStore(blob_root),
    ).deposit(_bundle(key="backup-v2-bundle"))

    created = backup_sqlite(
        source,
        backup,
        manifest_path=manifest,
        blob_root=blob_root,
    )
    restore_sqlite_backup(
        backup,
        restored,
        manifest_path=manifest,
        verify=True,
        blob_root=blob_root,
    )

    with sqlite3.connect(source) as source_conn, sqlite3.connect(restored) as restored_conn:
        assert sqlite_database_inventory(source_conn) == sqlite_database_inventory(
            restored_conn
        )
    assert created["inventory"]["policy"] == {
        "authoritative_tables": list(AUTHORITATIVE_BACKUP_TABLES),
        "rebuildable_tables": list(REBUILDABLE_SEARCH_TABLES),
    }
    assert "search_documents" not in created["inventory"]["tables"]
    assert "claim_revisions" in created["inventory"]["tables"]
    assert "idempotency_keys" in created["inventory"]["tables"]


def test_v2_only_semantic_tampering_changes_authoritative_inventory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite3"
    copied = tmp_path / "copied.sqlite3"
    blobs = FilesystemBlobStore(tmp_path / "blobs")
    service = RegistryService(source)
    service.initialize()
    ResearchDepositService(service.database, blobs).deposit(
        _bundle(key="semantic-v2-tamper")
    )
    with sqlite3.connect(source) as source_conn:
        source_conn.backup(sqlite3.connect(copied))
    with sqlite3.connect(copied) as conn:
        conn.execute(
            "UPDATE idempotency_keys SET response_json = ?",
            ('{"tampered":true}',),
        )
    with sqlite3.connect(source) as source_conn, sqlite3.connect(copied) as copied_conn:
        source_inventory = sqlite_database_inventory(source_conn)
        copied_inventory = sqlite_database_inventory(copied_conn)
    assert source_inventory["idempotency_keys"]["row_count"] == 1
    assert copied_inventory["idempotency_keys"]["row_count"] == 1
    assert source_inventory != copied_inventory


def test_backup_verification_rejects_altered_referenced_blob(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    manifest = tmp_path / "backup.manifest.json"
    blob_root = tmp_path / "blobs"
    service = RegistryService(source)
    service.initialize()
    receipt = ResearchDepositService(
        service.database,
        FilesystemBlobStore(blob_root),
    ).deposit(_bundle(key="altered-blob"))
    backup_sqlite(
        source,
        backup,
        manifest_path=manifest,
        blob_root=blob_root,
    )
    with service.connect() as conn:
        row = conn.execute(
            "SELECT co.storage_key FROM content_objects co "
            "JOIN source_versions sv ON sv.content_object_id = co.id "
            "WHERE sv.id = ?",
            (receipt.records.source_version_ids["source"],),
        ).fetchone()
    blob_path = blob_root / row["storage_key"]
    blob_path.write_bytes(b"altered")

    with pytest.raises(BackupVerificationError, match="blob integrity"):
        verify_sqlite_backup(backup, manifest, blob_root=blob_root)
