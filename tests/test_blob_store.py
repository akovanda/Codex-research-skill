from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys

import pytest

from research_registry.ingestion.blobs import (
    BlobContainmentError,
    BlobIntegrityError,
    BlobReference,
    BlobValidationError,
    FilesystemBlobStore,
)
from research_registry.cli import main as cli_main
from research_registry.service import RegistryService


def test_staged_blob_finalizes_atomically_with_generated_key_and_private_mode(
    tmp_path: Path,
) -> None:
    store = FilesystemBlobStore(tmp_path / "blobs")
    content = b"bounded source snapshot"
    digest = sha256(content).hexdigest()

    staged = store.stage_bytes(
        content,
        expected_sha256=digest,
        expected_byte_count=len(content),
        media_type="text/plain",
    )
    assert staged.storage_key == f"sha256/{digest[:2]}/{digest[2:4]}/{digest}"
    assert store.staged_count() == 1

    finalized = store.finalize(staged)
    duplicate = store.stage_bytes(content, media_type="text/plain")
    reused = store.finalize(duplicate)
    reference = BlobReference(
        sha256=digest,
        storage_key=staged.storage_key,
        byte_count=len(content),
        media_type="text/plain",
    )

    assert finalized.reused is False
    assert reused.reused is True
    assert store.read(reference) == content
    assert store.staged_count() == 0
    assert stat.S_IMODE(finalized.path.stat().st_mode) == 0o600
    if os.name != "nt":
        assert stat.S_IMODE(store.root.stat().st_mode) == 0o700


def test_staging_validates_hash_size_and_media_type(tmp_path: Path) -> None:
    store = FilesystemBlobStore(tmp_path / "blobs")
    content = b"snapshot"

    with pytest.raises(BlobValidationError, match="SHA-256"):
        store.stage_bytes(content, expected_sha256="0" * 64)
    with pytest.raises(BlobValidationError, match="byte count"):
        store.stage_bytes(content, expected_byte_count=len(content) + 1)
    with pytest.raises(BlobValidationError, match="media type"):
        store.stage_bytes(content, media_type="../../private")

    assert store.staged_count() == 0


def test_discard_removes_only_the_owned_staged_blob(tmp_path: Path) -> None:
    store = FilesystemBlobStore(tmp_path / "blobs")
    first = store.stage_bytes(b"first")
    second = store.stage_bytes(b"second")

    store.discard(first)

    assert store.staged_count() == 1
    with pytest.raises(BlobValidationError, match="not staged"):
        store.finalize(first)
    store.discard(second)
    assert store.staged_count() == 0


def test_blob_keys_reject_traversal_and_symlink_escape(tmp_path: Path) -> None:
    store = FilesystemBlobStore(tmp_path / "blobs")
    outside = tmp_path / "outside"
    outside.mkdir()
    digest = "ab" * 32

    with pytest.raises(BlobContainmentError):
        store.read(
            BlobReference(
                sha256=digest,
                storage_key=f"../outside/{digest}",
                byte_count=0,
                media_type=None,
            )
        )

    (store.root / "sha256").mkdir(mode=0o700)
    (store.root / "sha256" / digest[:2]).symlink_to(
        outside,
        target_is_directory=True,
    )
    with pytest.raises(BlobContainmentError, match="symlink"):
        store.read(
            BlobReference(
                sha256=digest,
                storage_key=f"sha256/{digest[:2]}/{digest[2:4]}/{digest}",
                byte_count=0,
                media_type=None,
            )
        )

    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(BlobContainmentError, match="symlink"):
        FilesystemBlobStore(linked_root)
    with pytest.raises(BlobContainmentError, match="dedicated"):
        FilesystemBlobStore(Path(Path.cwd().anchor))


def test_health_inventory_reports_missing_corrupt_and_orphan_without_bodies(
    tmp_path: Path,
) -> None:
    store = FilesystemBlobStore(tmp_path / "blobs")
    referenced_content = b"referenced-private-sentinel"
    orphan_content = b"orphan-private-sentinel"
    referenced = store.finalize(store.stage_bytes(referenced_content))
    orphan = store.finalize(store.stage_bytes(orphan_content))
    reference = BlobReference(
        sha256=referenced.sha256,
        storage_key=referenced.storage_key,
        byte_count=referenced.byte_count,
        media_type=None,
    )

    healthy = store.inspect([reference])
    assert healthy.healthy is True
    assert healthy.orphan_keys == (orphan.storage_key,)

    if os.name != "nt":
        referenced.path.chmod(0o644)
        insecure = store.inspect([reference])
        assert insecure.healthy is False
        assert insecure.unsafe_keys == (reference.storage_key,)
        referenced.path.chmod(0o600)

    referenced.path.write_bytes(b"corrupt")
    corrupted = store.inspect([reference])
    rendered = json.dumps(corrupted.to_dict(), sort_keys=True)

    assert corrupted.healthy is False
    assert corrupted.corrupt_keys == (reference.storage_key,)
    assert "referenced-private-sentinel" not in rendered
    assert "orphan-private-sentinel" not in rendered
    with pytest.raises(BlobIntegrityError):
        store.read(reference)


def test_blob_health_command_emits_content_free_integrity_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "registry.sqlite3"
    blob_root = tmp_path / "blobs"
    RegistryService(database).initialize()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "research-registry",
            "blob-health",
            "--database",
            str(database),
            "--blob-root",
            str(blob_root),
        ],
    )

    cli_main()
    payload = json.loads(capsys.readouterr().out)

    assert payload["healthy"] is True
    assert payload["referenced_objects"] == 0
    assert payload["stored_objects"] == 0
