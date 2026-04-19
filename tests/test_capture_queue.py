from __future__ import annotations

from pathlib import Path

from research_registry.capture_queue import CaptureQueue, QueuedAnnotation, QueuedCaptureBundle, QueuedFinding, QueuedReport
from research_registry.models import RunCreate, SourceCreate, SourceSelector
from research_registry.service import RegistryService


def make_service(tmp_path: Path) -> RegistryService:
    service = RegistryService(tmp_path / "queue.sqlite3")
    service.initialize()
    return service


def make_bundle(service: RegistryService) -> QueuedCaptureBundle:
    return QueuedCaptureBundle.create(
        prompt="Research branch-private memory isolation strategies for divergent narrative and coding branches.",
        normalized_topic="branch-private memory isolation",
        model_name="gpt-5.4",
        model_version="2026-04-10",
        run=RunCreate(
            question="Research branch-private memory isolation strategies for divergent narrative and coding branches.",
            model_name="gpt-5.4",
            model_version="2026-04-10",
            notes="queued fallback bundle",
        ),
        annotations=[
            QueuedAnnotation(
                temp_id="ann1",
                source=SourceCreate(
                    locator="https://example.com/branch-private",
                    title="Branch private note",
                    snapshot_present=True,
                ),
                subject="branch-private memory isolation",
                note="Branch-private memory stays isolated across divergent branches.",
                selector=SourceSelector(
                    exact="Branch-private memory stays isolated across divergent branches.",
                    deep_link="https://example.com/branch-private#isolation",
                ),
                quote_text="Branch-private memory stays isolated across divergent branches.",
            )
        ],
        findings=[
            QueuedFinding(
                temp_id="find1",
                title="Branch-private isolation is explicit",
                subject="branch-private memory isolation",
                claim="Branch-private memory isolation is explicit in the current design.",
                annotation_temp_ids=["ann1"],
            )
        ],
        report=QueuedReport(
            question="Research branch-private memory isolation strategies for divergent narrative and coding branches.",
            subject="branch-private memory isolation",
            summary_md="# Guidance\n\nKeep divergent branches isolated.",
            finding_temp_ids=["find1"],
        ),
        backend_status=service.backend_status(),
    )


def test_capture_queue_flush_replays_legacy_bundle_into_current_registry_model(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    queue = CaptureQueue(tmp_path / "pending.jsonl")
    queue.enqueue(make_bundle(service))

    result = queue.flush(service)

    assert len(result.flushed_queue_ids) == 1
    assert result.failed_queue_ids == []
    assert len(result.stored_report_ids) == 1
    assert queue.list_pending() == []

    report = service.get_report(result.stored_report_ids[0], include_private=True)
    question = service.get_question(report.question_id, include_private=True)
    claim = service.get_claim(report.claim_ids[0], include_private=True)
    excerpt = service.get_excerpt(claim.excerpt_ids[0], include_private=True)
    source = service.get_source(excerpt.source_id, include_private=True)

    assert question.status == "answered"
    assert report.title == "Research branch-private memory isolation strategies for divergent narrative and coding branches."
    assert report.focal_label == "branch-private memory isolation"
    assert claim.statement == "Branch-private memory isolation is explicit in the current design."
    assert excerpt.focal_label == "branch-private memory isolation"
    assert source.locator == "https://example.com/branch-private"

    with service.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM research_sessions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM excerpts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1


def test_capture_queue_flush_is_idempotent_for_reenqueued_bundle(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    queue = CaptureQueue(tmp_path / "pending.jsonl")
    bundle = make_bundle(service)

    queue.enqueue(bundle)
    first = queue.flush(service)
    queue.enqueue(bundle)
    second = queue.flush(service)

    assert len(first.flushed_queue_ids) == 1
    assert len(second.flushed_queue_ids) == 1
    assert first.failed_queue_ids == []
    assert second.failed_queue_ids == []
    assert first.stored_report_ids == second.stored_report_ids

    with service.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM research_sessions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM excerpts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
