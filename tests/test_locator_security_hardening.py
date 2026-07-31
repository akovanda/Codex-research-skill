from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from research_registry.app import create_app
from research_registry.contracts.v2 import ResearchDepositRequest
from research_registry.locator_security import (
    UnsafeLocatorError,
    validate_safe_locator,
)
from tests.test_locator_security import _settings
from tests.test_v2_deposit import _bundle


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/source;lang=en?view=compact",
        "https://xn--r8jz45g.xn--zckzah/source?lang=ja",
    ],
)
def test_canonical_http_locators_with_non_secret_parameters_are_allowed(
    value: str,
) -> None:
    assert validate_safe_locator(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/source?lang=en;access_token=top-secret",
        "https://example.com/source;jsessionid=top-secret",
        "https://example.com/source?%2561ccess_token=top-secret",
        "https://example.com/source?auth[token]=top-secret",
        "https://example.com/source?auth.token=top-secret",
        "https://example.com/source?ａｃｃｅｓｓ＿ｔｏｋｅｎ=top-secret",
        "https://example.com/source?SharedAccessSignature=top-secret",
        "https://example.com/source?ClientAssertion=top-secret",
    ],
)
def test_obfuscated_and_matrix_credential_parameters_are_rejected(
    value: str,
) -> None:
    with pytest.raises(
        UnsafeLocatorError,
        match="credential query parameters",
    ) as caught:
        validate_safe_locator(value)
    assert "top-secret" not in str(caught.value)
    assert value not in str(caught.value)


@pytest.mark.parametrize(
    "value",
    [
        "https:example.com/source",
        "https:///source",
        "https://example.com:invalid/source",
        "https://example.com/source path",
        "https://example.com\\source",
    ],
)
def test_noncanonical_http_locators_are_rejected(value: str) -> None:
    with pytest.raises(UnsafeLocatorError, match="malformed|absolute URL"):
        validate_safe_locator(value)


def test_non_string_locator_is_rejected_with_stable_error() -> None:
    with pytest.raises(UnsafeLocatorError, match="locator must be a string"):
        validate_safe_locator(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "unsafe",
    [
        "https://example.com/source?auth[token]=top-secret",
        "https://example.com/source;jsessionid=top-secret",
    ],
)
def test_v2_contract_inherits_obfuscated_parameter_rejection(unsafe: str) -> None:
    payload = deepcopy(_bundle(key="locator-hardening-v2"))
    payload["sources"][0]["identity"]["locator"] = unsafe

    with pytest.raises(ValidationError, match="credential query parameters"):
        ResearchDepositRequest.model_validate(payload)


def test_retained_http_rejects_matrix_session_without_persistence_or_echo(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    client = TestClient(app)
    response = client.post(
        "/api/sources",
        headers={"x-admin-token": "secret"},
        json={
            "locator": (
                "https://example.com/source;jsessionid=super-secret"
            ),
            "title": "Unsafe matrix session",
        },
    )

    assert response.status_code == 422
    assert "credential query parameters" in response.text
    assert "super-secret" not in response.text
    with app.state.service.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM sources"
        ).fetchone()["count"]
    assert count == 0
