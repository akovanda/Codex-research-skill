from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import socket
from typing import Callable, Iterable
from urllib.parse import SplitResult, urlsplit, urlunsplit


Resolver = Callable[[str, int], Iterable[object]]


class FetchPolicyError(ValueError):
    """A URL or resolved target violates the machine capture policy."""


class UrlSchemeDenied(FetchPolicyError):
    pass


class UrlAddressDenied(FetchPolicyError):
    pass


class UrlPortDenied(FetchPolicyError):
    pass


@dataclass(frozen=True)
class FetchPolicy:
    allow_http: bool = False
    allowed_nonstandard_ports: frozenset[int] = field(default_factory=frozenset)
    max_url_length: int = 8192
    max_redirects: int = 5
    max_response_bytes: int = 10_000_000
    max_extracted_text_bytes: int = 5_000_000
    max_duration_seconds: float = 30.0
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 10.0
    max_header_bytes: int = 64_000
    max_json_depth: int = 100
    max_json_nodes: int = 100_000
    allowed_media_types: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "text/plain",
                "text/html",
                "application/xhtml+xml",
                "application/json",
            }
        )
    )
    user_agent: str = "ResearchRegistry/0.1.0"

    def __post_init__(self) -> None:
        positive_ints = (
            self.max_url_length,
            self.max_response_bytes,
            self.max_extracted_text_bytes,
            self.max_header_bytes,
            self.max_json_depth,
            self.max_json_nodes,
        )
        if any(isinstance(value, bool) or value <= 0 for value in positive_ints):
            raise ValueError("fetch size limits must be positive")
        if self.max_redirects < 0:
            raise ValueError("redirect limit must be non-negative")
        if any(
            value <= 0
            for value in (
                self.max_duration_seconds,
                self.connect_timeout_seconds,
                self.read_timeout_seconds,
            )
        ):
            raise ValueError("fetch time limits must be positive")
        if any(port < 1 or port > 65535 for port in self.allowed_nonstandard_ports):
            raise ValueError("allowed ports must be between 1 and 65535")


@dataclass(frozen=True)
class ValidatedTarget:
    url: str
    scheme: str
    host: str
    port: int
    address: str
    addresses: tuple[str, ...]
    request_target: str
    host_header: str


_LOCAL_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "instance-data",
}
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal")


def default_resolver(host: str, port: int) -> Iterable[object]:
    return socket.getaddrinfo(
        host,
        port,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )


def validate_url(
    url: str,
    policy: FetchPolicy,
    *,
    resolver: Resolver = default_resolver,
) -> ValidatedTarget:
    if (
        not isinstance(url, str)
        or not url
        or len(url) > policy.max_url_length
        or any(ord(char) < 0x20 or char == "\x7f" for char in url)
        or "\\" in url
    ):
        raise UrlAddressDenied("URL_ADDRESS_DENIED: URL is malformed or too long.")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UrlAddressDenied("URL_ADDRESS_DENIED: URL authority is invalid.") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or (scheme == "http" and not policy.allow_http):
        raise UrlSchemeDenied("URL_SCHEME_DENIED: URL scheme is not allowed.")
    if parsed.username is not None or parsed.password is not None:
        raise UrlAddressDenied("URL_ADDRESS_DENIED: Embedded credentials are forbidden.")
    if parsed.hostname is None:
        raise UrlAddressDenied("URL_ADDRESS_DENIED: URL host is required.")

    host = _normalize_host(parsed.hostname)
    if (
        host in _LOCAL_HOSTS
        or host.endswith(_LOCAL_SUFFIXES)
        or host.endswith(".arpa")
        or "%" in host
    ):
        raise UrlAddressDenied("URL_ADDRESS_DENIED: Local hostnames are forbidden.")
    default_port = 443 if scheme == "https" else 80
    port = port or default_port
    if port != default_port and port not in policy.allowed_nonstandard_ports:
        raise UrlPortDenied("URL_ADDRESS_DENIED: URL port is not allowed.")

    try:
        resolved = tuple(
            sorted({_resolved_address(item) for item in resolver(host, port)})
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise UrlAddressDenied("URL_ADDRESS_DENIED: URL host could not be resolved.") from exc
    if not resolved:
        raise UrlAddressDenied("URL_ADDRESS_DENIED: URL host has no addresses.")
    for address in resolved:
        _validate_address(address)

    normalized = _normalized_url(parsed, scheme, host, port, default_port)
    request_target = parsed.path or "/"
    if parsed.query:
        request_target = f"{request_target}?{parsed.query}"
    bracketed = f"[{host}]" if ":" in host else host
    host_header = bracketed if port == default_port else f"{bracketed}:{port}"
    return ValidatedTarget(
        url=normalized,
        scheme=scheme,
        host=host,
        port=port,
        address=resolved[0],
        addresses=resolved,
        request_target=request_target,
        host_header=host_header,
    )


def _normalize_host(host: str) -> str:
    lowered = host.rstrip(".").lower()
    if not lowered or len(lowered) > 253:
        raise UrlAddressDenied("URL_ADDRESS_DENIED: URL host is invalid.")
    try:
        return lowered.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UrlAddressDenied("URL_ADDRESS_DENIED: URL host is invalid.") from exc


def _resolved_address(item: object) -> str:
    if (
        isinstance(item, tuple)
        and len(item) == 2
        and isinstance(item[0], int)
        and isinstance(item[1], str)
    ):
        return item[1]
    if isinstance(item, tuple) and len(item) >= 5:
        sockaddr = item[4]
        if isinstance(sockaddr, tuple) and sockaddr and isinstance(sockaddr[0], str):
            return sockaddr[0]
    if isinstance(item, str):
        return item
    raise ValueError("resolver returned an invalid address")


def _validate_address(raw: str) -> None:
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise UrlAddressDenied("URL_ADDRESS_DENIED: Resolved address is invalid.") from exc
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if (
        not address.is_global
        or address.is_multicast
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_private
        or address.is_reserved
    ):
        raise UrlAddressDenied("URL_ADDRESS_DENIED: Non-public addresses are forbidden.")


def _normalized_url(
    parsed: SplitResult,
    scheme: str,
    host: str,
    port: int,
    default_port: int,
) -> str:
    bracketed = f"[{host}]" if ":" in host else host
    authority = bracketed if port == default_port else f"{bracketed}:{port}"
    return urlunsplit(
        (
            scheme,
            authority,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )
