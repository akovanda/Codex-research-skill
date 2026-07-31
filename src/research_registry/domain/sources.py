from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
from pathlib import PurePosixPath
import re
from typing import Any, Literal, Mapping

from ..contracts.common import SnapshotPolicy
from ..ingestion.blobs import (
    BlobValidationError,
    validate_media_type,
    validate_sha256,
)


VersionKind = Literal[
    "web",
    "doi",
    "file",
    "git_blob",
    "pdf",
    "api",
    "note",
    "migration",
]
_VERSION_KINDS = {
    "web",
    "doi",
    "file",
    "git_blob",
    "pdf",
    "api",
    "note",
    "migration",
}
_POLICY_ORDER: dict[str, int] = {
    "metadata_only": 0,
    "evidence_only": 1,
    "extracted_text": 2,
    "full_content": 3,
}
_GIT_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class SourceVersionError(ValueError):
    """Base error for immutable source-version inputs."""


class SnapshotPolicyDenied(SourceVersionError):
    """Raised when source bytes violate configured retention policy."""


@dataclass(frozen=True)
class SourceVersionSpec:
    source_id: str
    version_key: str | None
    version_kind: VersionKind
    retrieved_at: str | datetime
    content_sha256: str
    canonical_locator: str
    snapshot_policy: SnapshotPolicy
    snapshot_bytes: bytes | None = field(default=None, repr=False)
    published_at: str | datetime | None = None
    media_type: str | None = None
    byte_count: int | None = None
    parser_name: str | None = None
    parser_version: str | None = None
    repository_locator: str | None = None
    commit_sha: str | None = None
    blob_sha: str | None = None
    path: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


def validate_source_version_spec(
    spec: SourceVersionSpec,
    *,
    max_snapshot_policy: SnapshotPolicy,
) -> SourceVersionSpec:
    if not isinstance(spec, SourceVersionSpec):
        raise SourceVersionError("source version input is invalid")
    if (
        not isinstance(spec.source_id, str)
        or not spec.source_id.strip()
        or len(spec.source_id) > 200
    ):
        raise SourceVersionError("source ID must be a bounded non-empty string")
    if spec.version_kind not in _VERSION_KINDS:
        raise SourceVersionError("source version kind is invalid")
    try:
        content_sha256 = validate_sha256(spec.content_sha256)
        media_type = validate_media_type(spec.media_type)
    except BlobValidationError as exc:
        raise SourceVersionError(str(exc)) from exc
    if spec.snapshot_policy not in _POLICY_ORDER:
        raise SnapshotPolicyDenied("snapshot policy is invalid")
    if max_snapshot_policy not in _POLICY_ORDER:
        raise SnapshotPolicyDenied("machine snapshot policy is invalid")
    if _POLICY_ORDER[spec.snapshot_policy] > _POLICY_ORDER[max_snapshot_policy]:
        raise SnapshotPolicyDenied(
            "requested snapshot retention exceeds machine policy"
        )

    if spec.snapshot_policy in {"metadata_only", "evidence_only"}:
        if spec.snapshot_bytes is not None:
            raise SnapshotPolicyDenied(
                f"{spec.snapshot_policy} must not include source snapshot bytes"
            )
    elif spec.snapshot_bytes is None:
        raise SnapshotPolicyDenied(
            f"{spec.snapshot_policy} requires snapshot bytes"
        )
    elif not isinstance(spec.snapshot_bytes, bytes):
        raise SourceVersionError("snapshot content must be bytes")

    byte_count = spec.byte_count
    if (
        byte_count is not None
        and (
            not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or byte_count < 0
        )
    ):
        raise SourceVersionError("source version byte count must be non-negative")
    if spec.snapshot_bytes is not None:
        actual_count = len(spec.snapshot_bytes)
        if actual_count > 50_000_000:
            raise SnapshotPolicyDenied("snapshot bytes exceed the retention size limit")
        if byte_count is None:
            byte_count = actual_count
        elif byte_count != actual_count:
            raise SourceVersionError("source version byte count does not match bytes")

    version_key = spec.version_key or f"{spec.version_kind}:{content_sha256}"
    if (
        not isinstance(version_key, str)
        or not version_key
        or len(version_key) > 500
    ):
        raise SourceVersionError("source version key must be a bounded string")
    if (
        not isinstance(spec.canonical_locator, str)
        or not spec.canonical_locator
        or len(spec.canonical_locator) > 8192
    ):
        raise SourceVersionError("canonical locator must be a bounded string")

    retrieved_at = _normalize_utc_timestamp(spec.retrieved_at, "retrieved_at")
    published_at = (
        _normalize_utc_timestamp(spec.published_at, "published_at")
        if spec.published_at is not None
        else None
    )
    parser_name = _bounded_optional(spec.parser_name, "parser name", 200)
    parser_version = _bounded_optional(
        spec.parser_version, "parser version", 100
    )
    repository_locator = _bounded_optional(
        spec.repository_locator, "repository locator", 8192
    )
    path = spec.path
    commit_sha = spec.commit_sha
    blob_sha = spec.blob_sha
    if spec.version_kind == "git_blob":
        if (
            commit_sha is None
            or _GIT_OBJECT_ID.fullmatch(commit_sha) is None
            or blob_sha is None
            or _GIT_OBJECT_ID.fullmatch(blob_sha) is None
            or path is None
        ):
            raise SourceVersionError(
                "git_blob source versions require full commit, blob, and path provenance"
            )
        _validate_posix_path(path)
    else:
        if commit_sha is not None and _GIT_OBJECT_ID.fullmatch(commit_sha) is None:
            raise SourceVersionError("commit SHA is invalid")
        if blob_sha is not None and _GIT_OBJECT_ID.fullmatch(blob_sha) is None:
            raise SourceVersionError("blob SHA is invalid")
        if path is not None:
            _validate_posix_path(path)

    metadata = dict(spec.metadata)
    try:
        json.dumps(metadata, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SourceVersionError("source version metadata must be JSON serializable") from exc
    if len(metadata) > 100:
        raise SourceVersionError("source version metadata is too large")

    return replace(
        spec,
        source_id=spec.source_id.strip(),
        version_key=version_key,
        retrieved_at=retrieved_at,
        published_at=published_at,
        content_sha256=content_sha256,
        media_type=media_type,
        byte_count=byte_count,
        parser_name=parser_name,
        parser_version=parser_version,
        repository_locator=repository_locator,
        metadata=metadata,
    )


def _normalize_utc_timestamp(value: str | datetime, field_name: str) -> str:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SourceVersionError(f"{field_name} must be an ISO timestamp") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise SourceVersionError(f"{field_name} must be an ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceVersionError(f"{field_name} must include a UTC offset")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _bounded_optional(
    value: str | None,
    field_name: str,
    maximum: int,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise SourceVersionError(f"{field_name} is invalid")
    return value


def _validate_posix_path(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or value.startswith("/")
        or "\\" in value
        or ".." in PurePosixPath(value).parts
        or str(PurePosixPath(value)) != value
    ):
        raise SourceVersionError("source path must be a normalized relative POSIX path")
