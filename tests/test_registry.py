from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from research_registry.app import create_app
from research_registry.config import Settings
from research_registry.models import (
    ApiKeyCreate,
    ClaimCreate,
    FocusTuple,
    ExcerptCreate,
    IndexStateRequest,
    PublishRequest,
    QuestionCreate,
    ReportCreate,
    ResearchSessionCreate,
    ReviewRequest,
    SourceCreate,
    SourceSelector,
)
from research_registry.service import RegistryService


def make_service(tmp_path: Path) -> RegistryService:
    service = RegistryService(tmp_path / "test.sqlite3")
    service.initialize()
    return service


def make_settings(tmp_path: Path, *, public_base_url: str = "https://registry.example.com") -> Settings:
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
        default_backend_url=public_base_url,
        backend_url=None,
        backend_api_key=None,
        backend_org=None,
        backend_profile=None,
        public_base_url=public_base_url,
    )


def test_question_claim_report_roundtrip_search_and_public_visibility(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    focus = FocusTuple(domain="memory-retrieval", object="branch-private memory isolation", context="choose-game")
    question = service.create_question(QuestionCreate(prompt="Research branch-private memory isolation.", focus=focus))
    source = service.create_source(SourceCreate(locator="https://example.com/branch-private", title="Branch isolation note", snippet="branch private isolation", snapshot_present=True))
    excerpt = service.create_excerpt(
        ExcerptCreate(
            source_id=source.id,
            question_id=question.id,
            focal_label=focus.label or "branch-private memory isolation",
            note="Anchored branch-private evidence.",
            selector=SourceSelector(exact="branch private isolation", deep_link="https://example.com/branch-private#1"),
            quote_text="branch private isolation",
        )
    )
    claim = service.create_claim(
        ClaimCreate(
            question_id=question.id,
            title="Branch-private isolation is explicit",
            focal_label=focus.label or "branch-private memory isolation",
            statement="The source supports branch-private memory isolation as a concrete design surface.",
            excerpt_ids=[excerpt.id],
        )
    )
    report = service.create_report(
        ReportCreate.model_validate(
            {
                "question_id": question.id,
                "title": question.prompt,
                "subject": focus.label,
                "summary_md": "# Branch-private isolation\n\nThe source supports a concrete isolation requirement.",
                "finding_ids": [claim.id],
            }
        )
    )

    assert report.claim_ids == [claim.id]
    assert report.source_ids == [source.id]
    assert service.get_question(question.id, include_private=True).status == "answered"

    public_hits = service.search("branch-private", kind="claim", include_private=False)
    assert public_hits.hits == []

    service.publish(PublishRequest(kind="claim", record_id=claim.id))
    public_hits = service.search("branch-private", kind="claim", include_private=False)
    assert [hit.id for hit in public_hits.hits] == [claim.id]

    service.review(ReviewRequest(kind="claim", record_id=claim.id))
    assert service.get_claim(claim.id, include_private=True).human_reviewed is True


def test_api_key_isolation_and_public_namespace_vs_global_index(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    service = app.state.service

    alice_key = service.issue_api_key(ApiKeyCreate(label="alice", actor_user_id="alice"))
    bob_key = service.issue_api_key(ApiKeyCreate(label="bob", actor_user_id="bob"))

    focus = FocusTuple(domain="memory-retrieval", object="typed-anchor memory")
    question_response = client.post(
        "/api/questions",
        headers={"x-api-key": alice_key.token},
        json=QuestionCreate(prompt="Research typed-anchor memory models.", focus=focus).model_dump(mode="json"),
    )
    assert question_response.status_code == 200
    question_id = question_response.json()["id"]

    source_response = client.post(
        "/api/sources",
        headers={"x-api-key": alice_key.token},
        json=SourceCreate(locator="https://example.com/typed-anchor", title="Typed anchor note", snapshot_present=True).model_dump(mode="json"),
    )
    assert source_response.status_code == 200
    source_id = source_response.json()["id"]

    excerpt_response = client.post(
        "/api/excerpts",
        headers={"x-api-key": alice_key.token},
        json=ExcerptCreate(
            source_id=source_id,
            question_id=question_id,
            focal_label=focus.label or "typed-anchor memory",
            note="Private typed-anchor note.",
            selector=SourceSelector(exact="typed anchor", deep_link="https://example.com/typed-anchor#1"),
            quote_text="typed anchor",
        ).model_dump(mode="json"),
    )
    assert excerpt_response.status_code == 200
    excerpt_id = excerpt_response.json()["id"]

    claim_response = client.post(
        "/api/claims",
        headers={"x-api-key": alice_key.token},
        json=ClaimCreate(
            question_id=question_id,
            title="Typed anchors are explicit",
            focal_label=focus.label or "typed-anchor memory",
            statement="Typed anchors are explicitly represented in the stored evidence.",
            excerpt_ids=[excerpt_id],
        ).model_dump(mode="json"),
    )
    assert claim_response.status_code == 200
    claim_id = claim_response.json()["id"]

    assert client.get(f"/api/claims/{claim_id}").status_code == 404
    assert client.get(f"/api/claims/{claim_id}", headers={"x-api-key": bob_key.token}, params={"include_private": "true"}).status_code == 404
    assert client.get(f"/api/claims/{claim_id}", headers={"x-api-key": alice_key.token}, params={"include_private": "true"}).status_code == 200

    publish_response = client.post(
        "/api/publish",
        headers={"x-api-key": alice_key.token},
        json=PublishRequest(kind="claim", record_id=claim_id).model_dump(mode="json"),
    )
    assert publish_response.status_code == 200

    namespace_search = client.get("/api/search", params={"q": "typed-anchor", "namespace_slug": "alice", "kind": "claim"})
    assert namespace_search.status_code == 200
    assert [hit["id"] for hit in namespace_search.json()["hits"]] == [claim_id]

    global_search = client.get("/api/search", params={"q": "typed-anchor"})
    assert global_search.status_code == 200
    assert global_search.json()["hits"] == []

    index_response = client.post(
        "/api/index-state",
        headers={"x-admin-token": "secret"},
        json=IndexStateRequest(kind="claim", record_id=claim_id, state="included").model_dump(mode="json"),
    )
    assert index_response.status_code == 200

    global_search = client.get("/api/search", params={"q": "typed-anchor"})
    assert [hit["id"] for hit in global_search.json()["hits"]] == [claim_id]

    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"

    org_response = client.post(
        "/api/admin/organizations",
        headers={"x-admin-token": "secret"},
        json={"org_id": "acme", "display_name": "Acme"},
    )
    assert org_response.status_code == 200
    assert org_response.json()["id"] == "acme"

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
    assert key_response.json()["token"].startswith("rrk_")
    assert key_response.json()["record"]["namespace_kind"] == "org"


def test_empty_pages_include_onboarding_guidance(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)

    home = client.get("/")
    assert home.status_code == 200
    assert "How To Get Value From A New Registry" in home.text
    assert "research-registry-local-status" in home.text
    assert "Publish only the reusable parts" in home.text

    login = client.get("/admin/login")
    assert login.status_code == 200
    assert "~/.config/research-registry/config.toml" in login.text

    workspace = client.get("/admin", headers={"x-admin-token": "secret"})
    assert workspace.status_code == 200
    assert "Private Workspace Is Empty" in workspace.text
    assert "research-registry-seed-memory-retrieval" in workspace.text


def test_search_ranks_fresh_reports_above_stale_reports(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    focus = FocusTuple(domain="memory-retrieval", object="retrieval freshness")

    stale_question = service.create_question(QuestionCreate(prompt="Research stale retrieval freshness handling.", focus=focus))
    stale_session = service.create_session(
        ResearchSessionCreate(
            question_id=stale_question.id,
            prompt=stale_question.prompt,
            model_name="gpt-5.4",
            model_version="2026-04-10",
            mode="live_research",
            ttl_days=1,
        )
    )
    stale_source = service.create_source(SourceCreate(locator="https://example.com/stale", title="Stale note", snippet="stale freshness", snapshot_present=True))
    stale_excerpt = service.create_excerpt(
        ExcerptCreate(
            source_id=stale_source.id,
            question_id=stale_question.id,
            session_id=stale_session.id,
            focal_label=focus.label or "retrieval freshness",
            note="stale evidence",
            selector=SourceSelector(exact="stale freshness", deep_link="https://example.com/stale#1"),
            quote_text="stale freshness",
        )
    )
    stale_claim = service.create_claim(
        ClaimCreate(
            question_id=stale_question.id,
            session_id=stale_session.id,
            title="Stale freshness guidance",
            focal_label=focus.label or "retrieval freshness",
            statement="Stale retrieval freshness guidance exists.",
            excerpt_ids=[stale_excerpt.id],
        )
    )
    stale_report = service.create_report(
        ReportCreate(
            question_id=stale_question.id,
            session_id=stale_session.id,
            title=stale_question.prompt,
            focal_label=focus.label or "retrieval freshness",
            summary_md="# stale\n",
            claim_ids=[stale_claim.id],
        )
    )
    with service.connect() as conn:
        conn.execute(
            "UPDATE research_sessions SET expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", stale_session.id),
        )

    fresh_question = service.create_question(QuestionCreate(prompt="Research fresh retrieval freshness handling.", focus=focus))
    fresh_session = service.create_session(
        ResearchSessionCreate(
            question_id=fresh_question.id,
            prompt=fresh_question.prompt,
            model_name="gpt-5.4",
            model_version="2026-04-10",
            mode="live_research",
            ttl_days=30,
        )
    )
    fresh_source = service.create_source(SourceCreate(locator="https://example.com/fresh", title="Fresh note", snippet="fresh freshness", snapshot_present=True))
    fresh_excerpt = service.create_excerpt(
        ExcerptCreate(
            source_id=fresh_source.id,
            question_id=fresh_question.id,
            session_id=fresh_session.id,
            focal_label=focus.label or "retrieval freshness",
            note="fresh evidence",
            selector=SourceSelector(exact="fresh freshness", deep_link="https://example.com/fresh#1"),
            quote_text="fresh freshness",
        )
    )
    fresh_claim = service.create_claim(
        ClaimCreate(
            question_id=fresh_question.id,
            session_id=fresh_session.id,
            title="Fresh freshness guidance",
            focal_label=focus.label or "retrieval freshness",
            statement="Fresh retrieval freshness guidance exists.",
            excerpt_ids=[fresh_excerpt.id],
        )
    )
    fresh_report = service.create_report(
        ReportCreate(
            question_id=fresh_question.id,
            session_id=fresh_session.id,
            title=fresh_question.prompt,
            focal_label=focus.label or "retrieval freshness",
            summary_md="# fresh\n",
            claim_ids=[fresh_claim.id],
        )
    )

    hits = service.search("retrieval freshness guidance", kind="report", include_private=True).hits
    assert [hit.id for hit in hits[:2]] == [fresh_report.id, stale_report.id]
    assert hits[0].is_stale is False
    assert hits[1].is_stale is True


def test_registry_service_accepts_sqlite_database_url(tmp_path: Path) -> None:
    db_path = tmp_path / "dsn.sqlite3"
    service = RegistryService(f"sqlite:///{db_path.resolve()}")
    service.initialize()
    assert service.database.kind == "sqlite"
    assert service.database.sqlite_path == db_path.resolve()
