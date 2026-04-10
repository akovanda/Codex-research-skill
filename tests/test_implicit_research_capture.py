from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from research_registry.capture_queue import CaptureQueue, QueuedAnnotation, QueuedCaptureBundle, QueuedFinding, QueuedReport
from research_registry.models import RunCreate, SourceCreate, SourceSelector
from research_registry.research_capture import CaptureSummary, format_capture_summary, is_research_request, specialized_skill_for_prompt
from research_registry.service import RegistryService


def make_service(tmp_path: Path) -> RegistryService:
    service = RegistryService(tmp_path / "implicit.sqlite3")
    service.initialize()
    return service


def make_queue_bundle() -> QueuedCaptureBundle:
    return QueuedCaptureBundle.create(
        prompt="Research long-term memory structure for LLM agents.",
        normalized_topic="long-term memory structure",
        model_name="gpt-5.4",
        model_version="2026-04-10",
        run=RunCreate(
            question="What structure works best for long-term memory in LLM agents?",
            model_name="gpt-5.4",
            model_version="2026-04-10",
            notes="implicit capture test",
        ),
        annotations=[
            QueuedAnnotation(
                temp_id="ann_1",
                source=SourceCreate(
                    canonical_url="https://example.org/ltm-episodic",
                    title="Episodic memory for agents",
                    source_type="paper",
                    snapshot_required=True,
                    snapshot_present=True,
                ),
                subject="long-term memory structure",
                note="This source supports separating episodic events from stable semantic profile memory.",
                selector=SourceSelector(
                    exact="Agent memory works better when episodic traces and stable semantic profiles are stored separately.",
                    deep_link="https://example.org/ltm-episodic#results",
                ),
                model_name="gpt-5.4",
                model_version="2026-04-10",
                tags=["memory", "episodic", "semantic"],
            ),
            QueuedAnnotation(
                temp_id="ann_2",
                source=SourceCreate(
                    canonical_url="https://example.org/ltm-retrieval-policy",
                    title="Retrieval policies for long-term agent memory",
                    source_type="paper",
                    snapshot_required=True,
                    snapshot_present=True,
                ),
                subject="long-term memory structure",
                note="This source supports storing provenance and freshness alongside retrieved memories.",
                selector=SourceSelector(
                    exact="Long-term memory retrieval remains trustworthy only when each memory record carries freshness and provenance metadata.",
                    deep_link="https://example.org/ltm-retrieval-policy#discussion",
                ),
                model_name="gpt-5.4",
                model_version="2026-04-10",
                tags=["memory", "provenance", "freshness"],
            ),
        ],
        findings=[
            QueuedFinding(
                temp_id="fdg_1",
                title="Long-term memory should separate event traces from stable profiles",
                subject="long-term memory structure",
                claim="A durable long-term memory structure should split episodic event traces from stable semantic or profile memory and retain provenance metadata for both.",
                annotation_temp_ids=["ann_1", "ann_2"],
                model_name="gpt-5.4",
                model_version="2026-04-10",
            )
        ],
        report=QueuedReport(
            question="What structure works best for long-term memory in LLM agents?",
            subject="long-term memory structure",
            summary_md="# Long-term memory structure\n\nStore episodic traces separately from stable semantic memory and attach provenance/freshness metadata to each memory record.",
            finding_temp_ids=["fdg_1"],
            model_name="gpt-5.4",
            model_version="2026-04-10",
        ),
    )


def test_research_prompt_classification_and_specialized_delegation() -> None:
    assert is_research_request("Please research RAG retrieval quality.")
    assert is_research_request("Look into long-term memory tradeoffs.")
    assert not is_research_request("Fix this failing unit test.")

    assert specialized_skill_for_prompt("Research long-term memory structure for LLMs.") == "research-memory-retrieval"
    assert specialized_skill_for_prompt("Research restaurant options in Boston.") is None


def test_capture_queue_round_trip_and_flush(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    queue = CaptureQueue(tmp_path / "pending-research-captures.jsonl")
    bundle = make_queue_bundle()

    queue.enqueue(bundle)
    reloaded = queue.list_pending()
    assert [item.queue_id for item in reloaded] == [bundle.queue_id]

    result = queue.flush(service)
    assert result.flushed_queue_ids == [bundle.queue_id]
    assert result.failed_queue_ids == []

    report_hits = service.search("long-term memory structure", kind="report", include_private=True)
    assert any(hit.title == "What structure works best for long-term memory in LLM agents?" for hit in report_hits.hits)


def test_queue_replay_is_idempotent_for_same_bundle(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    queue = CaptureQueue(tmp_path / "pending-research-captures.jsonl")
    bundle = make_queue_bundle()

    queue.enqueue(bundle)
    queue.flush(service)
    queue.enqueue(bundle)
    queue.flush(service)

    assert len(service.dashboard(include_private=True, limit=20).annotations) == 2
    assert len(service.dashboard(include_private=True, limit=20).findings) == 1
    assert len(service.dashboard(include_private=True, limit=20).reports) == 1


def test_capture_summary_mentions_reuse_storage_and_queue() -> None:
    summary = CaptureSummary(
        prompt="Research long-term memory structure",
        reused_record_ids=["fdg_existing"],
        stored_run_id="run_123",
        stored_annotation_ids=["ann_1", "ann_2"],
        stored_finding_ids=["fdg_1"],
        stored_report_id="rpt_1",
        queued_bundle_id="queue_1",
        pending_queue_count=2,
        created_at=datetime.now(timezone.utc),
    )

    formatted = format_capture_summary(summary)
    assert "fdg_existing" in formatted
    assert "Stored report: rpt_1" in formatted
    assert "Queued for retry: queue_1" in formatted
