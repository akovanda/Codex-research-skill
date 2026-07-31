from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from pydantic import TypeAdapter, ValidationError

from ..contracts.v2 import SourceSelectorV2
from ..ingestion.blobs import BlobValidationError, validate_sha256


_SELECTOR_ADAPTER = TypeAdapter(SourceSelectorV2)
_MISSING = object()


class EvidenceResolutionError(ValueError):
    """Base error for exact evidence resolution."""


class InvalidSelector(EvidenceResolutionError):
    """Raised when a selector is malformed or contains unknown fields."""


class EvidenceHashMismatch(EvidenceResolutionError):
    """Raised when quote or selector hashes do not match."""


class EvidenceUnresolved(EvidenceResolutionError):
    """Raised when exact evidence does not resolve."""


class EvidenceAmbiguous(EvidenceResolutionError):
    """Raised when exact evidence has more than one valid match."""


@dataclass(frozen=True)
class EvidenceDocument:
    text: str | None = None
    pages: tuple[str, ...] | None = None
    json_value: Any = field(default=_MISSING, repr=False)
    dom_text: Mapping[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class SourceVersionProvenance:
    path: str | None = None
    commit_sha: str | None = None
    blob_sha: str | None = None


@dataclass(frozen=True)
class EvidenceResolution:
    selector_type: str
    start: int | None = None
    end: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    start_page: int | None = None
    end_page: int | None = None
    pointer: str | None = None


def validate_selector(selector: Any) -> dict[str, Any]:
    try:
        validated = _SELECTOR_ADAPTER.validate_python(selector)
    except ValidationError as exc:
        raise InvalidSelector("evidence selector is invalid or contains unknown fields") from exc
    return validated.model_dump(mode="python", exclude_none=True)


def resolve_exact_evidence(
    document: EvidenceDocument | str | bytes | Mapping[str, Any] | list[Any],
    selector: Any,
    quote_text: str,
    *,
    quote_sha256: str | None = None,
    provenance: SourceVersionProvenance | None = None,
) -> EvidenceResolution:
    if (
        not isinstance(quote_text, str)
        or not quote_text
        or len(quote_text) > 20_000
    ):
        raise EvidenceHashMismatch("evidence quote is invalid")
    if quote_sha256 is not None:
        try:
            validate_sha256(quote_sha256)
        except BlobValidationError as exc:
            raise EvidenceHashMismatch("evidence quote SHA-256 is invalid") from exc
        if sha256(quote_text.encode("utf-8")).hexdigest() != quote_sha256:
            raise EvidenceHashMismatch("evidence quote SHA-256 does not match")

    closed = validate_selector(selector)
    selector_type = closed["type"]
    exact = closed.get("exact")
    if exact is not None and exact != quote_text:
        raise EvidenceHashMismatch("selector exact text does not match evidence quote")
    evidence_document = _coerce_document(document)

    if selector_type == "json_pointer":
        return _resolve_json_pointer(evidence_document, closed, quote_text)
    if selector_type == "page_range":
        return _resolve_page_range(evidence_document, closed, quote_text)
    if selector_type == "dom_text":
        return _resolve_dom_text(evidence_document, closed, quote_text)

    text = _require_text(evidence_document)
    if selector_type == "char_range":
        start = closed["start"]
        end = closed["end"]
        if text[start:end] != quote_text:
            raise EvidenceUnresolved("character range does not resolve exact evidence")
        return EvidenceResolution(
            selector_type=selector_type,
            start=start,
            end=end,
        )
    if selector_type in {"line_range", "git_line_range"}:
        if selector_type == "git_line_range":
            _validate_git_provenance(closed, provenance)
        start_line = closed["start_line"]
        end_line = closed["end_line"]
        lower, upper = _line_bounds(text, start_line, end_line)
        start, end = _unique_text_match(
            text,
            quote_text,
            lower=lower,
            upper=upper,
            prefix=closed.get("prefix"),
            suffix=closed.get("suffix"),
        )
        return EvidenceResolution(
            selector_type=selector_type,
            start=start,
            end=end,
            start_line=start_line,
            end_line=end_line,
        )
    if selector_type == "text_quote":
        start_offset = closed.get("start")
        end_offset = closed.get("end")
        if (start_offset is None) != (end_offset is None):
            raise InvalidSelector(
                "text quote start and end offsets must be supplied together"
            )
        start, end = _unique_text_match(
            text,
            quote_text,
            lower=0,
            upper=len(text),
            prefix=closed.get("prefix"),
            suffix=closed.get("suffix"),
            expected=(
                (start_offset, end_offset)
                if start_offset is not None and end_offset is not None
                else None
            ),
        )
        return EvidenceResolution(
            selector_type=selector_type,
            start=start,
            end=end,
        )
    raise InvalidSelector("evidence selector type is unsupported")


def _coerce_document(
    document: EvidenceDocument | str | bytes | Mapping[str, Any] | list[Any],
) -> EvidenceDocument:
    if isinstance(document, EvidenceDocument):
        return document
    if isinstance(document, bytes):
        try:
            return EvidenceDocument(text=document.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise EvidenceUnresolved(
                "source version is not valid UTF-8 extracted text"
            ) from exc
    if isinstance(document, str):
        return EvidenceDocument(text=document)
    if isinstance(document, (dict, list)):
        return EvidenceDocument(json_value=document)
    raise EvidenceUnresolved("source version content is unavailable")


def _require_text(document: EvidenceDocument) -> str:
    if document.text is None:
        raise EvidenceUnresolved("source version extracted text is unavailable")
    return document.text


def _unique_text_match(
    text: str,
    exact: str,
    *,
    lower: int,
    upper: int,
    prefix: str | None = None,
    suffix: str | None = None,
    expected: tuple[int, int] | None = None,
) -> tuple[int, int]:
    if expected is not None:
        start, end = expected
        if (
            start < lower
            or end > upper
            or start > end
            or text[start:end] != exact
            or not _context_matches(text, start, end, prefix, suffix)
        ):
            raise EvidenceUnresolved("recorded offsets do not resolve exact evidence")
        return start, end

    matches: list[tuple[int, int]] = []
    position = lower
    while position <= upper - len(exact):
        start = text.find(exact, position, upper)
        if start < 0:
            break
        end = start + len(exact)
        if _context_matches(text, start, end, prefix, suffix):
            matches.append((start, end))
            if len(matches) > 1:
                raise EvidenceAmbiguous("exact evidence resolves more than once")
        position = start + 1
    if not matches:
        raise EvidenceUnresolved("exact evidence does not resolve")
    return matches[0]


def _context_matches(
    text: str,
    start: int,
    end: int,
    prefix: str | None,
    suffix: str | None,
) -> bool:
    if prefix is not None and not text[:start].endswith(prefix):
        return False
    if suffix is not None and not text[end:].startswith(suffix):
        return False
    return True


def _line_bounds(text: str, start_line: int, end_line: int) -> tuple[int, int]:
    lines = text.splitlines(keepends=True)
    if start_line > len(lines) or end_line > len(lines):
        raise EvidenceUnresolved("line range is outside source version content")
    lower = sum(len(line) for line in lines[: start_line - 1])
    upper = sum(len(line) for line in lines[:end_line])
    return lower, upper


def _validate_git_provenance(
    selector: dict[str, Any],
    provenance: SourceVersionProvenance | None,
) -> None:
    if provenance is None or (
        selector["path"],
        selector["commit_sha"],
        selector["blob_sha"],
    ) != (
        provenance.path,
        provenance.commit_sha,
        provenance.blob_sha,
    ):
        raise EvidenceUnresolved(
            "Git selector provenance does not match the source version"
        )


def _resolve_page_range(
    document: EvidenceDocument,
    selector: dict[str, Any],
    quote_text: str,
) -> EvidenceResolution:
    if document.pages is None:
        raise EvidenceUnresolved("page-indexed source version content is unavailable")
    start_page = selector["start_page"]
    end_page = selector["end_page"]
    if end_page > len(document.pages):
        raise EvidenceUnresolved("page range is outside source version content")
    selected = "\n".join(document.pages[start_page - 1 : end_page])
    expected = None
    start_offset = selector.get("start")
    end_offset = selector.get("end")
    if (start_offset is None) != (end_offset is None):
        raise InvalidSelector(
            "page character start and end offsets must be supplied together"
        )
    if start_offset is not None and end_offset is not None:
        expected = (start_offset, end_offset)
    start, end = _unique_text_match(
        selected,
        quote_text,
        lower=0,
        upper=len(selected),
        expected=expected,
    )
    return EvidenceResolution(
        selector_type="page_range",
        start=start,
        end=end,
        start_page=start_page,
        end_page=end_page,
    )


def _resolve_dom_text(
    document: EvidenceDocument,
    selector: dict[str, Any],
    quote_text: str,
) -> EvidenceResolution:
    css_selector = selector.get("css_selector")
    if css_selector is not None:
        text = document.dom_text.get(css_selector)
        if text is None:
            raise EvidenceUnresolved("DOM selector target is unavailable")
    else:
        text = _require_text(document)
    start, end = _unique_text_match(
        text,
        quote_text,
        lower=0,
        upper=len(text),
        prefix=selector.get("prefix"),
        suffix=selector.get("suffix"),
    )
    return EvidenceResolution(
        selector_type="dom_text",
        start=start,
        end=end,
    )


def _resolve_json_pointer(
    document: EvidenceDocument,
    selector: dict[str, Any],
    quote_text: str,
) -> EvidenceResolution:
    value = document.json_value
    if value is _MISSING:
        text = _require_text(document)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EvidenceUnresolved("source version is not valid JSON") from exc
    pointer = selector["pointer"]
    current = value
    if pointer:
        for encoded_token in pointer[1:].split("/"):
            if re.search(r"~(?:[^01]|$)", encoded_token):
                raise InvalidSelector("JSON Pointer escape is invalid")
            token = encoded_token.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict):
                if token not in current:
                    raise EvidenceUnresolved("JSON Pointer target does not exist")
                current = current[token]
            elif isinstance(current, list):
                if not token.isdigit() or (
                    len(token) > 1 and token.startswith("0")
                ):
                    raise EvidenceUnresolved("JSON Pointer array index is invalid")
                index = int(token)
                if index >= len(current):
                    raise EvidenceUnresolved("JSON Pointer target does not exist")
                current = current[index]
            else:
                raise EvidenceUnresolved("JSON Pointer target does not exist")
    rendered = (
        current
        if isinstance(current, str)
        else json.dumps(
            current,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    if rendered != quote_text:
        raise EvidenceUnresolved("JSON Pointer value does not match exact evidence")
    return EvidenceResolution(
        selector_type="json_pointer",
        pointer=pointer,
    )
