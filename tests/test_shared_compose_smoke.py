from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from uuid import uuid4

import httpx
import pytest

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
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_SHARED_COMPOSE_SMOKE") != "1",
    reason="shared compose smoke runs only in dedicated CI or explicit opt-in runs",
)


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def _compose_command(project_name: str, env_file: Path, *args: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        project_name,
        "-f",
        str(REPO_ROOT / "deploy" / "compose.yaml"),
        "--env-file",
        str(env_file),
        *args,
    ]


def _wait_ready(base_url: str, *, timeout_seconds: float = 90.0) -> None:
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
        time.sleep(1.0)
    raise AssertionError(f"shared compose stack did not become ready: {last_error}")


def _compose_logs(project_name: str, env_file: Path) -> str:
    result = subprocess.run(
        _compose_command(project_name, env_file, "logs", "--no-color"),
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout + result.stderr


def test_shared_compose_end_to_end(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is required for shared compose smoke")

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    suffix = uuid4().hex[:8]
    project_name = f"rr-smoke-{suffix}"
    env_file = REPO_ROOT / "deploy" / ".env"
    original_env = env_file.read_text(encoding="utf-8") if env_file.exists() else None
    env_file.write_text(
        "\n".join(
            [
                "RESEARCH_REGISTRY_DATABASE_URL=postgresql://registry:registry@postgres:5432/registry",
                "RESEARCH_REGISTRY_ADMIN_TOKEN=secret",
                "RESEARCH_REGISTRY_SESSION_SECRET=session-secret",
                "RESEARCH_REGISTRY_HOST=0.0.0.0",
                "RESEARCH_REGISTRY_PORT=8000",
                f"RESEARCH_REGISTRY_PUBLIC_BASE_URL={base_url}",
                "RESEARCH_REGISTRY_BIND_HOST=127.0.0.1",
                f"RESEARCH_REGISTRY_BIND_PORT={port}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    logs = ""
    try:
        subprocess.run(
            _compose_command(project_name, env_file, "up", "--build", "-d"),
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        _wait_ready(base_url)

        focus = FocusTuple(domain="memory-retrieval", object=f"shared compose smoke {suffix}", context="compose-smoke")

        with httpx.Client(base_url=base_url, timeout=10.0) as client:
            assert client.get("/healthz").json() == {"status": "ok"}
            assert client.get("/readyz").json() == {"status": "ready"}

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
                    prompt=f"Research shared compose smoke {suffix}.",
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
                    prompt=f"Research shared compose smoke {suffix}.",
                    model_name="gpt-5.4",
                    model_version="2026-04-10",
                    mode="live_research",
                    namespace_kind="org",
                    namespace_id="acme",
                    source_signals=["smoke: shared compose job"],
                ).model_dump(mode="json"),
            )
            assert session_response.status_code == 200
            session_id = session_response.json()["id"]

            source_response = client.post(
                "/api/sources",
                headers=auth_headers,
                json=SourceCreate(
                    locator=f"https://example.com/shared-compose-smoke-{suffix}",
                    title=f"Shared compose smoke {suffix}",
                    snippet="shared compose provenance",
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
                    focal_label=focus.label or f"shared compose smoke {suffix}",
                    note="Compose smoke evidence stays replayable and attributable.",
                    selector=SourceSelector(
                        exact="shared compose provenance",
                        deep_link=f"https://example.com/shared-compose-smoke-{suffix}#provenance",
                    ),
                    quote_text="shared compose provenance",
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
                    title=f"Shared compose claim {suffix}",
                    focal_label=focus.label or f"shared compose smoke {suffix}",
                    statement="Shared compose smoke captures replayable provenance and organization scoping.",
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
                    title=f"Shared compose report {suffix}",
                    focal_label=focus.label or f"shared compose smoke {suffix}",
                    summary_md="# Guidance\n\nShared compose smoke succeeded.",
                    claim_ids=[claim_id],
                    namespace_kind="org",
                    namespace_id="acme",
                ).model_dump(mode="json"),
            )
            assert report_response.status_code == 200
            report_id = report_response.json()["id"]

            assert client.get(f"/api/reports/{report_id}").status_code == 404

            publish_response = client.post(
                "/api/publish",
                headers=auth_headers,
                json=PublishRequest(kind="report", record_id=report_id).model_dump(mode="json"),
            )
            assert publish_response.status_code == 200

            index_response = client.post(
                "/api/index-state",
                headers={"x-admin-token": "secret"},
                json=IndexStateRequest(kind="report", record_id=report_id, state="included").model_dump(mode="json"),
            )
            assert index_response.status_code == 200

            private_report = client.get(
                f"/api/reports/{report_id}",
                headers=auth_headers,
                params={"include_private": "true"},
            )
            assert private_report.status_code == 200

            global_search = client.get("/api/search", params={"q": suffix})
            assert global_search.status_code == 200
            assert report_id in {hit["id"] for hit in global_search.json()["hits"]}
    except Exception:
        logs = _compose_logs(project_name, env_file)
        raise AssertionError(f"shared compose smoke failed\n\n{logs}") from None
    finally:
        subprocess.run(
            _compose_command(project_name, env_file, "down", "-v"),
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if original_env is None:
            env_file.unlink(missing_ok=True)
        else:
            env_file.write_text(original_env, encoding="utf-8")
