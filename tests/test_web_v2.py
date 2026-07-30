from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from research_registry.app import create_app
from research_registry.application.deposit import ResearchDepositService
from research_registry.application.refresh import ResearchRefreshService
from research_registry.config import Settings
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.models import PublishRequest


SNAPSHOT_SENTINEL = "FULL_PRIVATE_SNAPSHOT_SENTINEL"
MALICIOUS_QUOTE = '<script>alert("stored content")</script>'


def _settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    database = tmp_path / "registry.sqlite3"
    return Settings(
        data_dir=data_dir,
        db_path=database,
        database_url=f"sqlite:///{database.resolve()}",
        capture_queue_path=data_dir / "pending.jsonl",
        backend_profile_path=data_dir / "profiles.json",
        admin_token="web-secret",
        session_secret="test-session-secret",
        host="127.0.0.1",
        port=8000,
        default_backend_url=None,
        backend_url=None,
        backend_api_key=None,
        backend_org=None,
        backend_profile=None,
        public_base_url="https://registry.example.test",
    )


def _bundle(*, visibility: str = "private") -> dict:
    quotes = {
        "support": "The current implementation preserves claim history.",
        "refute": "A later observation contradicts part of the conclusion.",
        "qualify": "The guarantee applies only to immutable revisions.",
        "context": MALICIOUS_QUOTE,
    }
    content = " ".join([SNAPSHOT_SENTINEL, *quotes.values()])
    evidence = []
    links = []
    relationships = {
        "support": "supports",
        "refute": "refutes",
        "qualify": "qualifies",
        "context": "contextualizes",
    }
    for ref, quote in quotes.items():
        evidence.append(
            {
                "client_ref": ref,
                "source_version": {"ref": "source"},
                "quote_text": quote,
                "selector": {"type": "text_quote", "exact": quote},
                "note": f"{ref} note",
            }
        )
        links.append(
            {
                "evidence": {"ref": ref},
                "relationship": relationships[ref],
                "rationale": f"{ref} rationale",
            }
        )
    return {
        "protocol": "research-deposit/v2",
        "idempotency_key": "web-v2-synthetic",
        "visibility": visibility,
        "inquiry": {
            "client_ref": "question",
            "prompt": "How does the v2 reviewer inspect evidence?",
            "topic_label": "Web review",
        },
        "run": {
            "client_ref": "run",
            "mode": "research",
            "provenance": {"actor_type": "agent"},
        },
        "sources": [
            {
                "client_ref": "source",
                "identity": {
                    "locator": "note:web-v2",
                    "title": "Reviewer <em>source</em>",
                    "source_type": "note",
                    "canonical_key": "web-v2-source",
                },
                "version": {
                    "version_key": "note:web-v2:v1",
                    "version_kind": "note",
                    "retrieved_at": "2026-07-30T00:00:00Z",
                    "content_sha256": sha256(content.encode()).hexdigest(),
                    "canonical_locator": "note:web-v2",
                    "snapshot": {
                        "policy": "extracted_text",
                        "text": content,
                        "media_type": "text/plain",
                        "byte_count": len(content.encode()),
                    },
                },
            }
        ],
        "evidence": evidence,
        "claims": [
            {
                "client_ref": "claim",
                "title": "Reviewer evidence stays inspectable",
                "statement": "Reviewers see typed evidence without unsafe rendering.",
                "status": "supported",
                "confidence": 0.9,
                "scope": {"repository": "registry", "paths": ["src/**"]},
                "evidence": links,
            }
        ],
        "report": {
            "client_ref": "report",
            "title": "Review workflow report",
            "summary_md": "# Review\n\nStored **Markdown** remains untrusted.",
            "claims": [{"ref": "claim"}],
        },
    }


def _client_with_fixture(
    tmp_path: Path, *, visibility: str = "private"
) -> tuple[TestClient, dict[str, str]]:
    settings = _settings(tmp_path)
    app = create_app(settings)
    receipt = ResearchDepositService(
        app.state.service.database,
        FilesystemBlobStore(settings.data_dir / "blobs"),
    ).deposit(_bundle())
    if visibility == "public":
        assert receipt.records.report_id is not None
        app.state.service.publish(
            PublishRequest(
                kind="report",
                record_id=receipt.records.report_id,
                include_in_global_index=True,
            )
        )
    ids = {
        "source": receipt.records.source_ids["source"],
        "source_version": receipt.records.source_version_ids["source"],
        "claim": receipt.records.claim_ids["claim"],
        "revision": receipt.records.claim_revision_ids["claim"],
        "support": receipt.records.evidence_ids["support"],
        "refute": receipt.records.evidence_ids["refute"],
        "qualify": receipt.records.evidence_ids["qualify"],
        "context": receipt.records.evidence_ids["context"],
        "report": receipt.records.report_id or "",
    }
    return TestClient(app), ids


def test_v2_search_is_private_aware_explained_and_keyboard_accessible(
    tmp_path: Path,
) -> None:
    client, ids = _client_with_fixture(tmp_path)

    public = client.get("/v2/search", params={"q": "reviewer evidence"})
    private = client.get(
        "/v2/search",
        params={"q": "reviewer evidence"},
        headers={"x-admin-token": "web-secret"},
    )

    assert public.status_code == 200
    assert ids["claim"] not in public.text
    assert private.status_code == 200
    assert ids["claim"] in private.text
    assert "score reasons" in private.text.lower()
    assert "unreviewed" in private.text.lower()
    assert "conflicted" in private.text.lower()
    assert 'name="q"' in private.text
    assert 'type="submit"' in private.text
    assert "autofocus" in private.text


def test_claim_evidence_source_and_receipt_render_safe_typed_details(
    tmp_path: Path,
) -> None:
    client, ids = _client_with_fixture(tmp_path, visibility="public")

    claim = client.get(f"/v2/claims/{ids['claim']}")
    claim_admin = client.get(
        f"/v2/claims/{ids['claim']}",
        headers={"x-admin-token": "web-secret"},
    )
    evidence = client.get(f"/v2/evidence/{ids['context']}")
    source = client.get(f"/v2/sources/{ids['source']}")
    receipt_public = client.get("/v2/deposits/web-v2-synthetic")
    receipt = client.get(
        "/v2/deposits/web-v2-synthetic",
        headers={"x-admin-token": "web-secret"},
    )

    assert claim.status_code == 200
    for heading in ("Supports", "Refutes", "Qualifies", "Context"):
        assert heading in claim.text
    assert "Current revision" in claim.text
    assert "Revision history" in claim.text
    assert "<script>" not in claim.text
    assert "&lt;script&gt;" in claim.text
    assert 'aria-label="Approve current claim revision"' in claim_admin.text

    assert evidence.status_code == 200
    assert "<script>" not in evidence.text
    assert "&lt;script&gt;" in evidence.text
    assert ids["source_version"] in evidence.text
    assert "text_quote" in evidence.text

    assert source.status_code == 200
    assert "Version history" in source.text
    assert "Initial observation" in source.text
    assert ids["source_version"] in source.text
    assert SNAPSHOT_SENTINEL not in source.text
    assert "<em>source</em>" not in source.text
    assert "&lt;em&gt;source&lt;/em&gt;" in source.text

    assert receipt_public.status_code == 401
    assert receipt.status_code == 200
    assert "Committed" in receipt.text
    assert ids["claim"] in receipt.text
    assert "Review required" in receipt.text


def test_review_refresh_status_auth_and_concurrency_error_are_understandable(
    tmp_path: Path,
) -> None:
    client, ids = _client_with_fixture(tmp_path)
    refresh = ResearchRefreshService(client.app.state.service.database)
    refresh.refresh(
        {
            "protocol": "research-refresh/v2",
            "mode": "enqueue",
            "idempotency_key": "web-refresh",
            "entities": [{"kind": "claim", "id": ids["claim"]}],
        }
    )

    for path in ("/v2/review", "/v2/refresh", "/v2/status"):
        assert client.get(path).status_code == 401

    headers = {"x-admin-token": "web-secret"}
    inbox = client.get("/v2/review", headers=headers)
    queue = client.get("/v2/refresh", headers=headers)
    status = client.get("/v2/status", headers=headers)
    assert inbox.status_code == 200
    assert ids["claim"] in inbox.text
    assert "Needs review" in inbox.text
    assert queue.status_code == 200
    assert "pending" in queue.text.lower()
    assert "manual" in queue.text.lower()
    assert "Change and anchor metadata" in queue.text
    assert "requested_by" in queue.text
    assert status.status_code == 200
    for label in (
        "Application version",
        "Schema version",
        "Database",
        "Blob storage",
        "Capture mode",
        "Legacy tools",
        "Backup integrity",
    ):
        assert label in status.text
    assert "web-secret" not in status.text
    assert "test-session-secret" not in status.text

    payload = {
        "entity_kind": "claim_revision",
        "entity_id": ids["revision"],
        "action": "approve",
        "expected_revision_id": ids["revision"],
        "expected_state": "unreviewed",
        "note": "Reviewed in the operator UI.",
    }
    applied = client.post(
        "/v2/review",
        headers=headers,
        data=payload,
        follow_redirects=False,
    )
    conflict = client.post("/v2/review", headers=headers, data=payload)

    assert applied.status_code == 303
    assert applied.headers["location"] == f"/v2/claims/{ids['claim']}"
    assert conflict.status_code == 409
    assert "another reviewer changed" in conflict.text.lower()
    assert "Reload the current record" in conflict.text
    assert "EXPECTED_STATE_MISMATCH" not in conflict.text


def test_v1_public_detail_routes_remain_compatible(tmp_path: Path) -> None:
    client, ids = _client_with_fixture(tmp_path, visibility="public")

    assert client.get(f"/claims/{ids['claim']}").status_code == 200
    assert client.get(f"/sources/{ids['source']}").status_code == 200
    assert client.get(f"/excerpts/{ids['support']}").status_code == 404
