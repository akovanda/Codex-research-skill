from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit


_SECRET_QUERY_KEYS = frozenset(
    {
        "token",
        "access_token",
        "api_key",
        "apikey",
        "key",
        "secret",
        "signature",
        "sig",
        "credential",
        "credentials",
        "session",
        "session_id",
        "sessionid",
        "password",
        "passwd",
        "authorization",
        "private_key",
        "saml_response",
        "assertion",
    }
)
_SECRET_QUERY_SUFFIXES = (
    "_token",
    "_secret",
    "_signature",
    "_credential",
    "_password",
    "_passwd",
    "_authorization",
    "_api_key",
    "_private_key",
    "_access_key",
    "_assertion",
    "_saml_response",
)
_SECRET_COMPACT_QUERY_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "awsaccesskeyid",
        "clientsecret",
        "idtoken",
        "password",
        "passwd",
        "privatekey",
        "refreshtoken",
        "samlresponse",
        "secretaccesskey",
        "sessionid",
        "xapikey",
    }
)


class UnsafeLocatorError(ValueError):
    """An HTTP(S) locator contains material that must not be persisted."""


def validate_safe_locator(value: str) -> str:
    """Reject credential-bearing or non-canonical HTTP(S) locators.

    Non-HTTP locators are intentionally left unchanged for retained DOI, note,
    local-file, and repository compatibility. Error messages never include the
    rejected locator or any query value.
    """

    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise UnsafeLocatorError("HTTP(S) locator is malformed") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        return value
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeLocatorError("HTTP(S) locators must not contain userinfo")
    if parsed.fragment:
        raise UnsafeLocatorError("HTTP(S) locators must not contain URL fragments")
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = key.strip().lower().replace("-", "_")
        compact = normalized.replace("_", "")
        if (
            normalized in _SECRET_QUERY_KEYS
            or normalized.endswith(_SECRET_QUERY_SUFFIXES)
            or compact in _SECRET_COMPACT_QUERY_KEYS
        ):
            raise UnsafeLocatorError(
                "HTTP(S) locators must not contain credential query parameters"
            )
    return value
