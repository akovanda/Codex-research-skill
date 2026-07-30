from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import urlsplit

from .data_audit import V1_TABLES, connect_database_read_only
from .db import DatabaseTarget, resolve_database_target
from .ingestion.blobs import (
    BlobReference,
    BlobValidationError,
    FilesystemBlobStore,
    storage_key_for_sha256,
    validate_media_type,
    validate_sha256,
)


BACKUP_MANIFEST_VERSION = 1


class BackupVerificationError(RuntimeError):
    """Raised when a backup differs from its recorded manifest."""


def backup_sqlite(
    source: str | Path | DatabaseTarget,
    destination: Path,
    *,
    manifest_path: Path,
    blob_root: Path | None = None,
) -> dict[str, Any]:
    """Create and verify an online SQLite backup without overwriting files."""
    resolved = source if isinstance(source, DatabaseTarget) else resolve_database_target(source)
    if resolved.kind != "sqlite":
        raise ValueError("backup_sqlite requires a SQLite database target")
    destination = destination.expanduser().resolve()
    manifest_path = manifest_path.expanduser().resolve()
    if destination == manifest_path:
        raise ValueError("backup destination and manifest path must differ")
    if destination.exists():
        raise FileExistsError(destination)
    if manifest_path.exists():
        raise FileExistsError(manifest_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    source_inventory: dict[str, dict[str, Any]]
    source_blob_inventory: list[dict[str, Any]]
    try:
        with connect_database_read_only(resolved) as source_conn:
            source_inventory = sqlite_database_inventory(source_conn.raw_connection)
            source_blob_inventory = sqlite_blob_inventory(
                source_conn.raw_connection
            )
            destination_raw = sqlite3.connect(destination)
            try:
                source_conn.raw_connection.backup(destination_raw)
            finally:
                destination_raw.close()
        if os.name != "nt":
            destination.chmod(0o600)
        backup_inventory = _inventory_from_path(destination)
        backup_blob_inventory = _blob_inventory_from_path(destination)
        integrity = _sqlite_integrity(destination)
        if source_inventory != backup_inventory:
            raise BackupVerificationError("backup inventory does not match the source database")
        if source_blob_inventory != backup_blob_inventory:
            raise BackupVerificationError(
                "backup blob references do not match the source database"
            )
        if integrity["integrity_check"] != "ok" or integrity["foreign_key_violations"]:
            raise BackupVerificationError("backup database integrity verification failed")
        blob_manifest = _build_blob_manifest(
            backup_blob_inventory,
            blob_root=blob_root,
        )
        artifact_sha256 = _file_sha256(destination)
        manifest: dict[str, Any] = {
            "format_version": BACKUP_MANIFEST_VERSION,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "database": {
                "kind": "sqlite",
                "sqlite_version": sqlite3.sqlite_version,
            },
            "artifacts": [
                {
                    "kind": "sqlite_database",
                    "sha256": artifact_sha256,
                    "byte_count": destination.stat().st_size,
                }
            ],
            "inventory": {
                "tables": backup_inventory,
            },
            "configuration": {
                "status": "not_included_v1",
            },
            "blob_inventory": blob_manifest,
            "verification": {
                **integrity,
                "source_matches_backup": True,
            },
        }
        _write_json_exclusive(manifest_path, manifest)
        verify_sqlite_backup(
            destination,
            manifest_path,
            blob_root=blob_root,
        )
        return manifest
    except Exception:
        if destination.exists() and not manifest_path.exists():
            destination.unlink()
        raise


def verify_sqlite_backup(
    backup_path: Path,
    manifest_path: Path,
    *,
    blob_root: Path | None = None,
) -> dict[str, Any]:
    """Verify a SQLite backup artifact against its manifest."""
    backup_path = backup_path.expanduser().resolve()
    manifest_path = manifest_path.expanduser().resolve()
    manifest = _load_manifest(manifest_path)
    expected_artifact = manifest["artifacts"][0]
    actual_sha256 = _file_sha256(backup_path)
    if actual_sha256 != expected_artifact["sha256"]:
        raise BackupVerificationError("backup SHA-256 does not match the manifest")
    if backup_path.stat().st_size != expected_artifact["byte_count"]:
        raise BackupVerificationError("backup byte count does not match the manifest")
    integrity = _sqlite_integrity(backup_path)
    if integrity["integrity_check"] != "ok":
        raise BackupVerificationError("backup SQLite integrity check failed")
    if integrity["foreign_key_violations"]:
        raise BackupVerificationError("backup contains foreign-key violations")
    inventory = _inventory_from_path(backup_path)
    if inventory != manifest["inventory"]["tables"]:
        raise BackupVerificationError(
            "backup row counts or deterministic row hashes differ from the manifest"
        )
    blob_inventory = _blob_inventory_from_path(backup_path)
    if blob_inventory != manifest["blob_inventory"]["objects"]:
        raise BackupVerificationError(
            "backup blob references differ from the manifest"
        )
    _verify_manifest_blobs(
        manifest["blob_inventory"],
        blob_root=blob_root,
    )
    return {
        "verified": True,
        "sha256": actual_sha256,
        **integrity,
        "table_count": len(inventory),
        "blob_references": len(blob_inventory),
    }


def restore_sqlite_backup(
    backup_path: Path,
    destination: Path,
    *,
    manifest_path: Path,
    verify: bool = False,
    blob_root: Path | None = None,
) -> dict[str, Any]:
    """Restore a SQLite backup to a new path and optionally verify all inventory."""
    backup_path = backup_path.expanduser().resolve()
    destination = destination.expanduser().resolve()
    manifest_path = manifest_path.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(destination)
    manifest = _load_manifest(manifest_path)
    if verify:
        verify_sqlite_backup(
            backup_path,
            manifest_path,
            blob_root=blob_root,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with connect_database_read_only(backup_path) as source_conn:
            destination_raw = sqlite3.connect(destination)
            try:
                source_conn.raw_connection.backup(destination_raw)
            finally:
                destination_raw.close()
        if os.name != "nt":
            destination.chmod(0o600)
        integrity = _sqlite_integrity(destination)
        inventory = _inventory_from_path(destination)
        if inventory != manifest["inventory"]["tables"]:
            raise BackupVerificationError(
                "restored row counts or deterministic row hashes differ from the manifest"
            )
        if integrity["integrity_check"] != "ok" or integrity["foreign_key_violations"]:
            raise BackupVerificationError("restored database integrity verification failed")
        blob_inventory = _blob_inventory_from_path(destination)
        if blob_inventory != manifest["blob_inventory"]["objects"]:
            raise BackupVerificationError(
                "restored blob references differ from the manifest"
            )
        return {
            "verified": True,
            **integrity,
            "table_count": len(inventory),
            "blob_references": len(blob_inventory),
        }
    except Exception:
        if destination.exists():
            destination.unlink()
        raise


def sqlite_database_inventory(raw: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Return content-free row counts and deterministic hashes for v1 tables."""
    raw.row_factory = sqlite3.Row
    present = {
        row["name"]
        for row in raw.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    inventory: dict[str, dict[str, Any]] = {}
    for table in V1_TABLES:
        if table not in present:
            continue
        row_hashes = sorted(
            _row_sha256(row)
            for row in raw.execute(f"SELECT * FROM {table}")
        )
        digest = sha256()
        for row_hash in row_hashes:
            digest.update(row_hash.encode("ascii"))
            digest.update(b"\n")
        inventory[table] = {
            "row_count": len(row_hashes),
            "content_sha256": digest.hexdigest(),
        }
    return inventory


def sqlite_blob_inventory(raw: sqlite3.Connection) -> list[dict[str, Any]]:
    """Inventory referenced filesystem blobs without reading or returning bodies."""
    raw.row_factory = sqlite3.Row
    present = {
        row["name"]
        for row in raw.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name IN ('content_objects', 'source_versions')"
        ).fetchall()
    }
    if present != {"content_objects", "source_versions"}:
        return []
    unsupported = raw.execute(
        """
        SELECT COUNT(*) AS count
        FROM content_objects co
        WHERE co.storage_backend <> 'filesystem'
          AND EXISTS (
              SELECT 1
              FROM source_versions sv
              WHERE sv.content_object_id = co.id
          )
        """
    ).fetchone()["count"]
    if unsupported:
        raise BackupVerificationError(
            "backup includes an unsupported referenced blob backend"
        )
    rows = raw.execute(
        """
        SELECT DISTINCT
            co.sha256, co.storage_key, co.byte_count, co.media_type
        FROM content_objects co
        JOIN source_versions sv ON sv.content_object_id = co.id
        WHERE co.storage_backend = 'filesystem'
        ORDER BY co.storage_key
        """
    ).fetchall()
    objects: list[dict[str, Any]] = []
    for row in rows:
        try:
            digest = validate_sha256(row["sha256"])
            expected_key = storage_key_for_sha256(digest)
            media_type = validate_media_type(row["media_type"])
        except BlobValidationError as exc:
            raise BackupVerificationError(
                "database contains invalid referenced blob metadata"
            ) from exc
        byte_count = row["byte_count"]
        if (
            not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or byte_count < 0
            or row["storage_key"] != expected_key
        ):
            raise BackupVerificationError(
                "database contains invalid referenced blob metadata"
            )
        objects.append(
            {
                "byte_count": byte_count,
                "media_type": media_type,
                "sha256": digest,
                "storage_key": expected_key,
            }
        )
    return objects


def plan_postgres_backup(
    database_url: str,
    *,
    dump_path: Path,
    restore_database_url: str | None = None,
) -> dict[str, Any]:
    """Build a redacted argv-only pg_dump/pg_restore operator plan."""
    if resolve_database_target(database_url).kind != "postgres":
        raise ValueError("Postgres backup planning requires a postgresql database URL")
    source_display = _redacted_postgres_url(database_url)
    restore_display = (
        _redacted_postgres_url(restore_database_url)
        if restore_database_url is not None
        else "<DISPOSABLE_RESTORE_DATABASE_URL>"
    )
    dump_argv = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--file",
        str(dump_path),
        "--dbname",
        source_display,
    ]
    list_argv = ["pg_restore", "--list", str(dump_path)]
    restore_argv = [
        "pg_restore",
        "--exit-on-error",
        "--single-transaction",
        "--no-owner",
        "--no-acl",
        "--dbname",
        restore_display,
        str(dump_path),
    ]
    commands = [
        {
            "name": "dump",
            "execution": "display_only_non_executable",
            "argv": dump_argv,
        },
        {
            "name": "verify_dump",
            "execution": "display_only_non_executable",
            "argv": list_argv,
        },
        {
            "name": "restore",
            "execution": "display_only_non_executable",
            "argv": restore_argv,
        },
    ]
    return {
        "format_version": 1,
        "database": {
            "kind": "postgres",
            "source": source_display,
            "disposable_restore": restore_display,
        },
        "dump": commands[0],
        "verify_dump": commands[1],
        "restore": commands[2],
        "commands": commands,
        "version_checks": [
            {
                "execution": "display_only_non_executable",
                "argv": ["pg_dump", "--version"],
            },
            {
                "execution": "display_only_non_executable",
                "argv": ["pg_restore", "--version"],
            },
            {
                "execution": "display_only_non_executable",
                "argv": [
                    "psql",
                    "--no-password",
                    "--tuples-only",
                    "--command",
                    "SHOW server_version",
                    "--dbname",
                    source_display,
                ],
            },
        ],
        "credential_handling": {
            "display_only": True,
            "executable": False,
            "instruction": (
                "Supply credentials directly to a subprocess environment or argv at execution time; "
                "never interpolate this display plan into a shell command."
            ),
        },
        "blob_inventory": {
            "status": "not_configured_v1",
            "objects": [],
        },
    }


def _redacted_postgres_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname is None:
        raise ValueError("invalid postgresql database URL")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    database = parsed.path or "/"
    return f"postgresql://<redacted>@{host}{database}"


def _inventory_from_path(path: Path) -> dict[str, dict[str, Any]]:
    with connect_database_read_only(path) as conn:
        return sqlite_database_inventory(conn.raw_connection)


def _blob_inventory_from_path(path: Path) -> list[dict[str, Any]]:
    with connect_database_read_only(path) as conn:
        return sqlite_blob_inventory(conn.raw_connection)


def _build_blob_manifest(
    objects: list[dict[str, Any]],
    *,
    blob_root: Path | None,
) -> dict[str, Any]:
    if blob_root is None and not objects:
        return {
            "status": "not_configured_v1",
            "referenced_objects": 0,
            "objects": [],
        }
    status = "inventory_only"
    if blob_root is not None:
        references = [_blob_reference(item) for item in objects]
        health = FilesystemBlobStore(blob_root).inspect(references)
        if not health.healthy:
            raise BackupVerificationError(
                "referenced blob integrity verification failed"
            )
        status = "verified"
    return {
        "status": status,
        "referenced_objects": len(objects),
        "objects": objects,
    }


def _verify_manifest_blobs(
    inventory: dict[str, Any],
    *,
    blob_root: Path | None,
) -> None:
    if inventory["status"] != "verified":
        return
    if blob_root is None:
        raise BackupVerificationError(
            "verified blob inventory requires the configured blob root"
        )
    health = FilesystemBlobStore(blob_root).inspect(
        [_blob_reference(item) for item in inventory["objects"]]
    )
    if not health.healthy:
        raise BackupVerificationError(
            "referenced blob integrity verification failed"
        )


def _blob_reference(item: dict[str, Any]) -> BlobReference:
    return BlobReference(
        sha256=item["sha256"],
        storage_key=item["storage_key"],
        byte_count=item["byte_count"],
        media_type=item["media_type"],
    )


def _sqlite_integrity(path: Path) -> dict[str, Any]:
    with connect_database_read_only(path) as conn:
        integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
        integrity_check = str(integrity_row[0])
        violations = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    return {
        "integrity_check": integrity_check,
        "foreign_key_violations": violations,
    }


def _row_sha256(row: sqlite3.Row) -> str:
    values = {
        key: _hashable_value(row[key])
        for key in sorted(row.keys())
    }
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _hashable_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "byte_count": len(value),
            "sha256": sha256(value).hexdigest(),
        }
    if isinstance(value, float):
        return {"type": "float", "hex": value.hex()}
    return value


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != "nt":
        path.chmod(0o600)


def _load_manifest(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 1024 * 1024:
        raise BackupVerificationError("backup manifest exceeds the size limit")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupVerificationError("backup manifest is not valid JSON") from exc
    _validate_manifest_shape(manifest)
    return manifest


def _validate_manifest_shape(manifest: Any) -> None:
    if (
        not isinstance(manifest, dict)
        or manifest.get("format_version") != BACKUP_MANIFEST_VERSION
    ):
        raise BackupVerificationError("unsupported backup manifest")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        raise BackupVerificationError("backup manifest must describe exactly one database artifact")
    artifact = artifacts[0]
    if (
        not isinstance(artifact, dict)
        or artifact.get("kind") != "sqlite_database"
        or not isinstance(artifact.get("sha256"), str)
        or len(artifact["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in artifact["sha256"])
        or not isinstance(artifact.get("byte_count"), int)
        or isinstance(artifact["byte_count"], bool)
        or artifact["byte_count"] < 0
    ):
        raise BackupVerificationError("backup manifest artifact is invalid")
    inventory = manifest.get("inventory")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("tables"), dict):
        raise BackupVerificationError("backup manifest inventory is invalid")
    tables = inventory["tables"]
    if any(table not in V1_TABLES for table in tables):
        raise BackupVerificationError("backup manifest includes an unknown table")
    for table_inventory in tables.values():
        if (
            not isinstance(table_inventory, dict)
            or not isinstance(table_inventory.get("row_count"), int)
            or isinstance(table_inventory["row_count"], bool)
            or table_inventory["row_count"] < 0
            or not isinstance(table_inventory.get("content_sha256"), str)
            or len(table_inventory["content_sha256"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in table_inventory["content_sha256"]
            )
        ):
            raise BackupVerificationError("backup manifest table inventory is invalid")
    blob_inventory = manifest.get("blob_inventory")
    if not isinstance(blob_inventory, dict):
        raise BackupVerificationError("backup manifest blob inventory is invalid")
    status = blob_inventory.get("status")
    objects = blob_inventory.get("objects")
    referenced_objects = blob_inventory.get("referenced_objects")
    if (
        status not in {"not_configured_v1", "inventory_only", "verified"}
        or not isinstance(objects, list)
        or not isinstance(referenced_objects, int)
        or isinstance(referenced_objects, bool)
        or referenced_objects < 0
        or referenced_objects != len(objects)
        or (status == "not_configured_v1" and objects)
    ):
        raise BackupVerificationError("backup manifest blob inventory is invalid")
    previous_key = ""
    for item in objects:
        if not isinstance(item, dict) or set(item) != {
            "byte_count",
            "media_type",
            "sha256",
            "storage_key",
        }:
            raise BackupVerificationError("backup manifest blob object is invalid")
        try:
            digest = validate_sha256(item["sha256"])
            media_type = validate_media_type(item["media_type"])
        except (BlobValidationError, KeyError, TypeError) as exc:
            raise BackupVerificationError(
                "backup manifest blob object is invalid"
            ) from exc
        byte_count = item["byte_count"]
        storage_key = item["storage_key"]
        if (
            not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or byte_count < 0
            or media_type != item["media_type"]
            or storage_key != storage_key_for_sha256(digest)
            or storage_key <= previous_key
        ):
            raise BackupVerificationError("backup manifest blob object is invalid")
        previous_key = storage_key
