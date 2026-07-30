from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
import http.client
import json
import socket
import ssl
from time import monotonic
from typing import Callable, Protocol
from urllib.parse import quote, urljoin

from ..application.source_versions import (
    SourceVersionCreateResult,
    SourceVersionService,
)
from ..contracts.common import SnapshotPolicy
from ..domain.sources import SourceVersionSpec
from .fetch_policy import (
    FetchPolicy,
    Resolver,
    ValidatedTarget,
    default_resolver,
    validate_url,
)


Clock = Callable[[], float]


class FetchError(RuntimeError):
    """A remote capture failed before an immutable version was accepted."""


class FetchTimeout(FetchError):
    pass


class FetchTooLarge(FetchError):
    pass


class MediaTypeDenied(FetchError):
    pass


class RedirectDenied(FetchError):
    pass


class ParserFailed(FetchError):
    pass


class _Response(Protocol):
    status: int

    def getheaders(self) -> list[tuple[str, str]]: ...

    def read(self, amount: int) -> bytes: ...


class _Connection(Protocol):
    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None: ...

    def getresponse(self) -> _Response: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[ValidatedTarget, FetchPolicy], _Connection]


@dataclass(frozen=True)
class FetchedResource:
    requested_url: str
    final_url: str
    redirect_chain: tuple[str, ...]
    status: int
    headers: dict[str, str]
    media_type: str
    charset: str | None
    body: bytes
    content_sha256: str


@dataclass(frozen=True)
class CapturedSource:
    version: SourceVersionCreateResult
    extracted_text: str
    content: bytes


class _BoundHTTPConnection(http.client.HTTPConnection):
    def __init__(self, target: ValidatedTarget, policy: FetchPolicy) -> None:
        super().__init__(
            target.host,
            target.port,
            timeout=policy.connect_timeout_seconds,
        )
        self._validated_address = target.address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._validated_address, self.port),
            self.timeout,
        )


class _BoundHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, target: ValidatedTarget, policy: FetchPolicy) -> None:
        super().__init__(
            target.host,
            target.port,
            timeout=policy.connect_timeout_seconds,
            context=ssl.create_default_context(),
        )
        self._validated_address = target.address

    def connect(self) -> None:
        raw = socket.create_connection(
            (self._validated_address, self.port),
            self.timeout,
        )
        try:
            self.sock = self._context.wrap_socket(
                raw,
                server_hostname=self.host,
            )
        except Exception:
            raw.close()
            raise


def bound_connection_factory(
    target: ValidatedTarget,
    policy: FetchPolicy,
) -> _Connection:
    if target.scheme == "https":
        return _BoundHTTPSConnection(target, policy)
    return _BoundHTTPConnection(target, policy)


class HardenedWebFetcher:
    """Fetch one bounded resource while binding transport to validated DNS."""

    def __init__(
        self,
        policy: FetchPolicy,
        *,
        resolver: Resolver = default_resolver,
        connection_factory: ConnectionFactory = bound_connection_factory,
        clock: Clock = monotonic,
    ) -> None:
        self.policy = policy
        self.resolver = resolver
        self.connection_factory = connection_factory
        self.clock = clock

    def fetch(self, url: str) -> FetchedResource:
        requested_url = url
        current_url = url
        redirect_chain: list[str] = []
        started = self.clock()
        for redirect_index in range(self.policy.max_redirects + 1):
            self._check_deadline(started)
            target = validate_url(
                current_url,
                self.policy,
                resolver=self.resolver,
            )
            connection = self.connection_factory(target, self.policy)
            try:
                connection.request(
                    "GET",
                    target.request_target,
                    headers={
                        "Host": target.host_header,
                        "User-Agent": self.policy.user_agent,
                        "Accept": ", ".join(sorted(self.policy.allowed_media_types)),
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
                headers = self._bounded_headers(response.getheaders())
                if response.status in {301, 302, 303, 307, 308}:
                    location = headers.get("location")
                    if not location:
                        raise RedirectDenied(
                            "URL_REDIRECT_DENIED: Redirect has no Location header."
                        )
                    if redirect_index >= self.policy.max_redirects:
                        raise RedirectDenied(
                            "URL_REDIRECT_DENIED: Redirect limit was exceeded."
                        )
                    next_url = urljoin(target.url, location)
                    # Validate before the next loop so a prohibited target is
                    # rejected before any second transport is constructed.
                    validate_url(next_url, self.policy, resolver=self.resolver)
                    redirect_chain.append(next_url)
                    current_url = next_url
                    continue
                if response.status < 200 or response.status >= 300:
                    raise FetchError(
                        f"FETCH_FAILED: Remote server returned HTTP {response.status}."
                    )
                media_type, charset = _parse_content_type(
                    headers.get("content-type", "")
                )
                if media_type not in self.policy.allowed_media_types:
                    raise MediaTypeDenied(
                        "MEDIA_TYPE_DENIED: Response media type is not allowed."
                    )
                if headers.get("content-encoding", "identity").lower() not in {
                    "",
                    "identity",
                }:
                    raise MediaTypeDenied(
                        "MEDIA_TYPE_DENIED: Encoded response bodies are not accepted."
                    )
                declared = headers.get("content-length")
                if declared is not None:
                    try:
                        declared_bytes = int(declared)
                    except ValueError as exc:
                        raise FetchError(
                            "FETCH_FAILED: Content-Length is invalid."
                        ) from exc
                    if declared_bytes < 0 or declared_bytes > self.policy.max_response_bytes:
                        raise FetchTooLarge(
                            "FETCH_TOO_LARGE: Response exceeds the byte limit."
                        )
                body = self._read_body(connection, response, started)
                return FetchedResource(
                    requested_url=requested_url,
                    final_url=target.url,
                    redirect_chain=tuple(redirect_chain),
                    status=response.status,
                    headers={
                        key: value
                        for key, value in headers.items()
                        if key
                        in {
                            "etag",
                            "last-modified",
                            "content-language",
                            "content-type",
                        }
                    },
                    media_type=media_type,
                    charset=charset,
                    body=body,
                    content_sha256=sha256(body).hexdigest(),
                )
            except (socket.timeout, TimeoutError) as exc:
                raise FetchTimeout(
                    "FETCH_TIMEOUT: Remote source exceeded the time limit."
                ) from exc
            except (OSError, http.client.HTTPException) as exc:
                raise FetchError("FETCH_FAILED: Remote source could not be read.") from exc
            finally:
                connection.close()
        raise RedirectDenied("URL_REDIRECT_DENIED: Redirect limit was exceeded.")

    def _read_body(
        self,
        connection: _Connection,
        response: _Response,
        started: float,
    ) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = self.policy.max_duration_seconds - (self.clock() - started)
            if remaining <= 0:
                raise FetchTimeout(
                    "FETCH_TIMEOUT: Remote source exceeded the time limit."
                )
            sock = getattr(connection, "sock", None)
            if sock is not None:
                sock.settimeout(min(self.policy.read_timeout_seconds, remaining))
            chunk = response.read(min(64 * 1024, self.policy.max_response_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > self.policy.max_response_bytes:
                raise FetchTooLarge(
                    "FETCH_TOO_LARGE: Response exceeds the byte limit."
                )
            chunks.append(chunk)
            self._check_deadline(started)
        return b"".join(chunks)

    def _bounded_headers(self, pairs: list[tuple[str, str]]) -> dict[str, str]:
        total = 0
        headers: dict[str, str] = {}
        for raw_key, raw_value in pairs:
            total += len(raw_key.encode("utf-8")) + len(raw_value.encode("utf-8"))
            if total > self.policy.max_header_bytes:
                raise FetchTooLarge("FETCH_TOO_LARGE: Response headers are too large.")
            key = raw_key.strip().lower()
            value = raw_value.strip()
            if "\r" in key or "\n" in key or "\r" in value or "\n" in value:
                raise FetchError("FETCH_FAILED: Response headers are malformed.")
            if key not in headers:
                headers[key] = value
        return headers

    def _check_deadline(self, started: float) -> None:
        if self.clock() - started > self.policy.max_duration_seconds:
            raise FetchTimeout(
                "FETCH_TIMEOUT: Remote source exceeded the time limit."
            )


class WebSourceIngestor:
    def __init__(
        self,
        fetcher: HardenedWebFetcher,
        versions: SourceVersionService,
    ) -> None:
        self.fetcher = fetcher
        self.versions = versions

    def capture(
        self,
        *,
        source_id: str,
        url: str,
        snapshot_policy: SnapshotPolicy,
    ) -> CapturedSource:
        fetched = self.fetcher.fetch(url)
        extracted = extract_text(
            fetched.body,
            fetched.media_type,
            fetched.charset,
            maximum_bytes=self.fetcher.policy.max_extracted_text_bytes,
        )
        extracted_bytes = extracted.encode("utf-8")
        if snapshot_policy == "full_content":
            captured_bytes = fetched.body
            content_hash = fetched.content_sha256
            media_type = fetched.media_type
            parser_name = "research-registry-wire"
        else:
            captured_bytes = extracted_bytes
            content_hash = sha256(extracted_bytes).hexdigest()
            media_type = "text/plain"
            parser_name = _parser_name(fetched.media_type)
        result = self.versions.create_or_reuse(
            SourceVersionSpec(
                source_id=source_id,
                version_key=f"web:{content_hash}",
                version_kind="web",
                retrieved_at=_utc_now_text(),
                content_sha256=content_hash,
                canonical_locator=fetched.final_url,
                snapshot_policy=snapshot_policy,
                snapshot_bytes=(
                    captured_bytes
                    if snapshot_policy in {"extracted_text", "full_content"}
                    else None
                ),
                media_type=media_type,
                byte_count=len(captured_bytes),
                parser_name=parser_name,
                parser_version="2",
                metadata={
                    "requested_url": fetched.requested_url,
                    "redirect_chain": list(fetched.redirect_chain),
                    "http_status": fetched.status,
                    "response_headers": fetched.headers,
                    "wire_sha256": fetched.content_sha256,
                    "wire_byte_count": len(fetched.body),
                    "untrusted_content": True,
                },
            )
        )
        return CapturedSource(
            version=result,
            extracted_text=extracted,
            content=captured_bytes,
        )


class DoiSourceIngestor:
    def __init__(
        self,
        fetcher: HardenedWebFetcher,
        versions: SourceVersionService,
    ) -> None:
        self.fetcher = fetcher
        self.versions = versions

    def capture(
        self,
        *,
        source_id: str,
        doi: str,
        snapshot_policy: SnapshotPolicy,
    ) -> CapturedSource:
        normalized = normalize_doi(doi)
        fetched = self.fetcher.fetch(
            f"https://api.crossref.org/works/{quote(normalized, safe='/')}"
        )
        if fetched.media_type != "application/json":
            raise MediaTypeDenied(
                "MEDIA_TYPE_DENIED: DOI metadata provider did not return JSON."
            )
        try:
            document = parse_json_document(
                fetched.body,
                maximum_depth=self.fetcher.policy.max_json_depth,
                maximum_nodes=self.fetcher.policy.max_json_nodes,
            )
            message = document["message"]
            canonical = json.dumps(
                message,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ParserFailed("PARSER_FAILED: DOI metadata response is invalid.") from exc
        if len(canonical) > self.fetcher.policy.max_extracted_text_bytes:
            raise FetchTooLarge("FETCH_TOO_LARGE: DOI metadata exceeds the text limit.")
        content_hash = sha256(canonical).hexdigest()
        result = self.versions.create_or_reuse(
            SourceVersionSpec(
                source_id=source_id,
                version_key=f"doi:crossref:{content_hash}",
                version_kind="doi",
                retrieved_at=_utc_now_text(),
                content_sha256=content_hash,
                canonical_locator=f"https://doi.org/{normalized}",
                snapshot_policy=snapshot_policy,
                snapshot_bytes=(
                    canonical
                    if snapshot_policy in {"extracted_text", "full_content"}
                    else None
                ),
                media_type="application/json",
                byte_count=len(canonical),
                parser_name="research-registry-crossref",
                parser_version="2",
                metadata={
                    "doi": normalized,
                    "metadata_provider": "crossref",
                    "hash_semantics": "crossref_message_sha256",
                    "provider_response_sha256": fetched.content_sha256,
                    "untrusted_content": True,
                },
            )
        )
        return CapturedSource(
            version=result,
            extracted_text=canonical.decode("utf-8"),
            content=canonical,
        )


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._suppressed = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"}:
            self._suppressed += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "template"}:
            self._suppressed = max(0, self._suppressed - 1)

    def handle_data(self, data: str) -> None:
        if not self._suppressed:
            self.parts.append(data)


def extract_text(
    body: bytes,
    media_type: str,
    charset: str | None,
    *,
    maximum_bytes: int,
) -> str:
    encoding = charset or "utf-8"
    if encoding.lower().replace("_", "-") not in {
        "utf-8",
        "utf8",
        "us-ascii",
        "ascii",
        "iso-8859-1",
        "latin-1",
    }:
        raise ParserFailed("PARSER_FAILED: Response charset is not allowed.")
    try:
        text = body.decode(encoding)
    except (LookupError, UnicodeDecodeError) as exc:
        raise ParserFailed("PARSER_FAILED: Response text could not be decoded.") from exc
    if media_type in {"text/html", "application/xhtml+xml"}:
        parser = _TextExtractor()
        try:
            parser.feed(text)
            parser.close()
        except Exception as exc:
            raise ParserFailed("PARSER_FAILED: HTML extraction failed.") from exc
        text = " ".join(" ".join(parser.parts).split())
    if len(text.encode("utf-8")) > maximum_bytes:
        raise FetchTooLarge("FETCH_TOO_LARGE: Extracted text exceeds the limit.")
    return text


def normalize_doi(value: str) -> str:
    cleaned = value.strip()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    cleaned = cleaned.strip().lower()
    if (
        not cleaned.startswith("10.")
        or "/" not in cleaned
        or len(cleaned) > 500
        or any(ord(char) < 0x20 or char.isspace() for char in cleaned)
    ):
        raise ParserFailed("PARSER_FAILED: DOI is invalid.")
    return cleaned


def parse_json_document(
    body: bytes,
    *,
    maximum_depth: int,
    maximum_nodes: int,
) -> object:
    try:
        document = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise ParserFailed("PARSER_FAILED: JSON response is invalid.") from exc
    pending: list[tuple[object, int]] = [(document, 1)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > maximum_nodes or depth > maximum_depth:
            raise ParserFailed("PARSER_FAILED: JSON response exceeds parser limits.")
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    return document


def _parse_content_type(value: str) -> tuple[str, str | None]:
    parts = [part.strip() for part in value.split(";")]
    media_type = parts[0].lower()
    charset = None
    for part in parts[1:]:
        key, separator, raw = part.partition("=")
        if separator and key.strip().lower() == "charset":
            charset = raw.strip().strip("\"'").lower()
    return media_type, charset


def _parser_name(media_type: str) -> str:
    if media_type in {"text/html", "application/xhtml+xml"}:
        return "research-registry-html"
    if media_type == "application/json":
        return "research-registry-json"
    return "research-registry-text"


def _utc_now_text() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
