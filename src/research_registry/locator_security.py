from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote_plus, urlsplit


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
        "auth",
        "bearer",
        "jwt",
        "jsessionid",
        "private_key",
        "saml_response",
        "assertion",
        "oauth_verifier",
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
    "_oauth_verifier",
)
_SECRET_COMPACT_QUERY_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "authtoken",
        "awsaccesskeyid",
        "bearertoken",
        "clientassertion",
        "clientsecret",
        "idtoken",
        "jsessionid",
        "oauthverifier",
        "password",
        "passwd",
        "privatekey",
        "refreshtoken",
        "samlresponse",
        "secretaccesskey",
        "securitytoken",
        "sessionid",
        "sharedaccesssignature",
        "xapikey",
    }
)
_HTTP_SCHEMES = frozenset({"http", "https"})
_ASCII_CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f]")
_PARAMETER_SEPARATOR = re.compile(r"[&;]")
_BRACKETED_COMPONENT = re.compile(r"[^\[\]]+")


class UnsafeLocatorError(ValueError):
    """An HTTP(S) locator contains material that must not be persisted."""


def validate_safe_locator(value: str) -> str:
    """Reject credential-bearing or non-canonical HTTP(S) locators.

    Non-HTTP locators are intentionally left unchanged for retained DOI, note,
    local-file, and repository compatibility. Error messages never include the
    rejected locator or any parameter value.
    """

    if not isinstance(value, str):
        raise UnsafeLocatorError("locator must be a string")
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError) as exc:
        raise UnsafeLocatorError("HTTP(S) locator is malformed") from exc
    if parsed.scheme.lower() not in _HTTP_SCHEMES:
        return value
    if _ASCII_CONTROL_OR_SPACE.search(value) or "\\" in value:
        raise UnsafeLocatorError("HTTP(S) locator is malformed")
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise UnsafeLocatorError("HTTP(S) locator is malformed") from exc
    if not parsed.netloc or hostname is None:
        raise UnsafeLocatorError("HTTP(S) locator must be an absolute URL")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeLocatorError("HTTP(S) locators must not contain userinfo")
    if parsed.fragment:
        raise UnsafeLocatorError("HTTP(S) locators must not contain URL fragments")
    for raw_key in _parameter_keys(parsed.query, parsed.path):
        if _is_secret_parameter_key(raw_key):
            raise UnsafeLocatorError(
                "HTTP(S) locators must not contain credential query parameters"
            )
    return value


def _parameter_keys(query: str, path: str):
    for field in _PARAMETER_SEPARATOR.split(query):
        if field:
            yield field.partition("=")[0]
    for segment in path.split("/"):
        matrix_fields = segment.split(";")
        for field in matrix_fields[1:]:
            if field:
                yield field.partition("=")[0]


def _is_secret_parameter_key(raw_key: str) -> bool:
    key = raw_key
    for _ in range(4):
        decoded = unquote_plus(key)
        if decoded == key:
            break
        key = decoded
    key = unicodedata.normalize("NFKC", key).strip().lower()
    key = re.sub(r"[\s.\-]+", "_", key)
    candidates = {key}
    candidates.update(
        component.strip("_")
        for component in _BRACKETED_COMPONENT.findall(key)
        if component.strip("_")
    )
    for candidate in candidates:
        compact = candidate.replace("_", "")
        if (
            candidate in _SECRET_QUERY_KEYS
            or candidate.endswith(_SECRET_QUERY_SUFFIXES)
            or compact in _SECRET_COMPACT_QUERY_KEYS
        ):
            return True
    return False
