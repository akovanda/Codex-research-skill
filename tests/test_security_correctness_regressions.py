from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from research_registry.app import create_app
from research_registry.backend_client import RegistryApiClient
from research_registry.config import Settings
from research_registry.models import BackendStatus


def _settings(
    tmp_path: Path,
    *,
    admin_token: str | None = "secret",
    host: str = "127.0.0.1",
    public_base_url: str = "http://127.0.0.1:8000",
) -> Settings:
    data_dir = tmp_path / "data"
    database = tmp_path / "registry.sqlite3"
    return Settings(
        data_dir=data_dir,
        db_path=database,
        database_url=f"sqlite:///{database.resolve()}",
        capture_queue_path=data_dir / "pending.jsonl",
        backend_profile_path=data_dir / "profiles.json",
        admin_token=admin_token,
        session_secret="test-session-secret",
        host=host,
        port=8000,
        default_backend_url=None,
        backend_url=None,
        backend_api_key=None,
        backend_org=None,
        backend_profile=None,
        public_base_url=public_base_url,
    )


@pytest.mark.parametrize(
    ("host", "public_base_url"),
    [
        ("0.0.0.0", "http://127.0.0.1:8000"),
        ("::", "http://[::1]:8000"),
        ("127.0.0.1", "https://registry.example.test"),
    ],
)
def test_tokenless_admin_mode_rejects_non_loopback_exposure(
    tmp_path: Path,
    host: str,
    public_base_url: str,
) -> None:
    with pytest.raises(ValueError, match="RESEARCH_REGISTRY_ADMIN_TOKEN"):
        create_app(
            _settings(
                tmp_path,
                admin_token=None,
                host=host,
                public_base_url=public_base_url,
            )
        )


def test_empty_admin_token_is_treated_as_tokenless(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="RESEARCH_REGISTRY_ADMIN_TOKEN"):
        create_app(
            _settings(
                tmp_path,
                admin_token="   ",
                host="0.0.0.0",
                public_base_url="https://registry.example.test",
            )
        )


def test_configured_admin_token_allows_non_loopback_binding(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(
            tmp_path,
            admin_token="secret",
            host="0.0.0.0",
            public_base_url="https://registry.example.test",
        )
    )
    client = TestClient(app)
    assert client.get("/admin").status_code == 401
    assert client.get("/admin", headers={"x-admin-token": "secret"}).status_code == 200


def test_tokenless_admin_mode_remains_available_on_explicit_loopback(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path, admin_token=None))
    response = TestClient(app).get("/admin")
    assert response.status_code == 200


def test_remote_backend_rejects_plaintext_and_credential_bearing_urls() -> None:
    status = BackendStatus(
        name="remote",
        kind="custom",
        selection_source="test",
        url="https://registry.example.test",
    )

    with pytest.raises(ValueError, match="BACKEND_TRANSPORT_INSECURE"):
        RegistryApiClient("http://registry.example.test", "rrk_secret", status)
    with pytest.raises(ValueError, match="BACKEND_URL_INVALID"):
        RegistryApiClient(
            "https://user:password@registry.example.test",
            "rrk_secret",
            status,
        )
    with pytest.raises(ValueError, match="BACKEND_URL_INVALID"):
        RegistryApiClient(
            "https://registry.example.test/prefix",
            "rrk_secret",
            status,
        )
    with pytest.raises(ValueError, match="BACKEND_URL_INVALID"):
        RegistryApiClient(
            "https://registry.example.test?token=secret",
            "rrk_secret",
            status,
        )
    with pytest.raises(ValueError, match="BACKEND_URL_INVALID"):
        RegistryApiClient(
            "https://registry.example.test\n.evil.test",
            "rrk_secret",
            status,
        )
    with pytest.raises(ValueError, match="BACKEND_URL_INVALID"):
        RegistryApiClient(
            "https:\\registry.example.test",
            "rrk_secret",
            status,
        )

    loopback = RegistryApiClient("http://127.0.0.1:8000/", None, status)
    assert loopback.base_url == "http://127.0.0.1:8000"


def test_ready_probe_redacts_storage_exception_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_settings(tmp_path))

    def fail_ready() -> None:
        raise RuntimeError(
            "postgresql://private-user:private-password@db/private_table"
        )

    monkeypatch.setattr(app.state.service, "check_ready", fail_ready)
    response = TestClient(app).get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"detail": "storage unavailable"}
    assert "private-user" not in response.text
    assert "private-password" not in response.text
    assert "private_table" not in response.text


def test_question_status_rejects_unknown_values_without_mutating_state(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    client = TestClient(app)
    headers = {"x-admin-token": "secret"}
    created = client.post(
        "/api/questions",
        headers=headers,
        json={
            "prompt": "Can an invalid status corrupt the question?",
            "focus": {
                "domain": "correctness",
                "object": "question status",
            },
        },
    )
    assert created.status_code == 200
    question_id = created.json()["id"]

    invalid = client.post(
        f"/api/questions/{question_id}/status",
        headers=headers,
        json={"status": "silently-corrupted"},
    )
    assert invalid.status_code == 422

    current = client.get(
        f"/api/questions/{question_id}",
        headers=headers,
        params={"include_private": "true"},
    )
    assert current.status_code == 200
    assert current.json()["status"] == "open"

    with pytest.raises(ValueError, match="invalid question status"):
        app.state.service.set_question_status(
            question_id,
            "silently-corrupted",  # type: ignore[arg-type]
        )

    valid = client.post(
        f"/api/questions/{question_id}/status",
        headers=headers,
        json={
            "status": "answered",
            "namespace_kind": "user",
            "namespace_id": "local",
        },
    )
    assert valid.status_code == 200


def test_https_admin_session_cookie_is_secure_and_local_http_still_works(
    tmp_path: Path,
) -> None:
    https_app = create_app(
        _settings(
            tmp_path / "https",
            public_base_url="https://registry.example.test",
        )
    )
    https_client = TestClient(
        https_app,
        base_url="https://registry.example.test",
        follow_redirects=False,
    )
    https_login = https_client.post("/admin/login", data={"token": "secret"})
    assert https_login.status_code == 303
    https_cookie = https_login.headers["set-cookie"].lower()
    assert "; secure" in https_cookie
    assert "httponly" in https_cookie
    assert "samesite=lax" in https_cookie
    assert https_client.get("/admin").status_code == 200

    http_app = create_app(_settings(tmp_path / "http"))
    http_client = TestClient(
        http_app,
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    )
    http_login = http_client.post("/admin/login", data={"token": "secret"})
    assert http_login.status_code == 303
    http_cookie = http_login.headers["set-cookie"].lower()
    assert "; secure" not in http_cookie
    assert http_client.get("/admin").status_code == 200
