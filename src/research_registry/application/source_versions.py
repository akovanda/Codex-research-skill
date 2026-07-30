from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..contracts.common import SnapshotPolicy
from ..db import DatabaseTarget, connect_database, resolve_database_target
from ..domain.evidence import (
    EvidenceResolution,
    SourceVersionProvenance,
    resolve_exact_evidence,
)
from ..domain.sources import SourceVersionSpec, validate_source_version_spec
from ..ingestion.blobs import (
    BlobHealthReport,
    BlobIntegrityError,
    BlobReference,
    BlobStore,
    StagedBlob,
)
from ..persistence.repositories import (
    ContentObjectRecord,
    SourceVersionRecord,
    SourceVersionRepository,
)


class SourceVersionConflict(RuntimeError):
    """Raised when a version key would mutate an immutable observation."""


class ContentObjectMissing(RuntimeError):
    """Raised when a source version references unavailable snapshot content."""


@dataclass(frozen=True)
class SourceVersionCreateResult:
    record: SourceVersionRecord
    reused: bool


class SourceVersionService:
    """Coordinate immutable database records with finalized filesystem blobs."""

    def __init__(
        self,
        database: str | Path | DatabaseTarget,
        blob_store: BlobStore,
        *,
        max_snapshot_policy: SnapshotPolicy = "full_content",
    ):
        self.database = (
            database
            if isinstance(database, DatabaseTarget)
            else resolve_database_target(database)
        )
        self.blob_store = blob_store
        self.max_snapshot_policy = max_snapshot_policy

    def create_or_reuse(
        self,
        requested: SourceVersionSpec,
    ) -> SourceVersionCreateResult:
        spec = validate_source_version_spec(
            requested,
            max_snapshot_policy=self.max_snapshot_policy,
        )
        staged: StagedBlob | None = None
        if spec.snapshot_bytes is not None:
            staged = self.blob_store.stage_bytes(
                spec.snapshot_bytes,
                expected_sha256=spec.content_sha256,
                expected_byte_count=spec.byte_count,
                media_type=spec.media_type,
            )

        try:
            result: SourceVersionCreateResult
            with connect_database(self.database) as conn:
                repository = SourceVersionRepository(conn)
                assert spec.version_key is not None
                existing = repository.find_by_source_and_key(
                    spec.source_id,
                    spec.version_key,
                )
                if existing is not None:
                    self._validate_reuse(repository, existing, spec)
                    if staged is not None:
                        self.blob_store.discard(staged)
                        staged = None
                    result = SourceVersionCreateResult(
                        record=existing,
                        reused=True,
                    )
                else:
                    content_object, needs_finalize = self._prepare_content_object(
                        repository,
                        spec,
                        staged,
                    )
                    if staged is not None and not needs_finalize:
                        self.blob_store.discard(staged)
                        staged = None
                    record = self._new_source_version(spec, content_object)
                    repository.insert_source_version(record)
                    # Finalization is deliberately the final operation before
                    # commit. SQL failures discard staging; finalize failures
                    # roll the transaction back; a commit failure can produce
                    # an inventory-visible orphan but never a dangling DB row.
                    if staged is not None and needs_finalize:
                        self.blob_store.finalize(staged)
                        staged = None
                    result = SourceVersionCreateResult(
                        record=record,
                        reused=False,
                    )
            return result
        except Exception:
            if staged is not None:
                self.blob_store.discard(staged)
            raise

    def resolve_evidence(
        self,
        source_version_id: str,
        selector: Any,
        quote_text: str,
        *,
        quote_sha256: str | None = None,
    ) -> EvidenceResolution:
        with connect_database(self.database) as conn:
            repository = SourceVersionRepository(conn)
            version = repository.get_source_version(source_version_id)
            if version.content_object_id is None:
                raise ContentObjectMissing(
                    "source version does not retain resolvable snapshot content"
                )
            content_object = repository.get_content_object(
                version.content_object_id
            )
        try:
            content = self.blob_store.read(
                self._blob_reference(content_object),
                verify=True,
            )
        except (FileNotFoundError, BlobIntegrityError) as exc:
            raise ContentObjectMissing(
                "source version snapshot content is unavailable"
            ) from exc
        return resolve_exact_evidence(
            content,
            selector,
            quote_text,
            quote_sha256=quote_sha256,
            provenance=SourceVersionProvenance(
                path=version.path,
                commit_sha=version.commit_sha,
                blob_sha=version.blob_sha,
            ),
        )

    def inspect_blob_health(self) -> BlobHealthReport:
        with connect_database(self.database) as conn:
            repository = SourceVersionRepository(conn)
            references = repository.list_referenced_blobs()
            reference_errors = repository.blob_reference_error_count()
        return replace(
            self.blob_store.inspect(references),
            database_reference_errors=reference_errors,
        )

    def _prepare_content_object(
        self,
        repository: SourceVersionRepository,
        spec: SourceVersionSpec,
        staged: StagedBlob | None,
    ) -> tuple[ContentObjectRecord | None, bool]:
        if staged is None:
            return None, False
        existing = repository.find_content_by_sha256(spec.content_sha256)
        if existing is not None:
            self._validate_content_reuse(existing, staged)
            try:
                self.blob_store.read(self._blob_reference(existing), verify=True)
            except (FileNotFoundError, BlobIntegrityError) as exc:
                raise ContentObjectMissing(
                    "referenced content object is unavailable"
                ) from exc
            return existing, False

        record = ContentObjectRecord(
            id=f"blob_{uuid4()}",
            sha256=staged.sha256,
            storage_backend="filesystem",
            storage_key=staged.storage_key,
            media_type=staged.media_type,
            byte_count=staged.byte_count,
            compression="none",
            created_at=_utc_now_text(),
            metadata={"snapshot_policy": spec.snapshot_policy},
        )
        repository.insert_content_object(record)
        return record, True

    def _validate_reuse(
        self,
        repository: SourceVersionRepository,
        existing: SourceVersionRecord,
        spec: SourceVersionSpec,
    ) -> None:
        expected = (
            spec.version_kind,
            spec.content_sha256,
            spec.canonical_locator,
            spec.media_type,
            spec.byte_count,
            spec.parser_name,
            spec.parser_version,
            spec.repository_locator,
            spec.commit_sha,
            spec.blob_sha,
            spec.path,
            spec.snapshot_policy,
        )
        actual = (
            existing.version_kind,
            existing.content_sha256,
            existing.canonical_locator,
            existing.media_type,
            existing.byte_count,
            existing.parser_name,
            existing.parser_version,
            existing.repository_locator,
            existing.commit_sha,
            existing.blob_sha,
            existing.path,
            existing.metadata.get("snapshot_policy"),
        )
        if actual != expected:
            raise SourceVersionConflict(
                "source version key already identifies different immutable metadata"
            )
        if spec.snapshot_bytes is None:
            if existing.content_object_id is not None:
                raise SourceVersionConflict(
                    "source version snapshot retention cannot change in place"
                )
            return
        if existing.content_object_id is None:
            raise SourceVersionConflict(
                "source version snapshot retention cannot change in place"
            )
        content_object = repository.get_content_object(existing.content_object_id)
        self._validate_content_reuse(
            content_object,
            StagedBlob(
                token="0" * 32,
                sha256=spec.content_sha256,
                storage_key=content_object.storage_key,
                byte_count=spec.byte_count or 0,
                media_type=spec.media_type,
            ),
        )
        try:
            self.blob_store.read(
                self._blob_reference(content_object),
                verify=True,
            )
        except (FileNotFoundError, BlobIntegrityError) as exc:
            raise ContentObjectMissing(
                "referenced content object is unavailable"
            ) from exc

    @staticmethod
    def _validate_content_reuse(
        content_object: ContentObjectRecord,
        staged: StagedBlob,
    ) -> None:
        if (
            content_object.storage_backend != "filesystem"
            or content_object.sha256 != staged.sha256
            or content_object.storage_key != staged.storage_key
            or content_object.byte_count != staged.byte_count
            or content_object.media_type != staged.media_type
            or content_object.compression != "none"
        ):
            raise SourceVersionConflict(
                "content hash already identifies different immutable metadata"
            )

    @staticmethod
    def _new_source_version(
        spec: SourceVersionSpec,
        content_object: ContentObjectRecord | None,
    ) -> SourceVersionRecord:
        assert spec.version_key is not None
        metadata = {
            **dict(spec.metadata),
            "snapshot_policy": spec.snapshot_policy,
        }
        return SourceVersionRecord(
            id=f"srcv_{uuid4()}",
            source_id=spec.source_id,
            version_key=spec.version_key,
            version_kind=spec.version_kind,
            retrieved_at=str(spec.retrieved_at),
            published_at=(
                str(spec.published_at) if spec.published_at is not None else None
            ),
            content_sha256=spec.content_sha256,
            canonical_locator=spec.canonical_locator,
            metadata=metadata,
            created_at=_utc_now_text(),
            content_object_id=(
                content_object.id if content_object is not None else None
            ),
            media_type=spec.media_type,
            byte_count=spec.byte_count,
            parser_name=spec.parser_name,
            parser_version=spec.parser_version,
            repository_locator=spec.repository_locator,
            commit_sha=spec.commit_sha,
            blob_sha=spec.blob_sha,
            path=spec.path,
        )

    @staticmethod
    def _blob_reference(record: ContentObjectRecord) -> BlobReference:
        return BlobReference(
            sha256=record.sha256,
            storage_key=record.storage_key,
            byte_count=record.byte_count,
            media_type=record.media_type,
        )


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
