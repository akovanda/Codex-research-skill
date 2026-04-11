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
    ReviewRequest,
    SourceCreate,
    SourceSelector,
)
from research_registry.service import RegistryService


def make_service(tmp_path: Path) -> RegistryService:
    service = RegistryService(tmp_path / "test.sqlite3")
    service.initialize()
    return service


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
    data_dir = tmp_path / "data"
    settings = Settings(
        data_dir=data_dir,
        db_path=tmp_path / "auth.sqlite3",
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
