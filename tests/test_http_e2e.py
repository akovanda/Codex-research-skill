from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx

from research_registry.models import (
    ApiKeyCreate,
    ClaimCreate,
    ExcerptCreate,
    FocusTuple,
    IndexStateRequest,
    PublishRequest,
    QuestionCreate,
    ReportCreate,
    ResearchSessionCreate,
    SourceCreate,
    SourceSelector,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def _wait_ready(base_url: str, *, timeout_seconds: float = 15.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error: str | None = None
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/readyz", timeout=2.0)
            if response.status_code == 200 and response.json().get("status") == "ready":
                return
            last_error = f"unexpected response: {response.status_code} {response.text}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise AssertionError(f"server did not become ready: {last_error}")


def _terminate_server(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return process.stdout.read() if process.stdout else ""


def test_live_http_end_to_end(tmp_path: Path) -> None:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    db_path = (tmp_path / "e2e.sqlite3").resolve()
    env = os.environ.copy()
    python_path = env.get("PYTHONPATH")
    env.update(
        {
            "PYTHONPATH": f"{REPO_ROOT / 'src'}{os.pathsep}{python_path}" if python_path else str(REPO_ROOT / "src"),
            "RESEARCH_REGISTRY_DATA_DIR": str((tmp_path / "data").resolve()),
            "RESEARCH_REGISTRY_DATABASE_URL": f"sqlite:///{db_path}",
            "RESEARCH_REGISTRY_ADMIN_TOKEN": "secret",
            "RESEARCH_REGISTRY_SESSION_SECRET": "session-secret",
            "RESEARCH_REGISTRY_HOST": "127.0.0.1",
            "RESEARCH_REGISTRY_PORT": str(port),
            "RESEARCH_REGISTRY_PUBLIC_BASE_URL": base_url,
        }
    )
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "research_registry.app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    logs = ""
    try:
        _wait_ready(base_url)

        focus = FocusTuple(domain="memory-retrieval", object="shared registry recall", context="e2e-test")

        with httpx.Client(base_url=base_url, timeout=10.0) as client:
            assert client.get("/healthz").json() == {"status": "ok"}
            assert client.get("/readyz").json() == {"status": "ready"}
            assert client.get("/").status_code == 200

            org_response = client.post(
                "/api/admin/organizations",
                headers={"x-admin-token": "secret"},
                json={"org_id": "acme", "display_name": "Acme"},
            )
            assert org_response.status_code == 200

            key_response = client.post(
                "/api/admin/api-keys",
                headers={"x-admin-token": "secret"},
                json=ApiKeyCreate(
                    label="acme-writer",
                    actor_user_id="owner",
                    actor_org_id="acme",
                    namespace_kind="org",
                    namespace_id="acme",
                ).model_dump(mode="json"),
            )
            assert key_response.status_code == 200
            api_key = key_response.json()["token"]
            auth_headers = {"x-api-key": api_key}

            question_response = client.post(
                "/api/questions",
                headers=auth_headers,
                json=QuestionCreate(
                    prompt="Research shared registry memory retrieval optimization.",
                    focus=focus,
                    namespace_kind="org",
                    namespace_id="acme",
                ).model_dump(mode="json"),
            )
            assert question_response.status_code == 200
            question_id = question_response.json()["id"]

            session_response = client.post(
                "/api/sessions",
                headers=auth_headers,
                json=ResearchSessionCreate(
                    question_id=question_id,
                    prompt="Research shared registry memory retrieval optimization.",
                    model_name="gpt-5.4",
                    model_version="2026-04-10",
                    mode="live_research",
                    namespace_kind="org",
                    namespace_id="acme",
                    source_signals=["e2e: localhost live HTTP test"],
                ).model_dump(mode="json"),
            )
            assert session_response.status_code == 200
            session_id = session_response.json()["id"]

            source_response = client.post(
                "/api/sources",
                headers=auth_headers,
                json=SourceCreate(
                    locator="https://example.com/shared-registry-recall",
                    title="Shared registry recall note",
                    snippet="shared registry recall optimization",
                    snapshot_present=True,
                    namespace_kind="org",
                    namespace_id="acme",
                ).model_dump(mode="json"),
            )
            assert source_response.status_code == 200
            source_id = source_response.json()["id"]

            excerpt_response = client.post(
                "/api/excerpts",
                headers=auth_headers,
                json=ExcerptCreate(
                    source_id=source_id,
                    question_id=question_id,
                    session_id=session_id,
                    focal_label=focus.label or "shared registry recall",
                    note="The registry needs replayable provenance to improve retrieval reuse.",
                    selector=SourceSelector(
                        exact="shared registry recall optimization",
                        deep_link="https://example.com/shared-registry-recall#provenance",
                    ),
                    quote_text="shared registry recall optimization",
                    namespace_kind="org",
                    namespace_id="acme",
                ).model_dump(mode="json"),
            )
            assert excerpt_response.status_code == 200
            excerpt_id = excerpt_response.json()["id"]

            claim_response = client.post(
                "/api/claims",
                headers=auth_headers,
                json=ClaimCreate(
                    question_id=question_id,
                    session_id=session_id,
                    title="Replayable provenance improves reuse",
                    focal_label=focus.label or "shared registry recall",
                    statement="Replayable provenance improves reuse and keeps retrieved research auditable.",
                    excerpt_ids=[excerpt_id],
                    namespace_kind="org",
                    namespace_id="acme",
                ).model_dump(mode="json"),
            )
            assert claim_response.status_code == 200
            claim_id = claim_response.json()["id"]

            report_response = client.post(
                "/api/reports",
                headers=auth_headers,
                json=ReportCreate(
                    question_id=question_id,
                    session_id=session_id,
                    title="Shared registry retrieval guidance",
                    focal_label=focus.label or "shared registry recall",
                    summary_md="# Guidance\n\nReplayable provenance and explicit namespace controls improve research reuse.",
                    claim_ids=[claim_id],
                    namespace_kind="org",
                    namespace_id="acme",
                ).model_dump(mode="json"),
            )
            assert report_response.status_code == 200
            report_id = report_response.json()["id"]

            assert client.get(f"/api/claims/{claim_id}").status_code == 404
            assert client.get(f"/api/reports/{report_id}").status_code == 404

            publish_claim = client.post(
                "/api/publish",
                headers=auth_headers,
                json=PublishRequest(kind="claim", record_id=claim_id).model_dump(mode="json"),
            )
            assert publish_claim.status_code == 200

            publish_report = client.post(
                "/api/publish",
                headers=auth_headers,
                json=PublishRequest(kind="report", record_id=report_id).model_dump(mode="json"),
            )
            assert publish_report.status_code == 200

            namespace_search = client.get(
                "/api/search",
                params={"q": "shared registry recall", "namespace_slug": "acme"},
            )
            assert namespace_search.status_code == 200
            namespace_hit_ids = {hit["id"] for hit in namespace_search.json()["hits"]}
            assert claim_id in namespace_hit_ids
            assert report_id in namespace_hit_ids

            global_search = client.get("/api/search", params={"q": "shared registry recall"})
            assert global_search.status_code == 200
            assert global_search.json()["hits"] == []

            index_claim = client.post(
                "/api/index-state",
                headers={"x-admin-token": "secret"},
                json=IndexStateRequest(kind="claim", record_id=claim_id, state="included").model_dump(mode="json"),
            )
            assert index_claim.status_code == 200

            index_report = client.post(
                "/api/index-state",
                headers={"x-admin-token": "secret"},
                json=IndexStateRequest(kind="report", record_id=report_id, state="included").model_dump(mode="json"),
            )
            assert index_report.status_code == 200

            global_search = client.get("/api/search", params={"q": "shared registry recall"})
            assert global_search.status_code == 200
            global_hit_ids = {hit["id"] for hit in global_search.json()["hits"]}
            assert claim_id in global_hit_ids
            assert report_id in global_hit_ids

            question_page = client.get(f"/questions/{question_id}")
            assert question_page.status_code == 200
            assert "Shared registry retrieval guidance" in question_page.text

            namespace_page = client.get("/public/acme", params={"q": "shared registry recall"})
            assert namespace_page.status_code == 200
            assert "Replayable provenance improves reuse" in namespace_page.text

            backend_status = client.get("/api/backend/status")
            assert backend_status.status_code == 200
            assert backend_status.json()["url"] == base_url
    except Exception:
        logs = _terminate_server(server)
        raise AssertionError(f"live HTTP end-to-end test failed\n\n{logs}") from None
    else:
        logs = _terminate_server(server)
        if server.returncode not in (0, -15):
            raise AssertionError(f"server exited unexpectedly with code {server.returncode}\n\n{logs}")
