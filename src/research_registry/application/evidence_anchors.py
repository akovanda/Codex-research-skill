from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

from ..domain.evidence import (
    EvidenceAmbiguous,
    EvidenceDocument,
    EvidenceHashMismatch,
    EvidenceResolutionError,
    EvidenceUnresolved,
    InvalidSelector,
    SourceVersionProvenance,
    resolve_exact_evidence,
    validate_selector,
)
from ..ingestion.blobs import BlobReference, BlobStore, BlobStoreError


class EvidenceAnchorRejected(ValueError):
    """The supplied selector or quote contradicts available source content."""


class EvidenceAnchorStorageError(RuntimeError):
    """Retained content could not be read or failed integrity checks."""


@dataclass(frozen=True)
class AnchorContext:
    document: EvidenceDocument | None
    provenance: SourceVersionProvenance
    content_basis: str
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class AnchorValidation:
    anchor_state: str
    last_resolved_at: str | None
    metadata: dict[str, Any]
    warning_key: str | None = None


def build_anchor_context(
    *,
    source_version: Any,
    snapshot_text: str | None,
    snapshot_policy: str | None,
    source_version_repository: Any,
    blob_store: BlobStore,
) -> AnchorContext:
    """Build the strongest safe content view available for one source version."""
    provenance = SourceVersionProvenance(
        path=_value(source_version, "path"),
        commit_sha=_value(source_version, "commit_sha"),
        blob_sha=_value(source_version, "blob_sha"),
    )
    if snapshot_text is not None:
        return AnchorContext(
            document=EvidenceDocument(text=snapshot_text),
            provenance=provenance,
            content_basis=(
                "transient_snapshot"
                if snapshot_policy == "evidence_only"
                else "request_snapshot"
            ),
        )

    content_object_id = _value(source_version, "content_object_id")
    if content_object_id is None:
        return AnchorContext(
            document=None,
            provenance=provenance,
            content_basis="none",
            unavailable_reason="source_content_unavailable",
        )

    try:
        content_object = source_version_repository.get_content_object(
            content_object_id
        )
    except KeyError as exc:
        raise EvidenceAnchorStorageError(
            "SOURCE_CONTENT_INTEGRITY_ERROR: The retained content object is "
            "missing."
        ) from exc
    if content_object.storage_backend != "filesystem":
        raise EvidenceAnchorStorageError(
            "SOURCE_CONTENT_INTEGRITY_ERROR: The retained content backend is "
            "not supported by the configured blob store."
        )
    _validate_content_reference(source_version, content_object)
    reference = BlobReference(
        sha256=content_object.sha256,
        storage_key=content_object.storage_key,
        byte_count=content_object.byte_count,
        media_type=content_object.media_type,
    )
    try:
        content = blob_store.read(reference, verify=True)
    except (FileNotFoundError, BlobStoreError) as exc:
        raise EvidenceAnchorStorageError(
            "SOURCE_CONTENT_INTEGRITY_ERROR: Retained source content could "
            "not be read or verified."
        ) from exc
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return AnchorContext(
            document=None,
            provenance=provenance,
            content_basis="retained_blob",
            unavailable_reason="content_not_utf8",
        )
    return AnchorContext(
        document=EvidenceDocument(text=text),
        provenance=provenance,
        content_basis="retained_blob",
    )


def validate_anchor(
    *,
    client_ref: str,
    selector: Any,
    quote_text: str,
    context: AnchorContext,
    resolved_at: str,
) -> AnchorValidation:
    """Resolve exact evidence when the required content representation exists."""
    del client_ref  # Client identifiers stay out of durable warning summaries.
    closed = validate_selector(selector)
    exact = closed.get("exact")
    if exact is not None and exact != quote_text:
        raise EvidenceAnchorRejected(
            "EVIDENCE_SELECTOR_INVALID: Selector exact text does not match "
            "the evidence quote."
        )

    unavailable_reason = _selector_unavailable_reason(closed, context)
    if unavailable_reason is not None:
        return _unverified(
            selector_type=closed["type"],
            content_basis=context.content_basis,
            reason=unavailable_reason,
        )

    assert context.document is not None
    try:
        resolution = resolve_exact_evidence(
            context.document,
            closed,
            quote_text,
            quote_sha256=sha256(quote_text.encode("utf-8")).hexdigest(),
            provenance=context.provenance,
        )
    except EvidenceAmbiguous as exc:
        raise EvidenceAnchorRejected(
            "EVIDENCE_ANCHOR_AMBIGUOUS: Exact evidence resolves more than once."
        ) from exc
    except (EvidenceHashMismatch, InvalidSelector) as exc:
        raise EvidenceAnchorRejected(
            "EVIDENCE_SELECTOR_INVALID: The evidence selector or quote is "
            "internally inconsistent."
        ) from exc
    except EvidenceUnresolved as exc:
        raise EvidenceAnchorRejected(
            "EVIDENCE_ANCHOR_UNRESOLVED: Exact evidence does not resolve "
            "against available source content."
        ) from exc
    except EvidenceResolutionError as exc:
        raise EvidenceAnchorRejected(
            "EVIDENCE_ANCHOR_INVALID: Exact evidence validation failed."
        ) from exc

    resolution_fields = {
        key: value
        for key, value in asdict(resolution).items()
        if value is not None
    }
    return AnchorValidation(
        anchor_state="resolved",
        last_resolved_at=resolved_at,
        metadata={
            "status": "resolved",
            "content_basis": context.content_basis,
            "resolved_at": resolved_at,
            "resolution": resolution_fields,
        },
    )


def _selector_unavailable_reason(
    selector: dict[str, Any],
    context: AnchorContext,
) -> str | None:
    if context.document is None:
        return context.unavailable_reason or "source_content_unavailable"
    if selector["type"] == "page_range" and context.document.pages is None:
        return "page_index_unavailable"
    if (
        selector["type"] == "dom_text"
        and selector.get("css_selector") is not None
        and not context.document.dom_text
    ):
        return "dom_index_unavailable"
    return None


def _unverified(
    *,
    selector_type: str,
    content_basis: str,
    reason: str,
) -> AnchorValidation:
    return AnchorValidation(
        anchor_state="unverified",
        last_resolved_at=None,
        metadata={
            "status": "unverified",
            "selector_type": selector_type,
            "content_basis": content_basis,
            "reason": reason,
        },
        warning_key=f"EVIDENCE_ANCHOR_UNVERIFIED:{reason}",
    )


def _validate_content_reference(source_version: Any, content_object: Any) -> None:
    expected_sha256 = _value(source_version, "content_sha256")
    expected_byte_count = _value(source_version, "byte_count")
    expected_media_type = _value(source_version, "media_type")
    if content_object.sha256 != expected_sha256:
        raise EvidenceAnchorStorageError(
            "SOURCE_CONTENT_INTEGRITY_ERROR: Source version and retained "
            "content hashes disagree."
        )
    if (
        expected_byte_count is not None
        and content_object.byte_count != expected_byte_count
    ):
        raise EvidenceAnchorStorageError(
            "SOURCE_CONTENT_INTEGRITY_ERROR: Source version and retained "
            "content byte counts disagree."
        )
    if (
        expected_media_type is not None
        and content_object.media_type != expected_media_type
    ):
        raise EvidenceAnchorStorageError(
            "SOURCE_CONTENT_INTEGRITY_ERROR: Source version and retained "
            "content media types disagree."
        )


def _value(record: Any, field: str) -> Any:
    try:
        return record[field]
    except (IndexError, KeyError, TypeError):
        return getattr(record, field)
