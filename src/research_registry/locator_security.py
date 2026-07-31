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
_PARAMETER_SEPARATOR = re.compile(r"[&;]")
_BRACKETED_COMPONENT = re.compile(r"[^\[\]]+")
_MAX_COMPONENT_DECODE_ROUNDS = 4
_MAX_NESTED_URL_DEPTH = 3


class UnsafeLocatorError(ValueError):
    """An HTTP(S) locator contains material that must not be persisted."""


def validate_safe_locator(value: str) -> str:
    """Reject credential-bearing or non-canonical HTTP(S) locators.

    Non-HTTP locators are intentionally left unchanged for retained DOI, note,
    local-file, and repository compatibility. Error messages never include the
    rejected locator or any parameter value.
    """

    return _validate_safe_locator(value, nested_depth=0)


def _validate_safe_locator(value: str, *, nested_depth: int) -> str:
    if not isinstance(value, str):
        raise UnsafeLocatorError("locator must be a string")
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError) as exc:
        raise UnsafeLocatorError("HTTP(S) locator is malformed") from exc
    if parsed.scheme.lower() not in _HTTP_SCHEMES:
        return value
    if _has_unsafe_url_character(value):
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
    for raw_key, raw_value in _parameter_fields(parsed.query, parsed.path):
        if _is_secret_parameter_key(raw_key):
            raise UnsafeLocatorError(
                "HTTP(S) locators must not contain credential query parameters"
            )
        nested = _nested_http_locator(raw_value)
        if nested is not None:
            if nested_depth >= _MAX_NESTED_URL_DEPTH:
                raise UnsafeLocatorError(
                    "HTTP(S) locators must not contain deeply nested URLs"
                )
            _validate_safe_locator(nested, nested_depth=nested_depth + 1)
    return value


def _has_unsafe_url_character(value: str) -> bool:
    for character in value:
        if (
            character == "\\"
            or character.isspace()
            or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        ):
            return True
    return False


def _parameter_fields(query: str, path: str):
    for field in _PARAMETER_SEPARATOR.split(query):
        if field:
            key, separator, value = field.partition("=")
            yield key, value if separator else ""
    for segment in path.split("/"):
        matrix_fields = segment.split(";")
        for field in matrix_fields[1:]:
            if field:
                key, separator, value = field.partition("=")
                yield key, value if separator else ""


def _decode_component(value: str) -> str:
    decoded = value
    for _ in range(_MAX_COMPONENT_DECODE_ROUNDS):
        next_value = unquote_plus(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def _is_secret_parameter_key(raw_key: str) -> bool:
    key = unicodedata.normalize("NFKC", _decode_component(raw_key))
    key = re.sub(r"[\s.\-]+", "_", key.strip().lower())
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


def _nested_http_locator(raw_value: str) -> str | None:
    value = unicodedata.normalize("NFKC", _decode_component(raw_value)).strip()
    lowered = value.lower()
    if lowered.startswith(("http://", "https://")):
        return value
    if value.startswith("//"):
        return f"https:{value}"
    return None
