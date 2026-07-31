from __future__ import annotations

from hashlib import sha1, sha256
import json
from pathlib import Path
import zlib

import pytest

from research_registry.application.source_versions import SourceVersionService
from research_registry.application.refresh import (
    CapturePolicy,
    ResearchRefreshService,
    SourceCaptureCoordinator,
)
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.ingestion.git import (
    GitIngestionPolicy,
    GitObjectInvalid,
    GitObjectNotFound,
    GitRepositoryDenied,
    GitSourceIngestor,
)
from research_registry.service import RegistryService


def _object(git_dir: Path, kind: str, body: bytes) -> str:
    raw = f"{kind} {len(body)}\0".encode() + body
    oid = sha1(raw).hexdigest()
    path = git_dir / "objects" / oid[:2] / oid[2:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zlib.compress(raw))
    return oid


def _repository(root: Path, path: str, content: bytes, mode: str = "100644"):
    git_dir = root / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    blob = _object(git_dir, "blob", content)
    parts = path.split("/")
    child_oid = blob
    child_mode = mode
    for part in reversed(parts):
        tree = (
            f"{child_mode} {part}".encode()
            + b"\0"
            + bytes.fromhex(child_oid)
        )
        child_oid = _object(git_dir, "tree", tree)
        child_mode = "40000"
    commit = _object(
        git_dir,
        "commit",
        (
            f"tree {child_oid}\n"
            "author Test <test@example.test> 0 +0000\n"
            "committer Test <test@example.test> 0 +0000\n\nfixture\n"
        ).encode(),
    )
    return commit, blob


def _insert_source(registry: RegistryService, source_id: str) -> None:
    with registry.connect() as conn:
        conn.execute(
            """
            INSERT INTO sources (
                id, locator, title, source_type, visibility, created_at
            ) VALUES (?, 'git:fixture:docs/source.txt', 'Git fixture',
                      'code', 'private', '2026-07-30T00:00:00+00:00')
            """,
            (source_id,),
        )


def test_git_capture_records_exact_commit_blob_path_and_file_mode(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    repo = allowed / "repo"
    repo.mkdir(parents=True)
    commit, blob = _repository(repo, "docs/source.txt", b"prefix\nexact evidence\nsuffix\n")
    hooks = repo / ".git" / "hooks"
    hooks.mkdir()
    (hooks / "post-checkout").write_text(
        "#!/bin/sh\ntouch should-not-exist\n",
        encoding="utf-8",
    )
    (repo / ".git" / "config").write_text(
        "[remote \"origin\"]\n"
        "url = https://user:top-secret@example.test/repo.git\n",
        encoding="utf-8",
    )
    registry = RegistryService(tmp_path / "registry.sqlite3")
    registry.initialize()
    _insert_source(registry, "src_git")
    versions = SourceVersionService(
        registry.database,
        FilesystemBlobStore(tmp_path / "blobs"),
    )

    captured = GitSourceIngestor(
        GitIngestionPolicy(
            allowed_roots=(allowed,),
            repositories={"fixture": repo},
        ),
        versions,
    ).capture(
        source_id="src_git",
        repository_id="fixture",
        commit_sha=commit,
        path="docs/source.txt",
        snapshot_policy="extracted_text",
    )

    record = captured.version.record
    assert (record.commit_sha, record.blob_sha, record.path) == (
        commit,
        blob,
        "docs/source.txt",
    )
    assert record.metadata["file_mode"] == "100644"
    assert record.metadata["object_type"] == "blob"
    assert record.content_sha256 == sha256(captured.content).hexdigest()
    assert not (repo / "should-not-exist").exists()
    assert "top-secret" not in json.dumps(record.metadata)
    assert str(repo) not in record.repository_locator


def test_git_roots_symlinks_submodules_and_missing_objects_are_denied(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    registry = RegistryService(tmp_path / "registry.sqlite3")
    registry.initialize()
    _insert_source(registry, "src_git")
    ingestor = GitSourceIngestor(
        GitIngestionPolicy(
            allowed_roots=(allowed,),
            repositories={"outside": outside},
        ),
        SourceVersionService(
            registry.database,
            FilesystemBlobStore(tmp_path / "blobs"),
        ),
    )

    with pytest.raises(GitRepositoryDenied):
        ingestor.capture(
            source_id="src_git",
            repository_id="outside",
            commit_sha="a" * 40,
            path="source.txt",
            snapshot_policy="metadata_only",
        )

    repo = allowed / "repo"
    repo.mkdir()
    commit, _ = _repository(repo, "vendor", b"submodule", mode="160000")
    safe = GitSourceIngestor(
        GitIngestionPolicy(
            allowed_roots=(allowed,),
            repositories={"fixture": repo},
        ),
        ingestor.versions,
    )
    with pytest.raises(GitObjectNotFound):
        safe.capture(
            source_id="src_git",
            repository_id="fixture",
            commit_sha=commit,
            path="vendor",
            snapshot_policy="metadata_only",
        )


def test_loose_git_object_decompression_is_bounded(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    repo = allowed / "repo"
    git_dir = repo / ".git"
    git_dir.mkdir(parents=True)
    oversized_body = b"x" * 20_000
    raw = f"commit {len(oversized_body)}\0".encode() + oversized_body
    oid = sha1(raw).hexdigest()
    object_path = git_dir / "objects" / oid[:2] / oid[2:]
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(zlib.compress(raw))
    registry = RegistryService(tmp_path / "registry.sqlite3")
    registry.initialize()
    _insert_source(registry, "src_git")
    ingestor = GitSourceIngestor(
        GitIngestionPolicy(
            allowed_roots=(allowed,),
            repositories={"fixture": repo},
            max_object_bytes=1_024,
        ),
        SourceVersionService(
            registry.database,
            FilesystemBlobStore(tmp_path / "blobs"),
        ),
    )

    with pytest.raises(GitObjectInvalid, match="size limit"):
        ingestor.capture(
            source_id="src_git",
            repository_id="fixture",
            commit_sha=oid,
            path="source.txt",
            snapshot_policy="metadata_only",
        )
    with registry.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS count FROM source_versions"
            ).fetchone()["count"]
            == 0
        )


def test_same_blob_at_a_new_path_creates_relocated_evidence(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    repo = allowed / "repo"
    repo.mkdir(parents=True)
    content = b"before\nexact evidence\nafter\n"
    first_commit, first_blob = _repository(repo, "old/source.txt", content)
    registry = RegistryService(tmp_path / "registry.sqlite3")
    registry.initialize()
    _insert_source(registry, "src_git")
    blob_store = FilesystemBlobStore(tmp_path / "blobs")
    git = GitSourceIngestor(
        GitIngestionPolicy(
            allowed_roots=(allowed,),
            repositories={"fixture": repo},
        ),
        SourceVersionService(registry.database, blob_store),
    )
    first = git.capture(
        source_id="src_git",
        repository_id="fixture",
        commit_sha=first_commit,
        path="old/source.txt",
        snapshot_policy="extracted_text",
    )
    selector = {
        "type": "git_line_range",
        "path": "old/source.txt",
        "commit_sha": first_commit,
        "blob_sha": first_blob,
        "start_line": 2,
        "end_line": 2,
        "exact": "exact evidence",
        "prefix": "before\n",
        "suffix": "\nafter",
    }
    with registry.connect() as conn:
        conn.execute(
            """
            INSERT INTO evidence_spans (
                id, source_version_id, quote_text, quote_sha256,
                selector_type, selector_json, confidence, anchor_state,
                review_state, trust_tier, created_at, metadata_json
            ) VALUES (
                'evd_git', ?, 'exact evidence', ?, 'git_line_range', ?,
                1.0, 'resolved', 'unreviewed', 'high',
                '2026-07-30T00:00:00+00:00', '{}'
            )
            """,
            (
                first.version.record.id,
                sha256(b"exact evidence").hexdigest(),
                json.dumps(selector, separators=(",", ":"), sort_keys=True),
            ),
        )
    second_commit, second_blob = _repository(repo, "new/source.txt", content)
    (repo / ".git" / "HEAD").write_text(second_commit + "\n", encoding="ascii")

    result = ResearchRefreshService(
        registry.database,
        capture_coordinator=SourceCaptureCoordinator(
            registry.database,
            CapturePolicy(
                enabled_modes=frozenset({"capture"}),
                max_snapshot_policy="extracted_text",
            ),
            git=git,
        ),
    ).refresh(
        {
            "protocol": "research-refresh/v2",
            "mode": "capture",
            "idempotency_key": "git-relocation-capture",
            "entities": [{"kind": "source", "id": "src_git"}],
            "snapshot_policy": "extracted_text",
        }
    )

    relocated = next(item for item in result.items if item.evidence_span_id)
    assert relocated.anchor_state == "relocated"
    with registry.connect() as conn:
        new_evidence = conn.execute(
            "SELECT * FROM evidence_spans WHERE id = ?",
            (relocated.evidence_span_id,),
        ).fetchone()
        old_evidence = conn.execute(
            "SELECT * FROM evidence_spans WHERE id = 'evd_git'"
        ).fetchone()
    new_selector = json.loads(new_evidence["selector_json"])
    assert new_selector["path"] == "new/source.txt"
    assert new_selector["commit_sha"] == second_commit
    assert new_selector["blob_sha"] == second_blob == first_blob
    assert old_evidence["anchor_state"] == "resolved"
