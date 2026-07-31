from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from research_registry.app import create_app
from research_registry.application.migrate_v2 import run_v2_backfill
from research_registry.config import Settings
from research_registry.external_ingest import (
    CapturedVersionCandidate,
    ImportedSourceCandidate,
)
from research_registry.models import ApiKeyCreate, SourceCreate


def _settings(tmp_path: Path, database_url: str) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        data_dir=data_dir,
        db_path=tmp_path / "registry.sqlite3",
        database_url=database_url,
        capture_queue_path=data_dir / "pending.jsonl",
        backend_profile_path=data_dir / "profiles.json",
        admin_token="secret",
        session_secret="session-secret",
        host="127.0.0.1",
        port=8000,
        default_backend_url="https://registry.example.test",
        backend_url=None,
        backend_api_key=None,
        backend_org=None,
        backend_profile=None,
        public_base_url="https://registry.example.test",
    )


def _issued_headers(service, actor: str, *, admin: bool = False) -> dict[str, str]:
    scopes = (
        ["admin", "ingest", "publish", "read_private"]
        if admin
        else ["ingest", "publish", "read_private"]
    )
    issued = service.issue_api_key(
        ApiKeyCreate(
            label=f"{actor}-key",
            actor_user_id=actor,
            namespace_kind="user",
            namespace_id=actor,
            scopes=scopes,
        )
    )
    return {"x-api-key": issued.token}


def _post(client: TestClient, path: str, headers: dict[str, str], body: dict):
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _create_graph(
    client: TestClient,
    headers: dict[str, str],
    namespace_id: str,
    suffix: str,
) -> dict[str, str]:
    question = _post(
        client,
        "/api/questions",
        headers,
        {
            "prompt": f"{namespace_id} question {suffix}",
            "focus": {"domain": "authorization", "object": suffix},
            "namespace_kind": "user",
            "namespace_id": namespace_id,
            "dedupe_key": f"{suffix}:question",
        },
    )
    session = _post(
        client,
        "/api/sessions",
        headers,
        {
            "question_id": question["id"],
            "mode": "synthesis",
            "namespace_kind": "user",
            "namespace_id": namespace_id,
            "dedupe_key": f"{suffix}:session",
        },
    )
    source = _post(
        client,
        "/api/sources",
        headers,
        {
            "locator": f"note:{suffix}",
            "title": f"{namespace_id} private source {suffix}",
            "snapshot_present": True,
            "namespace_kind": "user",
            "namespace_id": namespace_id,
            "dedupe_key": f"{suffix}:source",
        },
    )
    excerpt = _post(
        client,
        "/api/excerpts",
        headers,
        {
            "source_id": source["id"],
            "question_id": question["id"],
            "session_id": session["id"],
            "focal_label": "authorization",
            "note": "Private evidence.",
            "selector": {"exact": "private evidence"},
            "quote_text": "private evidence",
            "namespace_kind": "user",
            "namespace_id": namespace_id,
            "dedupe_key": f"{suffix}:excerpt",
        },
    )
    claim = _post(
        client,
        "/api/claims",
        headers,
        {
            "question_id": question["id"],
            "session_id": session["id"],
            "title": "Private claim",
            "focal_label": "authorization",
            "statement": "The private evidence supports this claim.",
            "excerpt_ids": [excerpt["id"]],
            "namespace_kind": "user",
            "namespace_id": namespace_id,
            "dedupe_key": f"{suffix}:claim",
        },
    )
    report = _post(
        client,
        "/api/reports",
        headers,
        {
            "question_id": question["id"],
            "session_id": session["id"],
            "title": "Private report",
            "focal_label": "authorization",
            "summary_md": "Private report.",
            "claim_ids": [claim["id"]],
            "namespace_kind": "user",
            "namespace_id": namespace_id,
            "dedupe_key": f"{suffix}:report",
        },
    )
    return {
        "question": question["id"],
        "session": session["id"],
        "source": source["id"],
        "excerpt": excerpt["id"],
        "claim": claim["id"],
        "report": report["id"],
    }


def _captured_candidate(locator: str) -> ImportedSourceCandidate:
    content = f"same URL private capture {locator}".encode()
    digest = sha256(content).hexdigest()
    now = datetime.now(UTC).replace(microsecond=0)
    return ImportedSourceCandidate(
        source=SourceCreate(
            locator=locator,
            title="Same URL import",
            content_sha256=digest,
            snapshot_required=True,
            snapshot_present=True,
            dedupe_key=f"import-source:{locator}",
        ),
        excerpt_text="same URL private capture",
        version=CapturedVersionCandidate(
            version_kind="web",
            version_key=f"web:{digest}",
            content_sha256=digest,
            canonical_locator=locator,
            snapshot_policy="extracted_text",
            snapshot_bytes=content,
            media_type="text/plain",
            byte_count=len(content),
            parser_name="authorization-test",
            parser_version="1",
        ),
    )


def _assert_safe_denial(response) -> None:
    assert response.status_code == 403
    assert response.json()["detail"] == (
        "referenced record is not available for this mutation"
    )


def _exercise_two_user_isolation(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(settings)
    service = app.state.service
    client = TestClient(app)
    suffix = uuid4().hex
    alice_headers = _issued_headers(service, f"alice-{suffix}")
    bob_headers = _issued_headers(service, f"bob-{suffix}")
    admin_headers = _issued_headers(service, f"admin-{suffix}", admin=True)
    alice_namespace = f"alice-{suffix}"
    bob_namespace = f"bob-{suffix}"
    alice = _create_graph(
        client, alice_headers, alice_namespace, f"alice-{suffix}"
    )
    bob = _create_graph(
        client, bob_headers, bob_namespace, f"bob-{suffix}"
    )

    cross_writes = [
        (
            "/api/sessions",
            {
                "question_id": alice["question"],
                "namespace_kind": "user",
                "namespace_id": bob_namespace,
            },
        ),
        (
            "/api/excerpts",
            {
                "source_id": alice["source"],
                "question_id": alice["question"],
                "focal_label": "cross namespace",
                "note": "must fail",
                "selector": {"exact": "must fail"},
                "quote_text": "must fail",
                "namespace_kind": "user",
                "namespace_id": bob_namespace,
            },
        ),
        (
            "/api/claims",
            {
                "question_id": bob["question"],
                "title": "Cross namespace claim",
                "focal_label": "cross namespace",
                "statement": "must fail",
                "excerpt_ids": [alice["excerpt"]],
                "namespace_kind": "user",
                "namespace_id": bob_namespace,
            },
        ),
        (
            "/api/reports",
            {
                "question_id": bob["question"],
                "title": "Cross namespace report",
                "focal_label": "cross namespace",
                "summary_md": "must fail",
                "claim_ids": [alice["claim"]],
                "namespace_kind": "user",
                "namespace_id": bob_namespace,
            },
        ),
    ]
    for path, body in cross_writes:
        _assert_safe_denial(
            client.post(path, headers=bob_headers, json=body)
        )

    _assert_safe_denial(
        client.post(
            f"/api/questions/{alice['question']}/status",
            headers=bob_headers,
            json={"status": "answered"},
        )
    )
    _assert_safe_denial(
        client.post(
            f"/api/follow-ups/{alice['question']}/status",
            headers=bob_headers,
            json={"follow_up_status": "done"},
        )
    )

    for kind in ("source", "excerpt", "claim", "report"):
        _assert_safe_denial(
            client.post(
                "/api/publish",
                headers=bob_headers,
                json={"kind": kind, "record_id": alice[kind]},
            )
        )

    admin_session = _post(
        client,
        "/api/sessions",
        admin_headers,
        {
            "question_id": alice["question"],
            "namespace_kind": "user",
            "namespace_id": alice_namespace,
        },
    )
    assert admin_session["namespace_id"] == alice_namespace
    admin_status_without_namespace = client.post(
        f"/api/questions/{alice['question']}/status",
        headers=admin_headers,
        json={"status": "open"},
    )
    assert admin_status_without_namespace.status_code == 403
    assert admin_status_without_namespace.json()["detail"] == (
        "admin mutation requires an explicit namespace"
    )
    admin_status = client.post(
        f"/api/questions/{alice['question']}/status",
        headers=admin_headers,
        json={
            "status": "answered",
            "namespace_kind": "user",
            "namespace_id": alice_namespace,
        },
    )
    assert admin_status.status_code == 200
    _assert_safe_denial(
        client.post(
            "/api/reports",
            headers=admin_headers,
            json={
                "question_id": alice["question"],
                "title": "Mixed admin report",
                "focal_label": "cross namespace",
                "summary_md": "must fail",
                "claim_ids": [bob["claim"]],
                "namespace_kind": "user",
                "namespace_id": alice_namespace,
            },
        )
    )
    missing_admin_namespace = client.post(
        "/api/publish",
        headers=admin_headers,
        json={"kind": "report", "record_id": alice["report"]},
    )
    assert missing_admin_namespace.status_code == 403
    assert missing_admin_namespace.json()["detail"] == (
        "admin publish requires an explicit namespace"
    )
    published = client.post(
        "/api/publish",
        headers=admin_headers,
        json={
            "kind": "report",
            "record_id": alice["report"],
            "namespace_kind": "user",
            "namespace_id": alice_namespace,
        },
    )
    assert published.status_code == 200

    locator = f"https://example.test/same-url-{suffix}"
    candidate = _captured_candidate(locator)
    monkeypatch.setattr(
        "research_registry.service.fetch_url_candidate",
        lambda _: candidate,
    )
    alice_import = _post(
        client,
        "/api/import/url",
        alice_headers,
        {
            "url": locator,
            "question_id": alice["question"],
            "namespace_kind": "user",
            "namespace_id": alice_namespace,
        },
    )
    bob_import = _post(
        client,
        "/api/import/url",
        bob_headers,
        {
            "url": locator,
            "question_id": bob["question"],
            "namespace_kind": "user",
            "namespace_id": bob_namespace,
        },
    )
    assert alice_import["source_ids"] != bob_import["source_ids"]
    assert alice_import["excerpt_ids"] != bob_import["excerpt_ids"]
    with service.connect() as conn:
        imported = conn.execute(
            """
            SELECT s.id AS source_id, s.namespace_id, s.dedupe_key,
                   sv.id AS version_id, e.id AS evidence_id
            FROM sources s
            JOIN source_versions sv ON sv.source_id = s.id
            JOIN evidence_spans e ON e.source_version_id = sv.id
            WHERE s.id IN (?, ?)
            ORDER BY s.namespace_id
            """,
            (
                alice_import["source_ids"][0],
                bob_import["source_ids"][0],
            ),
        ).fetchall()
    assert len(imported) == 2
    assert len({row["source_id"] for row in imported}) == 2
    assert len({row["version_id"] for row in imported}) == 2
    assert len({row["evidence_id"] for row in imported}) == 2
    assert len({row["dedupe_key"] for row in imported}) == 2
    assert {row["namespace_id"] for row in imported} == {
        alice_namespace,
        bob_namespace,
    }


def _exercise_create_governance_and_admin_publish(
    settings: Settings,
) -> None:
    app = create_app(settings)
    service = app.state.service
    run_v2_backfill(service.database.url)
    client = TestClient(app)
    with service.connect() as conn:
        initial_review_events = conn.execute(
            "SELECT COUNT(*) AS count FROM review_events"
        ).fetchone()["count"]
        initial_publish_audits = conn.execute(
            "SELECT COUNT(*) AS count FROM audit_log WHERE action = 'publish'"
        ).fetchone()["count"]
        initial_approvals = conn.execute(
            """
            SELECT COUNT(*) AS count FROM review_events
            WHERE action = 'approve'
            """
        ).fetchone()["count"]
    suffix = uuid4().hex
    namespace_id = f"governed-{suffix}"
    ingest_headers = _issued_headers(service, namespace_id)
    admin_headers = {"x-admin-token": "secret"}
    escalated = {
        "visibility": "public",
        "review_state": "reviewed",
        "trust_tier": "high",
        "conflict_state": "conflicted",
    }

    question = _post(
        client,
        "/api/questions",
        ingest_headers,
        {
            "prompt": f"Governed creation {suffix}",
            "focus": {"domain": "governance", "object": suffix},
            **escalated,
            "namespace_kind": "user",
            "namespace_id": namespace_id,
        },
    )
    session = _post(
        client,
        "/api/sessions",
        ingest_headers,
        {
            "question_id": question["id"],
            **escalated,
            "namespace_kind": "user",
            "namespace_id": namespace_id,
        },
    )
    source = _post(
        client,
        "/api/sources",
        ingest_headers,
        {
            "locator": f"https://example.test/governed-{suffix}",
            "title": "Governed source",
            "snapshot_present": True,
            **escalated,
            "namespace_kind": "user",
            "namespace_id": namespace_id,
        },
    )
    excerpt = _post(
        client,
        "/api/excerpts",
        ingest_headers,
        {
            "source_id": source["id"],
            "question_id": question["id"],
            "session_id": session["id"],
            "focal_label": "governance",
            "note": "Governed evidence.",
            "selector": {"exact": "governed evidence"},
            "quote_text": "governed evidence",
            **escalated,
            "namespace_kind": "user",
            "namespace_id": namespace_id,
        },
    )
    claim = _post(
        client,
        "/api/claims",
        ingest_headers,
        {
            "question_id": question["id"],
            "session_id": session["id"],
            "title": "Governed claim",
            "focal_label": "governance",
            "statement": "Governed evidence supports the claim.",
            "excerpt_ids": [excerpt["id"]],
            **escalated,
            "namespace_kind": "user",
            "namespace_id": namespace_id,
        },
    )
    report = _post(
        client,
        "/api/reports",
        ingest_headers,
        {
            "question_id": question["id"],
            "session_id": session["id"],
            "title": "Governed report",
            "focal_label": "governance",
            "summary_md": "Governed report.",
            "claim_ids": [claim["id"]],
            **escalated,
            "namespace_kind": "user",
            "namespace_id": namespace_id,
        },
    )

    for record in (question, session, source, excerpt, claim, report):
        assert record["visibility"] == "private"
        assert record["public_index_state"] == "private"
    for record in (source, excerpt, claim, report):
        assert record["review_state"] == "unreviewed"
        assert record["conflict_state"] == "none"
        assert record["trust_tier"] == "low"
    assert excerpt["human_reviewed"] is False
    assert claim["human_reviewed"] is False
    assert report["human_reviewed"] is False

    with service.connect() as conn:
        review_events = conn.execute(
            "SELECT COUNT(*) AS count FROM review_events"
        ).fetchone()["count"]
        publish_audits = conn.execute(
            "SELECT COUNT(*) AS count FROM audit_log WHERE action = 'publish'"
        ).fetchone()["count"]
        evidence_state = conn.execute(
            """
            SELECT e.review_state
            FROM evidence_spans e
            JOIN legacy_projection_identity p
              ON p.v2_kind = 'evidence'
             AND p.v2_id = e.id
            WHERE p.legacy_kind = 'excerpt' AND p.legacy_id = ?
            """,
            (excerpt["id"],),
        ).fetchone()
        revision_state = conn.execute(
            """
            SELECT c.review_state
            FROM claims c
            WHERE c.id = ?
            """,
            (claim["id"],),
        ).fetchone()
    assert review_events == initial_review_events
    assert publish_audits == initial_publish_audits
    assert evidence_state["review_state"] == "unreviewed"
    assert revision_state["review_state"] == "unreviewed"
    assert client.get(
        "/api/search", params={"q": "Governed", "global_index_only": "true"}
    ).json()["hits"] == []

    reviewed = client.post(
        "/api/review",
        headers=admin_headers,
        json={"kind": "claim", "record_id": claim["id"], "reviewed": True},
    )
    assert reviewed.status_code == 200

    for kind, record in (
        ("source", source),
        ("excerpt", excerpt),
        ("claim", claim),
        ("report", report),
    ):
        published = client.post(
            f"/admin/{kind}/{record['id']}/publish",
            headers={**admin_headers, "referer": "/admin"},
            data={"confirm": "yes"},
            follow_redirects=False,
        )
        assert published.status_code == 303, published.text

    with service.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM audit_log WHERE action = 'publish'"
        ).fetchone()["count"] == initial_publish_audits + 4
        assert conn.execute(
            """
            SELECT COUNT(*) AS count FROM review_events
            WHERE action = 'approve'
            """
        ).fetchone()["count"] == initial_approvals + 1

    foreign_namespace = f"foreign-{suffix}"
    foreign_headers = _issued_headers(service, foreign_namespace)
    foreign = _create_graph(
        client,
        foreign_headers,
        foreign_namespace,
        f"foreign-{suffix}",
    )
    with service.connect() as conn:
        conn.execute(
            """
            INSERT INTO claim_excerpts (claim_id, excerpt_id, rationale, weight)
            VALUES (?, ?, ?, ?)
            """,
            (claim["id"], foreign["excerpt"], None, 1.0),
        )
    mixed = client.post(
        f"/admin/claim/{claim['id']}/publish",
        headers={**admin_headers, "referer": "/admin"},
        data={"confirm": "yes"},
        follow_redirects=False,
    )
    assert mixed.status_code == 403
    assert mixed.headers["content-type"].startswith("text/html")
    assert "Record was not published" in mixed.text
    assert foreign_namespace not in mixed.text


def test_shared_http_two_user_mutation_isolation_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "shared-auth.sqlite3"
    _exercise_two_user_isolation(
        _settings(tmp_path, f"sqlite:///{database.resolve()}"),
        monkeypatch,
    )


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="PostgreSQL shared authorization parity requires TEST_DATABASE_URL",
)
def test_shared_http_two_user_mutation_isolation_postgres(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _exercise_two_user_isolation(
        _settings(tmp_path, os.environ["TEST_DATABASE_URL"]),
        monkeypatch,
    )


def test_authenticated_create_governance_and_admin_publish_sqlite(
    tmp_path: Path,
) -> None:
    database = tmp_path / "create-governance.sqlite3"
    _exercise_create_governance_and_admin_publish(
        _settings(tmp_path, f"sqlite:///{database.resolve()}")
    )


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="PostgreSQL retained create governance parity requires TEST_DATABASE_URL",
)
def test_authenticated_create_governance_and_admin_publish_postgres(
    tmp_path: Path,
) -> None:
    _exercise_create_governance_and_admin_publish(
        _settings(tmp_path, os.environ["TEST_DATABASE_URL"])
    )
