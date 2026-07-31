from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest

from research_registry.application.source_versions import (
    SourceVersionConflict,
    SourceVersionService,
)
from research_registry.db import DbConnection
from research_registry.domain.sources import (
    SnapshotPolicyDenied,
    SourceVersionSpec,
)
from research_registry.ingestion.blobs import (
    BlobStoreError,
    FilesystemBlobStore,
)
from research_registry.persistence.repositories import SourceVersionRepository
from research_registry.service import RegistryService


def _insert_source(service: RegistryService, source_id: str) -> None:
    with service.connect() as conn:
        conn.execute(
            """
            INSERT INTO sources (
                id, locator, title, source_type, visibility, created_at
            ) VALUES (?, ?, ?, 'note', 'private', ?)
            """,
            (
                source_id,
                f"note:{source_id}",
                "Source version fixture",
                "2026-07-30T00:00:00+00:00",
            ),
        )


def _spec(
    source_id: str,
    content: bytes = b"immutable version snapshot",
    *,
    policy: str = "extracted_text",
    version_key: str | None = None,
) -> SourceVersionSpec:
    return SourceVersionSpec(
        source_id=source_id,
        version_key=version_key,
        version_kind="note",
        retrieved_at="2026-07-30T00:00:00+00:00",
        content_sha256=sha256(content).hexdigest(),
        canonical_locator=f"note:{source_id}",
        snapshot_policy=policy,
        snapshot_bytes=content if policy in {"extracted_text", "full_content"} else None,
        media_type="text/plain",
        byte_count=len(content),
    )


def _service(tmp_path: Path) -> tuple[RegistryService, FilesystemBlobStore, SourceVersionService]:
    registry = RegistryService(tmp_path / "registry.sqlite3")
    registry.initialize()
    blob_store = FilesystemBlobStore(tmp_path / "blobs")
    return registry, blob_store, SourceVersionService(registry.database, blob_store)


def test_same_content_reuses_content_object_and_source_version(tmp_path: Path) -> None:
    registry, blob_store, versions = _service(tmp_path)
    _insert_source(registry, "src_one")
    _insert_source(registry, "src_two")
    spec = _spec("src_one")

    first = versions.create_or_reuse(spec)
    replay = versions.create_or_reuse(spec)
    other_source = versions.create_or_reuse(
        replace(spec, source_id="src_two", canonical_locator="note:src_two")
    )

    with registry.connect() as conn:
        content_count = conn.execute(
            "SELECT COUNT(*) AS count FROM content_objects"
        ).fetchone()["count"]
        version_count = conn.execute(
            "SELECT COUNT(*) AS count FROM source_versions"
        ).fetchone()["count"]

    assert first.reused is False
    assert replay.reused is True
    assert replay.record.id == first.record.id
    assert other_source.record.id != first.record.id
    assert other_source.record.content_object_id == first.record.content_object_id
    assert content_count == 1
    assert version_count == 2
    assert blob_store.staged_count() == 0


def test_hash_mismatch_creates_no_database_or_blob_record(tmp_path: Path) -> None:
    registry, blob_store, versions = _service(tmp_path)
    _insert_source(registry, "src_hash")
    spec = replace(_spec("src_hash"), content_sha256="0" * 64)

    with pytest.raises(Exception, match="SHA-256"):
        versions.create_or_reuse(spec)

    with registry.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM source_versions"
        ).fetchone()["count"]
    assert count == 0
    assert blob_store.staged_count() == 0
    assert blob_store.inspect([]).stored_objects == 0


def test_database_failure_discards_staged_blob_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, blob_store, versions = _service(tmp_path)
    _insert_source(registry, "src_db_failure")

    def fail_insert(*args, **kwargs):
        raise sqlite3.IntegrityError("injected database failure")

    monkeypatch.setattr(SourceVersionRepository, "insert_source_version", fail_insert)

    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        versions.create_or_reuse(_spec("src_db_failure"))

    with registry.connect() as conn:
        content_count = conn.execute(
            "SELECT COUNT(*) AS count FROM content_objects"
        ).fetchone()["count"]
    assert content_count == 0
    assert blob_store.staged_count() == 0
    assert blob_store.inspect([]).stored_objects == 0


def test_finalize_failure_rolls_back_database_and_discards_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, blob_store, versions = _service(tmp_path)
    _insert_source(registry, "src_finalize_failure")

    def fail_finalize(*args, **kwargs):
        raise BlobStoreError("injected finalization failure")

    monkeypatch.setattr(blob_store, "finalize", fail_finalize)

    with pytest.raises(BlobStoreError, match="injected"):
        versions.create_or_reuse(_spec("src_finalize_failure"))

    with registry.connect() as conn:
        counts = conn.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM content_objects) AS contents, "
            "(SELECT COUNT(*) FROM source_versions) AS versions"
        ).fetchone()
    assert dict(counts) == {"contents": 0, "versions": 0}
    assert blob_store.staged_count() == 0


def test_commit_failure_never_leaves_a_database_reference_to_uncommitted_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, blob_store, versions = _service(tmp_path)
    _insert_source(registry, "src_commit_failure")
    original_commit = DbConnection.commit
    fail_next = True

    def fail_one_commit(connection: DbConnection) -> None:
        nonlocal fail_next
        if fail_next:
            fail_next = False
            raise sqlite3.OperationalError("injected commit failure")
        original_commit(connection)

    monkeypatch.setattr(DbConnection, "commit", fail_one_commit)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        versions.create_or_reuse(_spec("src_commit_failure"))

    with registry.connect() as conn:
        counts = conn.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM content_objects) AS contents, "
            "(SELECT COUNT(*) FROM source_versions) AS versions"
        ).fetchone()
    health = blob_store.inspect([])
    assert dict(counts) == {"contents": 0, "versions": 0}
    assert blob_store.staged_count() == 0
    assert len(health.orphan_keys) == 1


@pytest.mark.parametrize("policy", ["metadata_only", "evidence_only"])
def test_non_body_snapshot_policies_never_store_source_bytes(
    tmp_path: Path,
    policy: str,
) -> None:
    registry, blob_store, versions = _service(tmp_path)
    source_id = f"src_{policy}"
    _insert_source(registry, source_id)
    content = b"hash-only source observation"
    spec = _spec(source_id, content, policy=policy)

    result = versions.create_or_reuse(spec)

    assert result.record.content_object_id is None
    assert blob_store.inspect([]).stored_objects == 0
    with pytest.raises(SnapshotPolicyDenied, match="must not include"):
        versions.create_or_reuse(replace(spec, version_key="body", snapshot_bytes=content))


@pytest.mark.parametrize("policy", ["extracted_text", "full_content"])
def test_body_snapshot_policies_require_content_bytes(
    tmp_path: Path,
    policy: str,
) -> None:
    registry, blob_store, versions = _service(tmp_path)
    source_id = f"src_{policy}"
    _insert_source(registry, source_id)
    spec = replace(_spec(source_id, policy=policy), snapshot_bytes=None)

    with pytest.raises(SnapshotPolicyDenied, match="requires snapshot bytes"):
        versions.create_or_reuse(spec)
    assert blob_store.staged_count() == 0


def test_machine_policy_can_tighten_but_not_raise_snapshot_retention(
    tmp_path: Path,
) -> None:
    registry = RegistryService(tmp_path / "registry.sqlite3")
    registry.initialize()
    _insert_source(registry, "src_policy")
    blob_store = FilesystemBlobStore(tmp_path / "blobs")
    versions = SourceVersionService(
        registry.database,
        blob_store,
        max_snapshot_policy="evidence_only",
    )

    with pytest.raises(SnapshotPolicyDenied, match="machine policy"):
        versions.create_or_reuse(_spec("src_policy", policy="extracted_text"))


def test_version_key_conflict_and_source_version_mutation_are_refused(
    tmp_path: Path,
) -> None:
    registry, _, versions = _service(tmp_path)
    _insert_source(registry, "src_immutable")
    first = versions.create_or_reuse(_spec("src_immutable", version_key="stable-key"))

    with pytest.raises(SourceVersionConflict):
        versions.create_or_reuse(
            _spec(
                "src_immutable",
                b"different immutable bytes",
                version_key="stable-key",
            )
        )
    with registry.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE source_versions SET canonical_locator = ? WHERE id = ?",
                ("note:mutated", first.record.id),
            )
