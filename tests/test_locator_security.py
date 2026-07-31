from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

import research_registry.service as service_module
from research_registry.app import create_app
from research_registry.config import Settings
from research_registry.contracts.v2 import ResearchDepositRequest
from research_registry.locator_security import (
    UnsafeLocatorError,
    validate_safe_locator,
)
from research_registry.models import ImportUrlRequest, SourceCreate
from research_registry.service import RegistryService
from tests.test_v2_deposit import _bundle


def _settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    db_path = tmp_path / "app.sqlite3"
    return Settings(
        data_dir=data_dir,
        db_path=db_path,
        database_url=f"sqlite:///{db_path.resolve()}",
        capture_queue_path=data_dir / "pending-research-captures.jsonl",
        backend_profile_path=data_dir / "backend-profiles.json",
        admin_token="secret",
        session_secret="session-secret",
        host="127.0.0.1",
        port=8000,
        default_backend_url="https://registry.example.com",
        backend_url=None,
        backend_api_key=None,
        backend_org=None,
        backend_profile=None,
        public_base_url="https://registry.example.com",
    )


def _service(tmp_path: Path) -> RegistryService:
    service = RegistryService(tmp_path / "registry.sqlite3")
    service.initialize()
    return service


@pytest.mark.parametrize(
    "value",
    [
        "note:retained-source",
        "doi:10.1000/example",
        "file:///tmp/research.txt",
        "https://example.com/source",
        "https://example.com/source?lang=en&view=compact",
        "http://example.com/source?published=2026-07-31",
    ],
)
def test_safe_locator_accepts_non_secret_values(value: str) -> None:
    assert validate_safe_locator(value) == value


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (
            "https://reader:top-secret@example.com/source",
            "must not contain userinfo",
        ),
        (
            "https://example.com/source#private-fragment",
            "must not contain URL fragments",
        ),
        (
            "https://example.com/source?access_token=top-secret",
            "must not contain credential query parameters",
        ),
        (
            "https://example.com/source?X-Amz-Signature=top-secret",
            "must not contain credential query parameters",
        ),
        (
            "https://example.com/source?client-secret=top-secret",
            "must not contain credential query parameters",
        ),
        (
            "https://example.com/source?%61ccess_token=top-secret",
            "must not contain credential query parameters",
        ),
        (
            "https://example.com/source?password=top-secret",
            "must not contain credential query parameters",
        ),
        (
            "https://example.com/source?AWSAccessKeyId=top-secret",
            "must not contain credential query parameters",
        ),
    ],
)
def test_safe_locator_rejects_secret_bearing_http_without_echo(
    value: str,
    message: str,
) -> None:
    with pytest.raises(UnsafeLocatorError, match=message) as caught:
        validate_safe_locator(value)
    assert "top-secret" not in str(caught.value)
    assert value not in str(caught.value)


@pytest.mark.parametrize("field", ["identity", "version"])
def test_v2_contract_uses_the_shared_locator_policy(field: str) -> None:
    payload = deepcopy(_bundle(key=f"shared-locator-{field}"))
    unsafe = "https://example.com/source?access_token=top-secret"
    if field == "identity":
        payload["sources"][0]["identity"]["locator"] = unsafe
    else:
        payload["sources"][0]["version"]["canonical_locator"] = unsafe

    with pytest.raises(ValidationError, match="credential query parameters"):
        ResearchDepositRequest.model_validate(payload)


def test_retained_create_validates_before_dedupe_and_persistence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    existing = service.create_source(
        SourceCreate(
            locator="https://example.com/safe-source",
            title="Safe source",
            dedupe_key="same-source",
        )
    )

    with pytest.raises(UnsafeLocatorError, match="credential query parameters"):
        service.create_source(
            SourceCreate(
                locator="https://example.com/source?access_token=top-secret",
                title="Unsafe source",
                dedupe_key="same-source",
            )
        )
    with pytest.raises(UnsafeLocatorError, match="credential query parameters"):
        service.create_source(
            SourceCreate(
                locator="https://example.com/other-source",
                title="Unsafe snapshot",
                snapshot_url="https://storage.example.com/object?sig=top-secret",
            )
        )

    with service.connect() as conn:
        rows = conn.execute("SELECT id, locator FROM sources ORDER BY id").fetchall()
    assert [(row["id"], row["locator"]) for row in rows] == [
        (existing.id, "https://example.com/safe-source")
    ]


def test_historical_unsafe_locator_remains_readable_for_migration(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source = service.create_source(
        SourceCreate(
            locator="https://example.com/historical-source",
            title="Historical source",
        )
    )
    historical_locator = (
        "https://example.com/historical-source?access_token=legacy-secret"
    )
    historical_snapshot = "https://storage.example.com/object?sig=legacy-secret"
    with service.connect() as conn:
        conn.execute(
            "UPDATE sources SET locator = ?, snapshot_url = ? WHERE id = ?",
            (historical_locator, historical_snapshot, source.id),
        )

    record = service.get_source(source.id, include_private=True)
    assert record.locator == historical_locator
    assert record.snapshot_url == historical_snapshot


def test_import_url_rejects_before_fetch_even_when_contract_is_bypassed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    called = False

    def forbidden_fetch(_: str):
        nonlocal called
        called = True
        raise AssertionError("unsafe URL reached the external fetcher")

    monkeypatch.setattr(service_module, "fetch_url_candidate", forbidden_fetch)
    payload = ImportUrlRequest.model_construct(
        url="https://example.com/import?access_token=top-secret",
        question_id=None,
        focal_label=None,
        note=None,
        namespace_kind="user",
        namespace_id="local",
    )

    with pytest.raises(UnsafeLocatorError, match="credential query parameters"):
        service.import_url(payload)
    assert called is False
    with service.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM sources").fetchone()[
            "count"
        ] == 0


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/sources",
            {
                "url": "https://example.com/source?access_token=super-secret",
                "title": "Unsafe retained source",
            },
        ),
        (
            "/api/sources",
            {
                "locator": "https://example.com/source",
                "snapshot_url": (
                    "https://storage.example.com/object?sig=super-secret"
                ),
                "title": "Unsafe retained snapshot",
            },
        ),
        (
            "/api/import/url",
            {
                "url": "https://example.com/import?password=super-secret",
            },
        ),
    ],
)
def test_retained_http_rejects_secret_locators_without_persisting_or_echoing(
    tmp_path: Path,
    path: str,
    payload: dict[str, str],
) -> None:
    app = create_app(_settings(tmp_path))
    client = TestClient(app)

    response = client.post(
        path,
        headers={"x-admin-token": "secret"},
        json=payload,
    )

    assert response.status_code == 422
    assert "credential query parameters" in response.text
    assert "super-secret" not in response.text
    with app.state.service.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS count FROM sources").fetchone()[
            "count"
        ] == 0
