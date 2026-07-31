from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from research_registry import __version__
from research_registry.app import create_app
from research_registry.config import Settings
from research_registry.external_ingest import ImportedSourceCandidate
from research_registry.local_research import (
    LocalClaimDraft,
    LocalEvidenceHit,
    LocalFollowUpDraft,
    LocalGuidanceDraft,
    LocalResearchResult,
)
from research_registry.mcp_tools import create_mcp_server
from research_registry.application.migrate_v2 import run_v2_backfill
from research_registry.application.review import ResearchReviewService
from research_registry.persistence.read_adapter import CurrentRetrievalAdapter, ReadAccess
from research_registry.models import (
    ApiKeyCreate,
    AuthContext,
    BriefResolveRequest,
    ClaimCreate,
    ExcerptCreate,
    FocusTuple,
    ImportUrlRequest,
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
from tests.fixtures.v2_review import seed_review_registry


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
    focus = FocusTuple(domain="memory-retrieval", object="branch-private memory isolation", context="branch-sandbox")
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


def test_retained_http_review_appends_one_attributed_v2_event(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    service = app.state.service
    _, ids = seed_review_registry(
        tmp_path,
        key="retained-http-review",
        database=service.database.url,
    )
    issued = service.issue_api_key(
        ApiKeyCreate(
            label="legacy-review-admin",
            actor_user_id="reviewer",
            scopes=["admin", "read_private"],
        )
    )
    headers = {"x-api-key": issued.token}
    payload = {
        "kind": "claim",
        "record_id": ids["claim"],
        "reviewed": True,
    }

    first = client.post("/api/review", headers=headers, json=payload)
    replay = client.post("/api/review", headers=headers, json=payload)
    reversal = client.post(
        "/api/review",
        headers=headers,
        json={**payload, "reviewed": False},
    )
    report_review = client.post(
        "/api/review",
        headers=headers,
        json={
            "kind": "report",
            "record_id": ids["report"],
            "reviewed": True,
        },
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert reversal.status_code == 403
    assert report_review.status_code == 200
    with service.connect() as conn:
        events = conn.execute(
            """
            SELECT * FROM review_events
            WHERE entity_kind = 'claim_revision' AND entity_id = ?
            """,
            (ids["revision"],),
        ).fetchall()
        audit = conn.execute(
            """
            SELECT * FROM audit_log
            WHERE action = 'review' AND record_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (ids["claim"],),
        ).fetchone()
        report_event = conn.execute(
            """
            SELECT * FROM review_events
            WHERE entity_kind = 'report' AND entity_id = ?
            """,
            (ids["report"],),
        ).fetchone()
    assert len(events) == 1
    assert events[0]["action"] == "approve"
    assert events[0]["actor_id"] == issued.record.id
    assert audit is not None and audit["api_key_id"] == issued.record.id
    assert report_event is not None
    assert report_event["actor_id"] == issued.record.id


def test_retained_review_can_approve_a_later_contested_revision(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    service = app.state.service
    _, ids = seed_review_registry(
        tmp_path,
        key="retained-review-after-contest",
        database=service.database.url,
    )
    issued = service.issue_api_key(
        ApiKeyCreate(
            label="revision-aware-review-admin",
            actor_user_id="reviewer",
            scopes=["admin", "read_private"],
        )
    )
    headers = {"x-api-key": issued.token}
    retained_payload = {
        "kind": "claim",
        "record_id": ids["claim"],
        "reviewed": True,
    }

    first = client.post("/api/review", headers=headers, json=retained_payload)
    assert first.status_code == 200
    contested = ResearchReviewService(service.database).review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": "contest-after-retained-approve",
            "entity": {
                "kind": "claim_revision",
                "id": ids["revision"],
            },
            "action": "contest",
            "expected_revision_id": ids["revision"],
            "expected_state": "reviewed",
        }
    )
    assert contested.current_revision_id != ids["revision"]

    second = client.post("/api/review", headers=headers, json=retained_payload)
    replay = client.post("/api/review", headers=headers, json=retained_payload)
    reversal = client.post(
        "/api/review",
        headers=headers,
        json={**retained_payload, "reviewed": False},
    )

    assert second.status_code == 200
    assert replay.status_code == 200
    assert reversal.status_code == 403
    with service.connect() as conn:
        events = conn.execute(
            """
            SELECT entity_id, action
            FROM review_events
            WHERE entity_kind = 'claim_revision'
            ORDER BY created_at, id
            """
        ).fetchall()
        current = conn.execute(
            """
            SELECT current_revision_id, review_state
            FROM claims WHERE id = ?
            """,
            (ids["claim"],),
        ).fetchone()
    assert sorted(
        (row["entity_id"], row["action"]) for row in events
    ) == sorted(
        [
            (ids["revision"], "approve"),
            (contested.current_revision_id, "contest"),
            (contested.current_revision_id, "approve"),
        ]
    )
    assert current["current_revision_id"] == contested.current_revision_id
    assert current["review_state"] == "reviewed"


def test_retained_evidence_review_uses_append_only_effective_state(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    service = app.state.service
    _, ids = seed_review_registry(
        tmp_path,
        key="retained-evidence-review-after-contest",
        database=service.database.url,
    )
    issued = service.issue_api_key(
        ApiKeyCreate(
            label="evidence-review-admin",
            actor_user_id="reviewer",
            scopes=["admin", "read_private"],
        )
    )
    headers = {"x-api-key": issued.token}
    retained_payload = {
        "kind": "excerpt",
        "record_id": ids["excerpt"],
        "reviewed": True,
    }

    first = client.post(
        "/api/review", headers=headers, json=retained_payload
    )
    contested = ResearchReviewService(service.database).review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": "contest-after-retained-evidence-approve",
            "entity": {
                "kind": "evidence",
                "id": ids["supporting"],
            },
            "action": "contest",
            "expected_state": "reviewed",
        }
    )
    second = client.post(
        "/api/review", headers=headers, json=retained_payload
    )
    replay = client.post(
        "/api/review", headers=headers, json=retained_payload
    )

    assert first.status_code == 200
    assert contested.event_id
    assert second.status_code == 200
    assert replay.status_code == 200
    with service.connect() as conn:
        events = conn.execute(
            """
            SELECT action, from_state, to_state
            FROM review_events
            WHERE entity_kind = 'evidence' AND entity_id = ?
            ORDER BY created_at, id
            """,
            (ids["supporting"],),
        ).fetchall()
        evidence = conn.execute(
            "SELECT review_state FROM evidence_spans WHERE id = ?",
            (ids["supporting"],),
        ).fetchone()
        excerpt = conn.execute(
            """
            SELECT review_state, human_reviewed
            FROM excerpts WHERE id = ?
            """,
            (ids["excerpt"],),
        ).fetchone()
    effective = CurrentRetrievalAdapter(service.database).get_record(
        ids["supporting"],
        access=ReadAccess(include_private=True, local_trusted=True),
    )

    assert [
        (row["action"], row["from_state"], row["to_state"])
        for row in events
    ] == [
        ("approve", "unreviewed", "reviewed"),
        ("contest", "reviewed", "flagged"),
        ("approve", "flagged", "reviewed"),
    ]
    assert evidence["review_state"] == "unreviewed"
    assert (excerpt["review_state"], excerpt["human_reviewed"]) == (
        "reviewed",
        1,
    )
    assert effective is not None
    assert effective.review_state == "reviewed"


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


def test_create_question_accepts_legacy_subject_and_string_focus(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)
    service = app.state.service

    api_key = service.issue_api_key(ApiKeyCreate(label="writer", actor_user_id="alice"))

    response = client.post(
        "/api/questions",
        headers={"x-api-key": api_key.token},
        json={
            "subject": "Star Wars ship template expansion and layout-feel guidance for ScalaGalaxies",
            "focus": "catalog metadata, origin worlds, hull role coverage, and interior topology heuristics for ship templates",
            "visibility": "private",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["prompt"] == "Star Wars ship template expansion and layout-feel guidance for ScalaGalaxies"
    assert payload["focus"]["label"] == "catalog metadata, origin worlds, hull role coverage, and interior topology heuristics for ship templates"
    assert payload["visibility"] == "private"


def test_retained_v1_http_writes_are_immediately_projected_into_v2(
    tmp_path: Path,
) -> None:
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)
    service = app.state.service
    assert run_v2_backfill(service.database.label).status == "completed"
    issued = service.issue_api_key(
        ApiKeyCreate(label="compat-writer", actor_user_id="compat-user")
    )
    headers = {"x-api-key": issued.token}

    question = client.post(
        "/api/questions",
        headers=headers,
        json=QuestionCreate(
            prompt="Does retained v1 HTTP dual-write?",
            focus=FocusTuple(domain="compatibility", object="v1 dual write"),
        ).model_dump(mode="json"),
    ).json()
    source = client.post(
        "/api/sources",
        headers=headers,
        json=SourceCreate(
            locator="note:v1-v2-compat",
            title="V1 compatibility source",
            content_sha256="a" * 64,
        ).model_dump(mode="json"),
    ).json()
    excerpt = client.post(
        "/api/excerpts",
        headers=headers,
        json=ExcerptCreate(
            source_id=source["id"],
            question_id=question["id"],
            focal_label="v1 dual write",
            note="Immediate projection evidence.",
            selector=SourceSelector(exact="immediate projection"),
            quote_text="immediate projection",
        ).model_dump(mode="json"),
    ).json()
    claim = client.post(
        "/api/claims",
        headers=headers,
        json=ClaimCreate(
            question_id=question["id"],
            title="V1 writes project immediately",
            focal_label="v1 dual write",
            statement="The retained v1 path creates a v2 revision and evidence link.",
            excerpt_ids=[excerpt["id"]],
        ).model_dump(mode="json"),
    ).json()
    report = client.post(
        "/api/reports",
        headers=headers,
        json=ReportCreate(
            question_id=question["id"],
            title="Compatibility report",
            focal_label="v1 dual write",
            summary_md="The retained write path is projected.",
            claim_ids=[claim["id"]],
        ).model_dump(mode="json"),
    )
    assert report.status_code == 200

    adapter = CurrentRetrievalAdapter(service.database)
    access = ReadAccess(
        include_private=True,
        namespace_kind="user",
        namespace_id="compat-user",
    )
    hydrated = adapter.get_record(claim["id"], access=access)
    assert hydrated is not None
    revision = adapter.get_current_revision(claim["id"])
    assert revision is not None
    evidence = adapter.list_evidence(hydrated, access=access)
    assert len(evidence) == 1
    assert evidence[0]["quote_text"] == "immediate projection"


def test_agent_shaped_create_payloads_normalize() -> None:
    question = QuestionCreate.model_validate(
        {
            "text": "Research Spyglass artifact cleanup failures.",
            "focus_label": "Spyglass cleanup",
        }
    )
    assert question.prompt == "Research Spyglass artifact cleanup failures."
    assert question.focus and question.focus.label == "Spyglass cleanup"

    session = ResearchSessionCreate.model_validate(
        {
            "question_id": "q_123",
            "title": "Spyglass cleanup investigation",
            "mode": "implicit",
        }
    )
    assert session.prompt == "Spyglass cleanup investigation"
    assert session.model_name == "unspecified"
    assert session.model_version == "unspecified"
    assert session.mode == "live_research"

    source = SourceCreate.model_validate({"url": "https://example.com/build.log", "label": "Build log"})
    assert source.locator == "https://example.com/build.log"
    assert source.title == "Build log"

    excerpt = ExcerptCreate.model_validate(
        {
            "question_id": "q_123",
            "locator": "https://example.com/build.log#L10",
            "title": "Build log excerpt",
            "summary": "The cleanup job failed while publishing artifacts.",
            "selector": "https://example.com/build.log#L10",
            "text": "artifact publish failed",
        }
    )
    assert excerpt.focal_label == "Build log excerpt"
    assert excerpt.note == "The cleanup job failed while publishing artifacts."
    assert excerpt.quote_text == "artifact publish failed"
    assert excerpt.selector.deep_link == "https://example.com/build.log#L10"
    assert excerpt.source and excerpt.source.locator == "https://example.com/build.log#L10"

    claim = ClaimCreate.model_validate(
        {
            "question_id": "q_123",
            "summary": "Artifact publishing failed because the cleanup output shape was not accepted.",
            "subject": "Spyglass cleanup",
            "evidence_excerpt_ids": "ex_123",
        }
    )
    assert claim.title == "Artifact publishing failed because the cleanup output shape was not accepted."
    assert claim.statement == "Artifact publishing failed because the cleanup output shape was not accepted."
    assert claim.excerpt_ids == ["ex_123"]

    report = ReportCreate.model_validate(
        {
            "question_id": "q_123",
            "title": "Spyglass cleanup report",
            "summary_markdown": "# Guidance\n\nUse accepted bundle field names.",
            "focus_label": "Spyglass cleanup",
            "finding_id": "clm_123",
        }
    )
    assert report.summary_md == "# Guidance\n\nUse accepted bundle field names."
    assert report.focal_label == "Spyglass cleanup"
    assert report.claim_ids == ["clm_123"]


def test_mcp_write_tools_expose_typed_payload_schema() -> None:
    mcp = create_mcp_server(  # type: ignore[arg-type]
        object(),
        legacy_tools_enabled=True,
    )

    question_schema = mcp._tool_manager._tools["create_question"].parameters
    question_payload = question_schema["properties"]["payload"]
    assert question_payload == {"$ref": "#/$defs/QuestionCreate"}
    assert "prompt" in question_schema["$defs"]["QuestionCreate"]["properties"]

    report_schema = mcp._tool_manager._tools["create_report"].parameters
    report_payload = report_schema["properties"]["payload"]
    assert report_payload == {"$ref": "#/$defs/ReportCreate"}
    report_model = report_schema["$defs"]["ReportCreate"]
    assert "summary_md" in report_model["properties"]
    assert {"question_id", "title", "focal_label", "summary_md", "claim_ids"}.issubset(set(report_model["required"]))


def test_empty_pages_include_onboarding_guidance(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)

    home = client.get("/")
    assert home.status_code == 200
    assert "How To Get Value From A New Registry" in home.text
    assert "make up" in home.text
    assert "Publish only the reusable parts" in home.text

    login = client.get("/admin/login")
    assert login.status_code == 200
    assert "~/.config/research-registry/config.toml" in login.text

    workspace = client.get("/admin", headers={"x-admin-token": "secret"})
    assert workspace.status_code == 200
    assert "Private Workspace Is Empty" in workspace.text
    assert "make up" in workspace.text


def test_openapi_docs_are_exposed_with_package_version(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)

    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "swagger" in docs.text.lower()

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    body = openapi.json()
    assert body["info"]["title"] == "Research Registry"
    assert body["info"]["version"] == "0.1.0"
    assert __version__ == "0.2.0a1"


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


@pytest.mark.legacy
def test_import_brief_refresh_follow_up_and_source_review_work(tmp_path: Path, monkeypatch) -> None:
    service = make_service(tmp_path)
    auth = AuthContext(
        is_admin=True,
        scopes=["admin", "ingest", "publish", "read_private"],
        namespace_kind="user",
        namespace_id="local",
    )
    focus = FocusTuple(domain="memory-retrieval", object="implicit research reuse", concern="refresh planning")
    question = service.create_question(QuestionCreate(prompt="Research implicit research reuse planning.", focus=focus))
    session = service.create_session(
        ResearchSessionCreate(
            question_id=question.id,
            prompt=question.prompt,
            model_name="gpt-5.4",
            model_version="2026-04-10",
            mode="live_research",
        )
    )
    source = service.create_source(
        SourceCreate(
            locator="https://example.com/reuse-baseline",
            title="Reuse baseline",
            source_type="documentation",
            snippet="reuse baseline evidence",
            snapshot_present=True,
        )
    )
    excerpt = service.create_excerpt(
        ExcerptCreate(
            source_id=source.id,
            question_id=question.id,
            session_id=session.id,
            focal_label=focus.label or "implicit research reuse",
            note="Baseline reuse evidence.",
            selector=SourceSelector(exact="reuse baseline evidence", deep_link="https://example.com/reuse-baseline#1"),
            quote_text="reuse baseline evidence",
        )
    )
    claim = service.create_claim(
        ClaimCreate(
            question_id=question.id,
            session_id=session.id,
            title="Reuse evidence exists",
            focal_label=focus.label or "implicit research reuse",
            statement="Baseline reuse evidence already exists in the registry.",
            excerpt_ids=[excerpt.id],
        )
    )
    report = service.create_report(
        ReportCreate(
            question_id=question.id,
            session_id=session.id,
            title=question.prompt,
            focal_label=focus.label or "implicit research reuse",
            summary_md="# Guidance\n\nReuse evidence already exists.\n",
            claim_ids=[claim.id],
        )
    )

    now = datetime.now(UTC).replace(microsecond=0)

    def fake_fetch_url_candidate(url: str) -> ImportedSourceCandidate:
        return ImportedSourceCandidate(
            source=SourceCreate(
                locator=url,
                title="Imported reuse note",
                source_type="documentation",
                snippet="imported implicit reuse evidence",
                accessed_at=now,
                last_verified_at=now,
                snapshot_required=True,
                snapshot_present=False,
                review_state="unreviewed",
                trust_tier="medium",
                refresh_due_at=now + timedelta(days=30),
            ),
            excerpt_text="imported implicit reuse evidence",
        )

    monkeypatch.setattr("research_registry.service.fetch_url_candidate", fake_fetch_url_candidate)

    imported = service.import_url(
        ImportUrlRequest(url="https://example.com/imported-reuse", question_id=question.id),
        auth=auth,
    )
    assert len(imported.source_ids) == 1
    assert len(imported.excerpt_ids) == 1
    assert service.get_source(imported.source_ids[0], include_private=True).trust_tier == "medium"

    brief = service.resolve_brief(
        BriefResolveRequest(prompt="Research implicit research reuse planning.", include_private=True),
        auth=auth,
    )
    assert [item.id for item in brief.reports] == [report.id]
    assert imported.excerpt_ids[0] in {item.id for item in brief.excerpts}

    def fake_run_local_research(prompt: str, *, domain: str | None = None, **_: object) -> LocalResearchResult:
        return LocalResearchResult(
            focus=FocusTuple(domain=domain, object="implicit research reuse", concern="refresh planning"),
            query_terms=["implicit research reuse", "refresh planning"],
            source_roots=[str(tmp_path)],
            hits=[
                LocalEvidenceHit(
                    source=SourceCreate(
                        locator=str(tmp_path / "notes" / "refresh.md"),
                        title="Refresh evidence",
                        source_type="documentation",
                        snippet="refresh evidence for reuse planning",
                        snapshot_present=True,
                    ),
                    selector=SourceSelector(
                        exact="refresh evidence for reuse planning",
                        deep_link=f"{tmp_path / 'notes' / 'refresh.md'}#L1",
                        start_line=1,
                        end_line=1,
                    ),
                    quote_text="refresh evidence for reuse planning",
                    note="Matched refresh planning evidence.",
                    matched_terms=["refresh planning", "implicit research reuse"],
                    score=9.1,
                    repo_name="llmresearch",
                    file_path=str(tmp_path / "notes" / "refresh.md"),
                )
            ],
            claim_drafts=[
                LocalClaimDraft(
                    title="Refresh planning stays useful",
                    statement="Refresh planning evidence supports keeping reusable reports current.",
                    excerpt_indexes=[0],
                    status="supported",
                    confidence=0.82,
                )
            ],
            guidance=LocalGuidanceDraft(
                current_guidance=["Refresh reusable reports when the evidence window changes."],
                evidence_now=["Refresh evidence exists in local notes."],
                gaps=["Need stronger reranking evidence."],
                needs=["Need a benchmark for stale-vs-fresh reuse recall."],
                wants=["Want a shared import workflow for DOI and URL sources."],
                follow_ups=[
                    LocalFollowUpDraft(
                        prompt="Research stale-vs-fresh reuse recall benchmarks.",
                        reason="need",
                        rationale="Release guidance depends on measurable refresh benefit.",
                        priority_score=0.9,
                    )
                ],
            ),
            gaps=["Need stronger reranking evidence."],
            report_md="# Guidance\n\nRefresh reusable reports when the evidence window changes.\n",
        )

    monkeypatch.setattr("research_registry.service.run_local_research", fake_run_local_research)

    refreshed = service.refresh_report(report.id, auth=auth)
    assert refreshed.id != report.id
    assert refreshed.refresh_of_report_id == report.id
    child_questions = service.list_child_questions(question.id, include_private=True)
    assert len(child_questions) == 1
    assert child_questions[0].follow_up_status == "open"

    service.set_follow_up_status(child_questions[0].id, "ready")
    assert service.get_question(child_questions[0].id, include_private=True).follow_up_status == "ready"

    imported_source = service.get_source(imported.source_ids[0], include_private=True)
    service.review(ReviewRequest(kind="source", record_id=imported_source.id))
    assert service.get_source(imported_source.id, include_private=True).review_state == "reviewed"


def test_publish_blocks_sources_missing_snapshots(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    source = service.create_source(
        SourceCreate(
            locator="https://example.com/needs-snapshot",
            title="Needs snapshot",
            snapshot_required=True,
            snapshot_present=False,
        )
    )

    with pytest.raises(PermissionError):
        service.publish(PublishRequest(kind="source", record_id=source.id))


@pytest.mark.legacy
def test_http_supports_import_brief_follow_up_and_refresh_routes(tmp_path: Path, monkeypatch) -> None:
    app = create_app(make_settings(tmp_path))
    client = TestClient(app)
    service = app.state.service
    issued = service.issue_api_key(ApiKeyCreate(label="writer", actor_user_id="alice"))
    auth_headers = {"x-api-key": issued.token}
    focus = FocusTuple(domain="memory-retrieval", object="route-level refresh")

    question_id = client.post(
        "/api/questions",
        headers=auth_headers,
        json=QuestionCreate(prompt="Research route-level refresh handling.", focus=focus).model_dump(mode="json"),
    ).json()["id"]
    session_id = client.post(
        "/api/sessions",
        headers=auth_headers,
        json=ResearchSessionCreate(
            question_id=question_id,
            prompt="Research route-level refresh handling.",
            model_name="gpt-5.4",
            model_version="2026-04-10",
            mode="live_research",
        ).model_dump(mode="json"),
    ).json()["id"]
    source_id = client.post(
        "/api/sources",
        headers=auth_headers,
        json=SourceCreate(
            locator="https://example.com/route-refresh",
            title="Route refresh baseline",
            snippet="route refresh evidence",
            snapshot_present=True,
        ).model_dump(mode="json"),
    ).json()["id"]
    excerpt_id = client.post(
        "/api/excerpts",
        headers=auth_headers,
        json=ExcerptCreate(
            source_id=source_id,
            question_id=question_id,
            session_id=session_id,
            focal_label=focus.label or "route-level refresh",
            note="route refresh evidence",
            selector=SourceSelector(exact="route refresh evidence", deep_link="https://example.com/route-refresh#1"),
            quote_text="route refresh evidence",
        ).model_dump(mode="json"),
    ).json()["id"]
    claim_id = client.post(
        "/api/claims",
        headers=auth_headers,
        json=ClaimCreate(
            question_id=question_id,
            session_id=session_id,
            title="Route refresh works",
            focal_label=focus.label or "route-level refresh",
            statement="The HTTP routes expose refresh-capable workflows.",
            excerpt_ids=[excerpt_id],
        ).model_dump(mode="json"),
    ).json()["id"]
    report_id = client.post(
        "/api/reports",
        headers=auth_headers,
        json=ReportCreate(
            question_id=question_id,
            session_id=session_id,
            title="Route refresh report",
            focal_label=focus.label or "route-level refresh",
            summary_md="# Guidance\n\nRoute refresh is wired.\n",
            claim_ids=[claim_id],
        ).model_dump(mode="json"),
    ).json()["id"]

    now = datetime.now(UTC).replace(microsecond=0)

    monkeypatch.setattr(
        "research_registry.service.fetch_url_candidate",
        lambda url: ImportedSourceCandidate(
            source=SourceCreate(
                locator=url,
                title="Imported route note",
                source_type="documentation",
                snippet="route import evidence",
                accessed_at=now,
                last_verified_at=now,
                snapshot_required=True,
                snapshot_present=False,
                trust_tier="medium",
                refresh_due_at=now + timedelta(days=30),
            ),
            excerpt_text="route import evidence",
        ),
    )

    import_response = client.post(
        "/api/import/url",
        headers=auth_headers,
        json=ImportUrlRequest(url="https://example.com/route-import", question_id=question_id).model_dump(mode="json"),
    )
    assert import_response.status_code == 200
    assert len(import_response.json()["source_ids"]) == 1

    brief_response = client.post(
        "/api/briefs/resolve",
        headers=auth_headers,
        json=BriefResolveRequest(prompt="Research route-level refresh handling.", include_private=True).model_dump(mode="json"),
    )
    assert brief_response.status_code == 200
    assert [item["id"] for item in brief_response.json()["reports"]] == [report_id]

    monkeypatch.setattr(
        "research_registry.service.run_local_research",
        lambda prompt, *, domain=None, **_: LocalResearchResult(
            focus=FocusTuple(domain=domain, object="route-level refresh"),
            query_terms=["route-level refresh"],
            source_roots=[str(tmp_path)],
            hits=[
                LocalEvidenceHit(
                    source=SourceCreate(
                        locator=str(tmp_path / "route.md"),
                        title="Route evidence",
                        source_type="documentation",
                        snippet="route-level refresh evidence",
                        snapshot_present=True,
                    ),
                    selector=SourceSelector(
                        exact="route-level refresh evidence",
                        deep_link=f"{tmp_path / 'route.md'}#L1",
                        start_line=1,
                        end_line=1,
                    ),
                    quote_text="route-level refresh evidence",
                    note="route-level refresh evidence",
                    matched_terms=["route-level refresh"],
                    score=8.8,
                    repo_name="llmresearch",
                    file_path=str(tmp_path / "route.md"),
                )
            ],
            claim_drafts=[
                LocalClaimDraft(
                    title="Route refresh stays wired",
                    statement="Refresh routes create successor reports and follow-up questions.",
                    excerpt_indexes=[0],
                )
            ],
            guidance=LocalGuidanceDraft(
                current_guidance=["Keep refresh routes available to clients."],
                evidence_now=["Refresh route evidence exists."],
                gaps=["Need client SDK coverage."],
                needs=["Need route regression coverage."],
                wants=["Want preview UI actions."],
                follow_ups=[
                    LocalFollowUpDraft(
                        prompt="Research route regression coverage.",
                        reason="need",
                        rationale="The refresh route should stay stable for clients.",
                        priority_score=0.8,
                    )
                ],
            ),
            gaps=["Need client SDK coverage."],
            report_md="# Guidance\n\nKeep refresh routes available to clients.\n",
        ),
    )

    refresh_response = client.post(f"/api/reports/{report_id}/refresh", headers=auth_headers)
    assert refresh_response.status_code == 200
    refreshed_id = refresh_response.json()["id"]
    assert refreshed_id != report_id

    child_questions = service.list_child_questions(question_id, include_private=True)
    assert len(child_questions) == 1
    follow_up_response = client.post(
        f"/api/follow-ups/{child_questions[0].id}/status",
        headers=auth_headers,
        json={"follow_up_status": "done"},
    )
    assert follow_up_response.status_code == 200
    assert service.get_question(child_questions[0].id, include_private=True).follow_up_status == "done"
