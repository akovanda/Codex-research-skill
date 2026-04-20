from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from html import unescape
import os
import re
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from .models import SourceCreate


HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
META_RE = re.compile(
    r"""<meta[^>]+(?:name|property)=["'](?P<key>[^"']+)["'][^>]+content=["'](?P<value>[^"']+)["'][^>]*>""",
    re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)?", re.IGNORECASE)


class ImportedSourceCandidate(BaseModel):
    source: SourceCreate
    excerpt_text: str | None = None
    warnings: list[str] = Field(default_factory=list)


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def fetch_url_candidate(url: str) -> ImportedSourceCandidate:
    with httpx.Client(follow_redirects=True, timeout=20.0, headers={"user-agent": "ResearchRegistry/0.1.0"}) as client:
        response = client.get(url)
    response.raise_for_status()

    text = response.text
    final_url = str(response.url)
    host = response.url.host or urlparse(final_url).netloc or "web"
    title = _extract_html_title(text) or response.url.path.strip("/").split("/")[-1] or final_url
    snippet = _extract_meta(text, "description") or _extract_meta(text, "og:description") or _extract_first_paragraph(text)
    author = _extract_meta(text, "author")
    published_at = _parse_datetime(_extract_meta(text, "article:published_time"))
    source_type = _infer_source_type(final_url, response.headers.get("content-type", ""))
    now = utc_now()

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
            content_sha256=sha256(response.content).hexdigest(),
            snapshot_required=True,
            snapshot_present=False,
            last_verified_at=now,
            refresh_due_at=now + timedelta(days=30),
            review_state="unreviewed",
            trust_tier="low",
        ),
        excerpt_text=_clean_text(snippet)[:600] if snippet else None,
    )


def fetch_doi_candidate(doi: str) -> ImportedSourceCandidate:
    normalized = normalize_doi(doi)
    warnings: list[str] = []
    with httpx.Client(timeout=20.0, headers={"user-agent": "ResearchRegistry/0.1.0"}) as client:
        response = client.get(f"https://api.crossref.org/works/{normalized}")
    response.raise_for_status()
    message = response.json()["message"]

    openalex = _fetch_openalex_record(normalized)
    if openalex is None:
        warnings.append("OpenAlex enrichment unavailable for this DOI.")

    title = _first_non_empty(message.get("title", [])) or normalized
    container = _first_non_empty(message.get("container-title", []))
    abstract = _strip_jats(message.get("abstract")) or _openalex_abstract(openalex)
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
            content_sha256=sha256(locator.encode("utf-8")).hexdigest(),
            snapshot_required=True,
            snapshot_present=False,
            last_verified_at=now,
            refresh_due_at=now + timedelta(days=90),
            review_state="unreviewed",
            trust_tier="medium",
        ),
        excerpt_text=_clean_text(abstract)[:600] if abstract else None,
        warnings=warnings,
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
            content_sha256=sha256(locator.encode("utf-8")).hexdigest(),
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
    cleaned = DOI_PREFIX_RE.sub("", doi.strip())
    return cleaned.strip()


def _fetch_openalex_record(doi: str) -> dict | None:
    api_key = os.getenv("RESEARCH_REGISTRY_OPENALEX_API_KEY", "").strip()
    params = {"api_key": api_key} if api_key else None
    try:
        with httpx.Client(timeout=10.0, headers={"user-agent": "ResearchRegistry/0.1.0"}) as client:
            response = client.get(f"https://api.openalex.org/works/doi:{doi}", params=params)
        if response.status_code != 200:
            return None
        return response.json()
    except httpx.HTTPError:
        return None


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


def _openalex_abstract(openalex: dict | None) -> str | None:
    if not openalex:
        return None
    inverted = openalex.get("abstract_inverted_index")
    if not inverted:
        return None
    max_position = max((position for positions in inverted.values() for position in positions), default=-1)
    if max_position < 0:
        return None
    ordered = [""] * (max_position + 1)
    for token, positions in inverted.items():
        for position in positions:
            if 0 <= position < len(ordered):
                ordered[position] = token
    return _clean_text(" ".join(token for token in ordered if token))


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
