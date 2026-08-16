from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient
from mcp.shared.version import LATEST_PROTOCOL_VERSION
from mcp.server.fastmcp import FastMCP
import pytest

from research_registry.app import create_app
from research_registry.config import Settings
from research_registry.mcp.deep_research import create_deep_research_server
from research_registry.mcp_tools import create_mcp_server
from research_registry.models import ApiKeyCreate


_PUBLIC_URL = "https://ucs.tail9d8219.ts.net:8443"
_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": LATEST_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "transport-security-test", "version": "1"},
    },
}


def _settings(tmp_path: Path, public_base_url: str = _PUBLIC_URL) -> Settings:
    data_dir = tmp_path / "data"
    database = tmp_path / "registry.sqlite3"
    return Settings(
        data_dir=data_dir,
        db_path=database,
        database_url=f"sqlite:///{database.resolve()}",
        capture_queue_path=data_dir / "pending.jsonl",
        backend_profile_path=data_dir / "profiles.json",
        admin_token="secret",
        session_secret="test-session-secret",
        host="0.0.0.0",
        port=8000,
        default_backend_url=None,
        backend_url=None,
        backend_api_key=None,
        backend_org=None,
        backend_profile=None,
        public_base_url=public_base_url,
    )


def _headers(api_key: str, *, origin: str = _PUBLIC_URL) -> dict[str, str]:
    return {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "origin": origin,
        "x-api-key": api_key,
    }


def test_configured_private_https_authority_is_allowed_on_both_mcp_mounts(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    issued = app.state.service.issue_api_key(
        ApiKeyCreate(
            label="transport security test",
            actor_user_id="transport-test",
            namespace_kind="user",
            namespace_id="transport-test",
            scopes=["read_private"],
        )
    )

    for server in (app.state.mcp, app.state.deep_research_mcp):
        policy = server.settings.transport_security
        assert policy is not None
        assert policy.enable_dns_rebinding_protection is True
        assert "ucs.tail9d8219.ts.net:8443" in policy.allowed_hosts
        assert _PUBLIC_URL in policy.allowed_origins
        assert "127.0.0.1:*" in policy.allowed_hosts
        assert "http://localhost:*" in policy.allowed_origins

    with TestClient(app, base_url=_PUBLIC_URL) as client:
        for path in ("/mcp/", "/deep-research-mcp/"):
            response = client.post(
                path,
                headers=_headers(issued.token),
                json=_INITIALIZE,
            )
            assert response.status_code == 200, response.text
            assert response.json()["result"]["protocolVersion"]


def test_unrelated_host_and_origin_are_rejected_on_both_mcp_mounts(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))

    with TestClient(app, base_url=_PUBLIC_URL) as client:
        for path in ("/mcp/", "/deep-research-mcp/"):
            unrelated_host = client.post(
                path,
                headers={**_headers("unused"), "host": "attacker.example"},
                json=_INITIALIZE,
            )
            assert unrelated_host.status_code == 421

            unrelated_origin = client.post(
                path,
                headers=_headers("unused", origin="https://attacker.example"),
                json=_INITIALIZE,
            )
            assert unrelated_origin.status_code == 403


@pytest.mark.parametrize(
    "factory",
    [create_mcp_server, create_deep_research_server],
)
@pytest.mark.parametrize(
    "public_base_url",
    [
        "https://user:password@ucs.tail9d8219.ts.net",
        "https://ucs.tail9d8219.ts.net\n.attacker.example",
        "https://ucs.tail9d8219.ts.net:invalid",
        "https:///ucs.tail9d8219.ts.net",
    ],
)
def test_mcp_server_factories_reject_invalid_public_urls_consistently(
    tmp_path: Path,
    factory: Callable[..., FastMCP],
    public_base_url: str,
) -> None:
    with pytest.raises(ValueError, match="RESEARCH_REGISTRY_PUBLIC_BASE_URL"):
        factory(object(), settings=_settings(tmp_path, public_base_url))
