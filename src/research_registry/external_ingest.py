from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from html import unescape
import json
import re
from urllib.parse import quote

from pydantic import BaseModel, Field

from .contracts.common import SnapshotPolicy
from .domain.sources import SourceVersionSpec
from .ingestion.fetch_policy import FetchPolicy
from .ingestion.web import (
    HardenedWebFetcher,
    MediaTypeDenied,
    ParserFailed,
    extract_text,
    normalize_doi as normalize_captured_doi,
    parse_json_document,
)
from .models import SourceCreate


HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
META_RE = re.compile(
    r"""<meta[^>]+(?:name|property)=["'](?P<key>[^"']+)["'][^>]+content=["'](?P<value>[^"']+)["'][^>]*>""",
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)?", re.IGNORECASE)


class CapturedVersionCandidate(BaseModel):
    version_kind: str
    version_key: str
    content_sha256: str
    canonical_locator: str
    snapshot_policy: SnapshotPolicy
    snapshot_bytes: bytes | None = None
    media_type: str | None = None
    byte_count: int | None = None
    parser_name: str | None = None
    parser_version: str | None = None
    metadata: dict = Field(default_factory=dict)

    def source_version_spec(self, source_id: str) -> SourceVersionSpec:
        return SourceVersionSpec(
            source_id=source_id,
            version_key=self.version_key,
            version_kind=self.version_kind,  # type: ignore[arg-type]
            retrieved_at=utc_now(),
            content_sha256=self.content_sha256,
            canonical_locator=self.canonical_locator,
            snapshot_policy=self.snapshot_policy,
            snapshot_bytes=self.snapshot_bytes,
            media_type=self.media_type,
            byte_count=self.byte_count,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            metadata=self.metadata,
        )


class ImportedSourceCandidate(BaseModel):
    source: SourceCreate
    excerpt_text: str | None = None
    warnings: list[str] = Field(default_factory=list)
    version: CapturedVersionCandidate | None = None


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def fetch_url_candidate(url: str) -> ImportedSourceCandidate:
    fetcher = HardenedWebFetcher(FetchPolicy())
    response = fetcher.fetch(url)
    text = extract_text(
        response.body,
        response.media_type,
        response.charset,
        maximum_bytes=fetcher.policy.max_extracted_text_bytes,
    )
    decoded_source = _decode_source_text(
        response.body,
        response.charset,
    )
    final_url = response.final_url
    host = final_url.split("/", 3)[2]
    path = final_url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    title = _extract_html_title(decoded_source) or path or final_url
    snippet = _extract_meta(text, "description") or _extract_meta(text, "og:description") or _extract_first_paragraph(text)
    if response.media_type in {"text/html", "application/xhtml+xml"}:
        snippet = (
            _extract_meta(decoded_source, "description")
            or _extract_meta(decoded_source, "og:description")
            or _extract_first_paragraph(decoded_source)
            or text[:600]
        )
    author = _extract_meta(decoded_source, "author")
    published_at = _parse_datetime(
        _extract_meta(decoded_source, "article:published_time")
    )
    source_type = _infer_source_type(final_url, response.media_type)
    now = utc_now()
    snapshot = text.encode("utf-8")
    content_hash = sha256(snapshot).hexdigest()

    return ImportedSourceCandidate(
        source=SourceCreate(
            locator=final_url,
            title=_clean_text(title)[:300],
            source_type=source_type,
            site_name=host,
            published_at=published_at,
            accessed_at=now,
            author=_clean_text(author)[:200] if author else None,
            snippet=_clean_text(snippet)[:600] if snippet else None,
            content_sha256=content_hash,
            snapshot_required=True,
            snapshot_present=True,
            last_verified_at=now,
            refresh_due_at=now + timedelta(days=30),
            review_state="unreviewed",
            trust_tier="low",
        ),
        excerpt_text=_clean_text(snippet)[:600] if snippet else None,
        version=CapturedVersionCandidate(
            version_kind="web",
            version_key=f"web:{content_hash}",
            content_sha256=content_hash,
            canonical_locator=final_url,
            snapshot_policy="extracted_text",
            snapshot_bytes=snapshot,
            media_type="text/plain",
            byte_count=len(snapshot),
            parser_name=(
                "research-registry-html"
                if response.media_type in {"text/html", "application/xhtml+xml"}
                else "research-registry-text"
            ),
            parser_version="2",
            metadata={
                "requested_url": response.requested_url,
                "redirect_chain": list(response.redirect_chain),
                "http_status": response.status,
                "response_headers": response.headers,
                "wire_sha256": response.content_sha256,
                "wire_byte_count": len(response.body),
                "untrusted_content": True,
            },
        ),
    )


def fetch_doi_candidate(doi: str) -> ImportedSourceCandidate:
    normalized = normalize_doi(doi)
    fetcher = HardenedWebFetcher(FetchPolicy())
    response = fetcher.fetch(
        f"https://api.crossref.org/works/{quote(normalized, safe='/')}"
    )
    if response.media_type != "application/json":
        raise MediaTypeDenied(
            "MEDIA_TYPE_DENIED: DOI metadata provider did not return JSON."
        )
    document = parse_json_document(
        response.body,
        maximum_depth=fetcher.policy.max_json_depth,
        maximum_nodes=fetcher.policy.max_json_nodes,
    )
    try:
        message = document["message"] if isinstance(document, dict) else None
        if not isinstance(message, dict):
            raise TypeError("Crossref message is not an object")
        canonical_metadata = json.dumps(
            message,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (KeyError, TypeError, ValueError) as exc:
        raise ParserFailed(
            "PARSER_FAILED: DOI metadata response is invalid."
        ) from exc
    metadata_hash = sha256(canonical_metadata).hexdigest()

    title = _first_non_empty(message.get("title", [])) or normalized
    container = _first_non_empty(message.get("container-title", []))
    abstract = _strip_jats(message.get("abstract"))
    author = _format_crossref_authors(message.get("author", []))
    published_at = _crossref_date(message)
    locator = f"https://doi.org/{normalized}"
    snippet = abstract or container or title
    now = utc_now()

    return ImportedSourceCandidate(
        source=SourceCreate(
            locator=locator,
            title=_clean_text(title)[:300],
            source_type="paper",
            site_name=_clean_text(container)[:200] if container else "Crossref DOI",
            published_at=published_at,
            accessed_at=now,
            author=author,
            snippet=_clean_text(snippet)[:600] if snippet else None,
            content_sha256=metadata_hash,
            snapshot_required=True,
            snapshot_present=True,
            last_verified_at=now,
            refresh_due_at=now + timedelta(days=90),
            review_state="unreviewed",
            trust_tier="medium",
        ),
        excerpt_text=_clean_text(abstract)[:600] if abstract else None,
        version=CapturedVersionCandidate(
            version_kind="doi",
            version_key=f"doi:crossref:{metadata_hash}",
            content_sha256=metadata_hash,
            canonical_locator=locator,
            snapshot_policy="extracted_text",
            snapshot_bytes=canonical_metadata,
            media_type="application/json",
            byte_count=len(canonical_metadata),
            parser_name="research-registry-crossref",
            parser_version="2",
            metadata={
                "doi": normalized,
                "metadata_provider": "crossref",
                "hash_semantics": "crossref_message_sha256",
                "provider_response_sha256": response.content_sha256,
                "untrusted_content": True,
            },
        ),
    )


def bibtex_candidates(bibtex: str) -> list[ImportedSourceCandidate]:
    return [candidate_from_bibtex_entry(entry) for entry in parse_bibtex_entries(bibtex)]


def candidate_from_bibtex_entry(entry: dict[str, str]) -> ImportedSourceCandidate:
    locator = _bibtex_locator(entry)
    title = entry.get("title") or locator or "Untitled BibTeX entry"
    snippet = entry.get("abstract") or entry.get("journal") or entry.get("booktitle") or title
    author = entry.get("author")
    published_at = _parse_bibtex_date(entry)
    source_type = "paper" if entry.get("journal") or entry.get("booktitle") or entry.get("doi") else "article"
    now = utc_now()
    return ImportedSourceCandidate(
        source=SourceCreate(
            locator=locator,
            title=_clean_text(title)[:300],
            source_type=source_type,
            site_name=_clean_text(entry.get("journal") or entry.get("booktitle") or "BibTeX import")[:200],
            published_at=published_at,
            accessed_at=now,
            author=_clean_text(author)[:200] if author else None,
            snippet=_clean_text(snippet)[:600] if snippet else None,
            # BibTeX is reference metadata, not a captured article body.
            # Leave the content hash unknown rather than hash the DOI/URL.
            content_sha256=None,
            snapshot_required=bool(entry.get("url") or entry.get("doi")),
            snapshot_present=False,
            last_verified_at=now,
            refresh_due_at=now + timedelta(days=90),
            review_state="unreviewed",
            trust_tier="medium" if entry.get("doi") else "low",
        ),
        excerpt_text=_clean_text(entry.get("abstract") or "")[:600] or None,
    )


def parse_bibtex_entries(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    index = 0
    while True:
        start = text.find("@", index)
        if start == -1:
            break
        brace = text.find("{", start)
        if brace == -1:
            break
        depth = 1
        cursor = brace + 1
        while cursor < len(text) and depth > 0:
            char = text[cursor]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            cursor += 1
        body = text[brace + 1 : cursor - 1]
        comma = body.find(",")
        if comma == -1:
            index = cursor
            continue
        fields = _parse_bibtex_fields(body[comma + 1 :])
        if fields:
            entries.append(fields)
        index = cursor
    return entries


def normalize_doi(doi: str) -> str:
    return normalize_captured_doi(DOI_PREFIX_RE.sub("", doi.strip()))


def _extract_html_title(text: str) -> str | None:
    match = HTML_TITLE_RE.search(text)
    if not match:
        return None
    return _clean_text(match.group(1))


def _extract_meta(text: str, key: str) -> str | None:
    lowered = key.lower()
    for match in META_RE.finditer(text):
        if match.group("key").lower() == lowered:
            return _clean_text(match.group("value"))
    return None


def _extract_first_paragraph(text: str) -> str | None:
    match = re.search(r"<p[^>]*>(.*?)</p>", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return _clean_text(match.group(1))


def _clean_text(value: str) -> str:
    stripped = HTML_TAG_RE.sub(" ", unescape(value or ""))
    return WHITESPACE_RE.sub(" ", stripped).strip()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _infer_source_type(url: str, content_type: str) -> str:
    lowered = f"{url} {content_type}".lower()
    if "arxiv.org" in lowered or "doi.org" in lowered:
        return "paper"
    if "docs" in lowered or "documentation" in lowered:
        return "official-docs"
    if "pdf" in lowered:
        return "report"
    return "webpage"


def _first_non_empty(values: list[str] | None) -> str | None:
    if not values:
        return None
    for value in values:
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned
    return None


def _strip_jats(value: str | None) -> str | None:
    if not value:
        return None
    return _clean_text(value)


def _format_crossref_authors(authors: list[dict]) -> str | None:
    names: list[str] = []
    for author in authors[:4]:
        given = _clean_text(author.get("given", ""))
        family = _clean_text(author.get("family", ""))
        full = " ".join(part for part in [given, family] if part).strip()
        if full:
            names.append(full)
    return ", ".join(names) or None


def _crossref_date(message: dict) -> datetime | None:
    for key in ("published-print", "published-online", "created", "issued"):
        parts = message.get(key, {}).get("date-parts", [])
        if not parts or not parts[0]:
            continue
        raw = parts[0]
        year = raw[0]
        month = raw[1] if len(raw) > 1 else 1
        day = raw[2] if len(raw) > 2 else 1
        try:
            return datetime(year, month, day, tzinfo=UTC)
        except ValueError:
            continue
    return None


def _decode_source_text(body: bytes, charset: str | None) -> str:
    encoding = charset or "utf-8"
    try:
        return body.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


def _bibtex_locator(entry: dict[str, str]) -> str:
    doi = entry.get("doi")
    if doi:
        return f"https://doi.org/{normalize_doi(doi)}"
    url = entry.get("url")
    if url:
        return url.strip()
    title = entry.get("title", "bibtex-entry")
    return f"bibtex:{sha256(title.encode('utf-8')).hexdigest()[:16]}"


def _parse_bibtex_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    index = 0
    while index < len(body):
        while index < len(body) and body[index] in " \t\r\n,":
            index += 1
        if index >= len(body):
            break
        key_start = index
        while index < len(body) and re.match(r"[A-Za-z0-9_-]", body[index]):
            index += 1
        key = body[key_start:index].strip().lower()
        while index < len(body) and body[index] in " \t\r\n=":
            index += 1
        if not key or index >= len(body):
            break
        value, index = _parse_bibtex_value(body, index)
        if value:
            fields[key] = _clean_text(value)
    return fields


def _parse_bibtex_value(body: str, index: int) -> tuple[str, int]:
    if body[index] == "{":
        depth = 1
        cursor = index + 1
        while cursor < len(body) and depth > 0:
            if body[cursor] == "{":
                depth += 1
            elif body[cursor] == "}":
                depth -= 1
            cursor += 1
        return body[index + 1 : cursor - 1], cursor
    if body[index] == '"':
        cursor = index + 1
        while cursor < len(body):
            if body[cursor] == '"' and body[cursor - 1] != "\\":
                break
            cursor += 1
        return body[index + 1 : cursor], cursor + 1
    cursor = index
    while cursor < len(body) and body[cursor] not in ",\n":
        cursor += 1
    return body[index:cursor], cursor


def _parse_bibtex_date(entry: dict[str, str]) -> datetime | None:
    year = entry.get("year")
    if not year or not year.isdigit():
        return None
    month_value = entry.get("month")
    month = 1
    if month_value:
        try:
            month = int(re.sub(r"[^0-9]", "", month_value) or "1")
        except ValueError:
            month = 1
    try:
        return datetime(int(year), max(1, min(month, 12)), 1, tzinfo=UTC)
    except ValueError:
        return None
