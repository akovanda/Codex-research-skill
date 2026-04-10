from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from research_registry.app import create_app
from research_registry.config import Settings
from research_registry.models import AnnotationCreate, FindingCreate, PublishRequest, ReportCompileCreate, ReviewRequest, RunCreate, SourceCreate, SourceSelector
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
    db_path = tmp_path / "app.sqlite3"
    settings = Settings(
        db_path=db_path,
        admin_token="secret",
        session_secret="session-secret",
        host="127.0.0.1",
        port=8000,
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
