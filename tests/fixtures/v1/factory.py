from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from research_registry.models import (
    ApiKeyCreate,
    ClaimCreate,
    ExcerptCreate,
    FocusTuple,
    GuidancePayload,
    QuestionCreate,
    ReportCreate,
    ResearchSessionCreate,
    ReviewRequest,
    SourceCreate,
    SourceSelector,
)
from research_registry.service import RegistryService


@dataclass(frozen=True)
class V1FixtureIds:
    root_question_id: str
    follow_up_question_id: str
    fresh_session_id: str
    stale_session_id: str
    snapshotted_source_id: str
    missing_snapshot_source_id: str
    reviewed_excerpt_id: str
    flagged_excerpt_id: str
    reviewed_claim_id: str
    conflicted_claim_id: str
    report_id: str
    refreshed_report_id: str
    api_key_id: str

    @property
    def annotation_id(self) -> str:
        return self.reviewed_excerpt_id

    @property
    def finding_id(self) -> str:
        return self.reviewed_claim_id


def populate_v1_fixture(service: RegistryService, *, suffix: str = "representative") -> V1FixtureIds:
    """Populate any supported v1 database through the public service methods."""
    service.initialize()
    focus = FocusTuple(domain="rr2-fixture", object=f"v1 migration {suffix}")
    root = service.create_question(
        QuestionCreate(
            prompt=f"Private fixture prompt sentinel {suffix}.",
            focus=focus,
            namespace_kind="org",
            namespace_id=f"fixture-{suffix}",
            dedupe_key=f"fixture:{suffix}:question",
        )
    )
    fresh_session = service.create_session(
        ResearchSessionCreate(
            question_id=root.id,
            prompt=root.prompt,
            model_name="fixture-model",
            model_version="v1",
            mode="live_research",
            source_signals=["synthetic:v1-fixture"],
            namespace_kind="org",
            namespace_id=f"fixture-{suffix}",
            dedupe_key=f"fixture:{suffix}:session:fresh",
        )
    )
    stale_session = service.create_session(
        ResearchSessionCreate(
            question_id=root.id,
            prompt=root.prompt,
            model_name="fixture-model",
            model_version="v1",
            mode="synthesis",
            refresh_of_session_id=fresh_session.id,
            namespace_kind="org",
            namespace_id=f"fixture-{suffix}",
            dedupe_key=f"fixture:{suffix}:session:stale",
        )
    )
    snapshotted = service.create_source(
        SourceCreate(
            locator=f"https://example.invalid/{suffix}/source?private_query=fixture-secret",
            title=f"Synthetic snapshotted source {suffix}",
            content_sha256="a" * 64,
            snapshot_url=f"https://archive.invalid/{suffix}",
            snapshot_required=True,
            snapshot_present=True,
            review_state="reviewed",
            trust_tier="high",
            visibility="public",
            namespace_kind="org",
            namespace_id=f"fixture-{suffix}",
            dedupe_key=f"fixture:{suffix}:source:snapshotted",
        )
    )
    missing_snapshot = service.create_source(
        SourceCreate(
            locator=f"https://example.invalid/{suffix}/missing",
            title=f"Synthetic missing snapshot source {suffix}",
            snapshot_required=True,
            snapshot_present=False,
            review_state="flagged",
            trust_tier="low",
            conflict_state="conflicted",
            namespace_kind="org",
            namespace_id=f"fixture-{suffix}",
            dedupe_key=f"fixture:{suffix}:source:missing",
        )
    )
    reviewed_excerpt = service.create_excerpt(
        ExcerptCreate(
            source_id=snapshotted.id,
            question_id=root.id,
            session_id=fresh_session.id,
            focal_label=focus.label or "v1 migration",
            note=f"Synthetic reviewed evidence note {suffix}.",
            selector=SourceSelector(
                type="TextQuoteSelector",
                exact=f"Private fixture quote sentinel {suffix}.",
                deep_link=f"https://example.invalid/{suffix}/source#evidence",
            ),
            quote_text=f"Private fixture quote sentinel {suffix}.",
            review_state="reviewed",
            trust_tier="high",
            visibility="public",
            namespace_kind="org",
            namespace_id=f"fixture-{suffix}",
            dedupe_key=f"fixture:{suffix}:excerpt:reviewed",
        )
    )
    flagged_excerpt = service.create_excerpt(
        ExcerptCreate(
            source_id=missing_snapshot.id,
            question_id=root.id,
            session_id=stale_session.id,
            focal_label=focus.label or "v1 migration",
            note=f"Synthetic flagged evidence note {suffix}.",
            selector=SourceSelector(start_line=10, end_line=12),
            quote_text=f"Synthetic flagged quote {suffix}.",
            review_state="flagged",
            trust_tier="low",
            conflict_state="conflicted",
            namespace_kind="org",
            namespace_id=f"fixture-{suffix}",
            dedupe_key=f"fixture:{suffix}:excerpt:flagged",
        )
    )
    reviewed_claim = service.create_claim(
        ClaimCreate(
            question_id=root.id,
            session_id=fresh_session.id,
            title=f"Synthetic reviewed claim {suffix}",
            focal_label=focus.label or "v1 migration",
            statement=f"Private fixture claim sentinel {suffix}.",
            excerpt_ids=[reviewed_excerpt.id],
            review_state="reviewed",
            trust_tier="high",
            visibility="public",
            namespace_kind="org",
            namespace_id=f"fixture-{suffix}",
            dedupe_key=f"fixture:{suffix}:claim:reviewed",
        )
    )
    conflicted_claim = service.create_claim(
        ClaimCreate(
            question_id=root.id,
            session_id=stale_session.id,
            title=f"Synthetic conflicted claim {suffix}",
            focal_label=focus.label or "v1 migration",
            statement=f"Synthetic conflicted statement {suffix}.",
            excerpt_ids=[flagged_excerpt.id],
            status="conflicted",
            review_state="flagged",
            trust_tier="low",
            conflict_state="conflicted",
            namespace_kind="org",
            namespace_id=f"fixture-{suffix}",
            dedupe_key=f"fixture:{suffix}:claim:conflicted",
        )
    )
    report = service.create_report(
        ReportCreate(
            question_id=root.id,
            session_id=fresh_session.id,
            title=f"Synthetic v1 report {suffix}",
            focal_label=focus.label or "v1 migration",
            summary_md=f"# Private fixture report sentinel {suffix}",
            guidance=GuidancePayload(current_guidance=["Synthetic fixture guidance."]),
            claim_ids=[reviewed_claim.id, conflicted_claim.id],
            review_state="reviewed",
            trust_tier="high",
            visibility="public",
            namespace_kind="org",
            namespace_id=f"fixture-{suffix}",
            dedupe_key=f"fixture:{suffix}:report",
        )
    )
    refreshed_report = service.create_report(
        ReportCreate(
            question_id=root.id,
            session_id=stale_session.id,
            title=f"Synthetic refreshed v1 report {suffix}",
            focal_label=focus.label or "v1 migration",
            summary_md=f"# Synthetic refreshed report {suffix}",
            refresh_of_report_id=report.id,
            guidance=GuidancePayload(current_guidance=["Synthetic refreshed fixture guidance."]),
            claim_ids=[reviewed_claim.id, conflicted_claim.id],
            review_state="flagged",
            trust_tier="medium",
            conflict_state="conflicted",
            namespace_kind="org",
            namespace_id=f"fixture-{suffix}",
            dedupe_key=f"fixture:{suffix}:report:refreshed",
        )
    )
    follow_up = service.create_question(
        QuestionCreate(
            prompt=f"Synthetic follow-up {suffix}.",
            focus=focus,
            parent_question_id=root.id,
            generated_by_session_id=stale_session.id,
            generation_reason="synthetic_fixture_gap",
            follow_up_status="ready",
            priority_score=0.8,
            namespace_kind="org",
            namespace_id=f"fixture-{suffix}",
            dedupe_key=f"fixture:{suffix}:question:follow-up",
        )
    )
    service.add_org_membership(f"fixture-{suffix}", f"fixture-user-{suffix}", role="reviewer")
    issued_key = service.issue_api_key(
        ApiKeyCreate(
            label=f"Synthetic fixture key {suffix}",
            actor_user_id=f"fixture-user-{suffix}",
            actor_org_id=f"fixture-{suffix}",
            namespace_kind="org",
            namespace_id=f"fixture-{suffix}",
        )
    )
    service.review(
        ReviewRequest(kind="claim", record_id=reviewed_claim.id),
        auth=service.authenticate_api_key(issued_key.token),
    )

    # The v1 service computes freshness from wall-clock time. These two state
    # edits create a stable expired legacy edge after all records were created
    # through supported v1 methods.
    with service.connect() as conn:
        conn.execute(
            """
            UPDATE research_sessions
            SET expires_at = ?, freshness_state = 'needs_refresh'
            WHERE id = ?
            """,
            (datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat(), stale_session.id),
        )
        for table, record_id in (
            ("sources", missing_snapshot.id),
            ("excerpts", flagged_excerpt.id),
            ("claims", conflicted_claim.id),
            ("reports", refreshed_report.id),
        ):
            conn.execute(
                f"UPDATE {table} SET refresh_due_at = ? WHERE id = ?",
                (datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat(), record_id),
            )

    return V1FixtureIds(
        root_question_id=root.id,
        follow_up_question_id=follow_up.id,
        fresh_session_id=fresh_session.id,
        stale_session_id=stale_session.id,
        snapshotted_source_id=snapshotted.id,
        missing_snapshot_source_id=missing_snapshot.id,
        reviewed_excerpt_id=reviewed_excerpt.id,
        flagged_excerpt_id=flagged_excerpt.id,
        reviewed_claim_id=reviewed_claim.id,
        conflicted_claim_id=conflicted_claim.id,
        report_id=report.id,
        refreshed_report_id=refreshed_report.id,
        api_key_id=issued_key.record.id,
    )


def weaken_sqlite_v1_fixture(database_path: Path, ids: V1FixtureIds) -> None:
    """Inject safely reportable weak-v1 states after service-backed creation."""
    with sqlite3.connect(database_path) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "UPDATE excerpts SET source_id = 'src_missing_fixture' WHERE id = ?",
            (ids.flagged_excerpt_id,),
        )
        conn.execute(
            "UPDATE excerpts SET selector_json = '{malformed' WHERE id = ?",
            (ids.reviewed_excerpt_id,),
        )
        conn.execute(
            "UPDATE sources SET review_state = 'legacy_unknown' WHERE id = ?",
            (ids.missing_snapshot_source_id,),
        )
        conn.execute(
            "DELETE FROM claim_excerpts WHERE claim_id = ?",
            (ids.conflicted_claim_id,),
        )
        conn.execute(
            "DELETE FROM report_claims WHERE report_id = ?",
            (ids.refreshed_report_id,),
        )
