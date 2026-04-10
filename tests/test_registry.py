from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from research_registry.app import create_app
from research_registry.backend_selection import resolve_backend
from research_registry.config import Settings
from research_registry.models import AnnotationCreate, ApiKeyCreate, FindingCreate, PublishRequest, ReportCompileCreate, ReviewRequest, RunCreate, SourceCreate, SourceSelector
from research_registry.service import RegistryService


def make_service(tmp_path: Path) -> RegistryService:
    service = RegistryService(tmp_path / "test.sqlite3")
    service.initialize()
    return service


def test_annotation_roundtrip_search_and_public_visibility(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    source = service.create_source(SourceCreate(canonical_url="https://example.com/a", title="A zebra note", snapshot_required=True, snapshot_present=True))
    annotation = service.create_annotation(
        AnnotationCreate(
            source_id=source.id,
            subject="zebras",
            note="Zebra stripes were described in a controlled note.",
            selector=SourceSelector(exact="zebra stripes", deep_link="https://example.com/a#1"),
            tags=["zebra"],
        )
    )

    public_hits = service.search("zebra", kind="annotation", include_private=False)
    assert public_hits.hits == []

    service.publish(PublishRequest(kind="annotation", record_id=annotation.id))
    public_hits = service.search("zebra", kind="annotation", include_private=False)
    assert [hit.id for hit in public_hits.hits] == [annotation.id]


def test_same_anchor_keeps_multiple_provenance_records(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    source = service.create_source(
        SourceCreate(
            canonical_url="https://example.com/a",
            title="A zebra passage",
            content_sha256="hash-v1",
            snapshot_required=True,
            snapshot_present=True,
        )
    )
    run_one = service.create_run(RunCreate(question="q1", model_name="gpt", model_version="1"))
    run_two = service.create_run(RunCreate(question="q2", model_name="gpt", model_version="2"))

    annotation_one = service.create_annotation(
        AnnotationCreate(
            source_id=source.id,
            run_id=run_one.id,
            subject="zebra stripes",
            note="First take.",
            selector=SourceSelector(exact="same passage", deep_link="https://example.com/a#2"),
        )
    )
    annotation_two = service.create_annotation(
        AnnotationCreate(
            source_id=source.id,
            run_id=run_two.id,
            subject="zebra stripes",
            note="Second take.",
            selector=SourceSelector(exact="same passage", deep_link="https://example.com/a#2"),
        )
    )

    assert annotation_one.anchor_fingerprint == annotation_two.anchor_fingerprint
    assert annotation_one.id != annotation_two.id
    assert annotation_one.run_id != annotation_two.run_id


def test_report_compile_keeps_finding_annotation_and_source_links(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    source = service.create_source(SourceCreate(canonical_url="https://example.com/a", title="Zebra source", snapshot_required=True, snapshot_present=True))
    annotation = service.create_annotation(
        AnnotationCreate(
            source_id=source.id,
            subject="zebra stripes",
            note="Anchored evidence.",
            selector=SourceSelector(exact="anchored evidence"),
        )
    )
    finding = service.create_finding(
        FindingCreate(
            title="Main zebra claim",
            subject="zebra stripes",
            claim="The source supports one narrow claim.",
            annotation_ids=[annotation.id],
        )
    )
    report = service.compile_report(
        ReportCompileCreate(
            question="What does the source say?",
            subject="zebra stripes",
            finding_ids=[finding.id],
        )
    )

    assert report.finding_ids == [finding.id]
    assert report.annotation_ids == [annotation.id]
    assert report.source_ids == [source.id]
    assert source.canonical_url in report.summary_md


def test_staleness_reasons_cover_source_change_snapshot_missing_and_freshness(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    fresh_source = service.create_source(
        SourceCreate(
            canonical_url="https://example.com/fresh",
            title="Fresh source",
            content_sha256="fresh-hash",
            snapshot_required=True,
            snapshot_present=True,
        )
    )
    changed_source = service.create_source(
        SourceCreate(
            canonical_url="https://example.com/changed",
            title="Changed source",
            content_sha256="old-hash",
            snapshot_required=True,
            snapshot_present=True,
        )
    )
    missing_snapshot_source = service.create_source(
        SourceCreate(
            canonical_url="https://example.com/missing",
            title="Missing snapshot source",
            snapshot_required=True,
            snapshot_present=False,
        )
    )

    expired = service.create_annotation(
        AnnotationCreate(
            source_id=fresh_source.id,
            subject="expired",
            note="This should expire immediately.",
            selector=SourceSelector(exact="expired"),
            freshness_ttl_days=1,
        )
    )
    changed = service.create_annotation(
        AnnotationCreate(
            source_id=changed_source.id,
            subject="changed",
            note="This source will change.",
            selector=SourceSelector(exact="changed"),
        )
    )
    missing = service.create_annotation(
        AnnotationCreate(
            source_id=missing_snapshot_source.id,
            subject="missing",
            note="Snapshot is missing.",
            selector=SourceSelector(exact="missing"),
        )
    )

    with service.connect() as conn:
        conn.execute("UPDATE annotations SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (expired.id,))
        conn.execute("UPDATE sources SET content_sha256 = 'new-hash' WHERE id = ?", (changed_source.id,))

    assert service.get_annotation(expired.id, include_private=True).staleness_reason == "freshness_expired"
    assert service.get_annotation(changed.id, include_private=True).staleness_reason == "source_changed"
    assert service.get_annotation(missing.id, include_private=True).staleness_reason == "snapshot_missing"


def test_api_hides_private_records_without_admin_token(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db_path = tmp_path / "app.sqlite3"
    settings = Settings(
        data_dir=data_dir,
        db_path=db_path,
        capture_queue_path=data_dir / "pending-research-captures.jsonl",
        backend_profile_path=data_dir / "backend-profiles.json",
        admin_token="secret",
        session_secret="session-secret",
        host="127.0.0.1",
        port=8000,
        default_backend_url=None,
        backend_url=None,
        backend_api_key=None,
        backend_org=None,
        backend_profile=None,
        public_base_url="http://127.0.0.1:8000",
    )
    app = create_app(settings)
    client = TestClient(app)
    service = app.state.service

    source = service.create_source(SourceCreate(canonical_url="https://example.com/a", title="Private source", snapshot_required=True, snapshot_present=True))
    annotation = service.create_annotation(
        AnnotationCreate(
            source_id=source.id,
            subject="private zebra",
            note="Private note.",
            selector=SourceSelector(exact="private note"),
        )
    )

    response = client.get(f"/api/annotations/{annotation.id}")
    assert response.status_code == 404

    review_response = client.post("/api/review", headers={"x-admin-token": "secret"}, json=ReviewRequest(kind="annotation", record_id=annotation.id).model_dump(mode="json"))
    assert review_response.status_code == 200

    publish_response = client.post("/api/publish", headers={"x-admin-token": "secret"}, json=PublishRequest(kind="annotation", record_id=annotation.id).model_dump(mode="json"))
    assert publish_response.status_code == 200

    response = client.get(f"/api/annotations/{annotation.id}")
    assert response.status_code == 200


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

    create_response = client.post(
        "/api/annotations",
        headers={"x-api-key": alice_key.token},
        json=AnnotationCreate(
            source=SourceCreate(canonical_url="https://example.com/memory", title="Memory source", snapshot_required=True, snapshot_present=True),
            subject="agent memory",
            note="Private memory note.",
            selector=SourceSelector(exact="memory note", deep_link="https://example.com/memory#1"),
        ).model_dump(mode="json"),
    )
    assert create_response.status_code == 200
    annotation_id = create_response.json()["id"]

    assert client.get(f"/api/annotations/{annotation_id}").status_code == 404
    assert client.get(f"/api/annotations/{annotation_id}", headers={"x-api-key": bob_key.token}, params={"include_private": "true"}).status_code == 404
    assert client.get(f"/api/annotations/{annotation_id}", headers={"x-api-key": alice_key.token}, params={"include_private": "true"}).status_code == 200

    publish_response = client.post(
        "/api/publish",
        headers={"x-api-key": alice_key.token},
        json=PublishRequest(kind="annotation", record_id=annotation_id).model_dump(mode="json"),
    )
    assert publish_response.status_code == 200

    namespace_search = client.get("/api/search", params={"q": "memory", "namespace_slug": "alice", "kind": "annotation"})
    assert namespace_search.status_code == 200
    assert [hit["id"] for hit in namespace_search.json()["hits"]] == [annotation_id]

    global_search = client.get("/api/search", params={"q": "memory"})
    assert global_search.status_code == 200
    assert global_search.json()["hits"] == []

    index_response = client.post(
        "/api/index-state",
        headers={"x-admin-token": "secret"},
        json={"kind": "annotation", "record_id": annotation_id, "state": "included"},
    )
    assert index_response.status_code == 200

    global_search = client.get("/api/search", params={"q": "memory"})
    assert [hit["id"] for hit in global_search.json()["hits"]] == [annotation_id]


def test_backend_selection_precedence(tmp_path: Path) -> None:
    profile_path = tmp_path / "profiles.json"
    profile_path.write_text(
        """
        {
          "profiles": {
            "vpn": {"url": "https://vpn.example.com", "kind": "corporate"}
          },
          "organizations": {
            "acme": {"url": "https://acme.example.com", "kind": "corporate"}
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    base_settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "backend.sqlite3",
        capture_queue_path=tmp_path / "data" / "queue.jsonl",
        backend_profile_path=profile_path,
        admin_token=None,
        session_secret="secret",
        host="127.0.0.1",
        port=8000,
        default_backend_url="https://default.example.com",
        backend_url=None,
        backend_api_key=None,
        backend_org=None,
        backend_profile=None,
        public_base_url="http://127.0.0.1:8000",
    )

    explicit = resolve_backend(base_settings.__class__(**{**base_settings.__dict__, "backend_url": "https://custom.example.com"}))
    assert explicit.url == "https://custom.example.com"
    assert explicit.selection_source == "explicit_url"

    named_profile = resolve_backend(base_settings.__class__(**{**base_settings.__dict__, "backend_profile": "vpn"}))
    assert named_profile.url == "https://vpn.example.com"
    assert named_profile.selection_source == "named_profile"

    org_profile = resolve_backend(base_settings.__class__(**{**base_settings.__dict__, "backend_org": "acme"}))
    assert org_profile.url == "https://acme.example.com"
    assert org_profile.selection_source == "organization_profile"

    default = resolve_backend(base_settings)
    assert default.url == "https://default.example.com"
    assert default.selection_source == "default_hosted"
