from __future__ import annotations

from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings

from ..config import Settings


_LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_LOCAL_ORIGINS = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
]
_INVALID_PUBLIC_URL = (
    "RESEARCH_REGISTRY_PUBLIC_BASE_URL must be a valid HTTP(S) origin "
    "without credentials, path, query, or fragment"
)


def transport_security_settings(settings: Settings) -> TransportSecuritySettings:
    """Build a fail-closed MCP Host/Origin policy from the public URL."""
    raw_url = settings.public_base_url
    if not raw_url or any(character.isspace() for character in raw_url):
        raise ValueError(_INVALID_PUBLIC_URL)

    try:
        parsed = urlsplit(raw_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(_INVALID_PUBLIC_URL) from exc

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or "\\" in raw_url
    ):
        raise ValueError(_INVALID_PUBLIC_URL)

    hostname = parsed.hostname
    host = f"[{hostname}]" if ":" in hostname else hostname
    authority = f"{host}:{port}" if port is not None else host
    origin = f"{parsed.scheme}://{authority}"

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*_LOCAL_HOSTS, authority],
        allowed_origins=[*_LOCAL_ORIGINS, origin],
    )
