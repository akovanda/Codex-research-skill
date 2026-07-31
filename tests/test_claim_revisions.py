from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
from threading import Barrier

import pytest

from research_registry.application.review import (
    ExpectedRevisionMismatch,
    ExpectedStateMismatch,
    IdempotencyConflict,
    InvalidClaimTransition,
    ResearchReviewService,
)
from research_registry.persistence.read_adapter import (
    CurrentRetrievalAdapter,
    ReadAccess,
)
from tests.fixtures.v2_review import STATEMENT, seed_review_registry


def _revision_digest(row) -> str:
    return sha256(
        json.dumps(dict(row), sort_keys=True, default=str).encode()
    ).hexdigest()


def test_approve_is_idempotent_and_appends_an_immutable_review_event(
    tmp_path: Path,
) -> None:
    registry, ids = seed_review_registry(tmp_path)
    reviews = ResearchReviewService(registry.database)
    request = {
        "protocol": "research-review/v2",
        "idempotency_key": "approve-current",
        "entity": {"kind": "claim_revision", "id": ids["revision"]},
        "action": "approve",
        "expected_revision_id": ids["revision"],
        "expected_state": "unreviewed",
        "note": "Evidence and statement agree.",
    }

    first = reviews.review(request)
    replay = reviews.review(request)

    assert first.status == "applied"
    assert first.idempotent_replay is False
    assert replay.model_copy(update={"idempotent_replay": False}) == first
    assert replay.idempotent_replay is True
    assert first.current_revision_id == ids["revision"]
    assert first.revision_created is False
    assert first.current_state.review_state == "reviewed"
    with registry.connect() as conn:
        claim = conn.execute(
            "SELECT * FROM claims WHERE id = ?", (ids["claim"],)
        ).fetchone()
        events = conn.execute(
            "SELECT * FROM review_events WHERE entity_id = ?",
            (ids["revision"],),
        ).fetchall()
    assert claim["review_state"] == "reviewed"
    assert claim["human_reviewed"] == 1
    assert len(events) == 1
    assert events[0]["action"] == "approve"
    assert events[0]["from_state"] == "unreviewed"
    assert events[0]["to_state"] == "reviewed"
    changed = {**request, "note": "A different decision payload."}
    with pytest.raises(IdempotencyConflict, match="IDEMPOTENCY_CONFLICT"):
        reviews.review(changed)


def test_evidence_revision_history_never_inherits_mutable_claim_review_state(
    tmp_path: Path,
) -> None:
    registry, ids = seed_review_registry(
        tmp_path,
        key="historical-review-fallback",
    )
    current_revision_id = "clmr_historical_fallback_current"
    with registry.connect() as conn:
        conn.execute(
            """
            INSERT INTO claim_revisions (
                id, claim_id, revision_number, title, statement, status,
                confidence, valid_from, valid_until, supersedes_revision_id,
                created_by_model, created_at, metadata_json
            )
            SELECT
                ?, claim_id, 2, title, statement, status, confidence,
                valid_from, valid_until, id, created_by_model,
                '2026-07-31T12:00:00+00:00', metadata_json
            FROM claim_revisions
            WHERE id = ?
            """,
            (current_revision_id, ids["revision"]),
        )
        conn.execute(
            """
            INSERT INTO claim_evidence (
                claim_revision_id, evidence_span_id, relationship, rationale,
                weight, review_state, created_at
            )
            SELECT
                ?, evidence_span_id, relationship, rationale, weight,
                review_state, '2026-07-31T12:00:00+00:00'
            FROM claim_evidence
            WHERE claim_revision_id = ?
            """,
            (current_revision_id, ids["revision"]),
        )
        conn.execute(
            """
            UPDATE claims
            SET current_revision_id = ?, review_state = 'reviewed',
                human_reviewed = 1
            WHERE id = ?
            """,
            (current_revision_id, ids["claim"]),
        )

    revisions = CurrentRetrievalAdapter(
        registry.database
    ).list_claim_revisions_for_evidence(
        ids["supporting"],
        access=ReadAccess(include_private=True, local_trusted=True),
    )

    assert {
        row["revision_id"]: row["review_state"] for row in revisions
    } == {
        ids["revision"]: "unreviewed",
        current_revision_id: "unreviewed",
    }


def test_stale_expected_revision_and_state_conflict_without_partial_writes(
    tmp_path: Path,
) -> None:
    registry, ids = seed_review_registry(tmp_path)
    reviews = ResearchReviewService(registry.database)

    with pytest.raises(ExpectedRevisionMismatch, match="EXPECTED_REVISION_MISMATCH"):
        reviews.review(
            {
                "protocol": "research-review/v2",
                "idempotency_key": "stale-revision",
                "entity": {"kind": "claim_revision", "id": ids["revision"]},
                "action": "approve",
                "expected_revision_id": "clmr_stale",
                "expected_state": "unreviewed",
            }
        )
    with pytest.raises(ExpectedStateMismatch, match="EXPECTED_STATE_MISMATCH"):
        reviews.review(
            {
                "protocol": "research-review/v2",
                "idempotency_key": "stale-state",
                "entity": {"kind": "claim_revision", "id": ids["revision"]},
                "action": "approve",
                "expected_revision_id": ids["revision"],
                "expected_state": "reviewed",
            }
        )

    with registry.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS count FROM review_events"
            ).fetchone()["count"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) AS count FROM idempotency_keys "
                "WHERE operation = 'research_review_v2'"
            ).fetchone()["count"]
            == 0
        )


def test_two_concurrent_reviewers_commit_once_and_stale_request_conflicts(
    tmp_path: Path,
) -> None:
    registry, ids = seed_review_registry(tmp_path, key="concurrent-review")
    barrier = Barrier(2)

    def apply(action: str) -> str:
        barrier.wait()
        try:
            ResearchReviewService(registry.database).review(
                {
                    "protocol": "research-review/v2",
                    "idempotency_key": f"concurrent-{action}",
                    "entity": {
                        "kind": "claim_revision",
                        "id": ids["revision"],
                    },
                    "action": action,
                    "expected_revision_id": ids["revision"],
                    "expected_state": "unreviewed",
                }
            )
        except ExpectedRevisionMismatch:
            return "conflict"
        return "applied"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(apply, ("contest", "reject")))

    assert sorted(outcomes) == ["applied", "conflict"]
    with registry.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS count FROM claim_revisions WHERE claim_id = ?",
                (ids["claim"],),
            ).fetchone()["count"]
            == 2
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) AS count FROM review_events"
            ).fetchone()["count"]
            == 1
        )


@pytest.mark.parametrize(
    ("action", "target_status", "expected_conflict"),
    [
        ("contest", "contested", "conflicted"),
        ("reject", "rejected", "none"),
    ],
)
def test_contest_and_reject_create_new_revisions_and_move_v1_mirrors(
    tmp_path: Path,
    action: str,
    target_status: str,
    expected_conflict: str,
) -> None:
    registry, ids = seed_review_registry(tmp_path, key=action)
    reviews = ResearchReviewService(registry.database)
    with registry.connect() as conn:
        old_row = conn.execute(
            "SELECT * FROM claim_revisions WHERE id = ?", (ids["revision"],)
        ).fetchone()
        old_digest = _revision_digest(old_row)

    result = reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"{action}-current",
            "entity": {"kind": "claim_revision", "id": ids["revision"]},
            "action": action,
            "expected_revision_id": ids["revision"],
            "expected_state": "unreviewed",
        }
    )

    assert result.revision_created is True
    assert result.current_revision_id != ids["revision"]
    assert result.current_state.status == target_status
    assert result.current_state.review_state == "flagged"
    assert result.current_state.conflict_state == expected_conflict
    with registry.connect() as conn:
        old_after = conn.execute(
            "SELECT * FROM claim_revisions WHERE id = ?", (ids["revision"],)
        ).fetchone()
        current = conn.execute(
            "SELECT c.*, cr.revision_number, cr.supersedes_revision_id "
            "FROM claims c JOIN claim_revisions cr "
            "ON cr.id = c.current_revision_id WHERE c.id = ?",
            (ids["claim"],),
        ).fetchone()
        links = conn.execute(
            "SELECT relationship FROM claim_evidence "
            "WHERE claim_revision_id = ?",
            (result.current_revision_id,),
        ).fetchall()
    assert _revision_digest(old_after) == old_digest
    assert current["current_revision_id"] == result.current_revision_id
    assert current["revision_number"] == 2
    assert current["supersedes_revision_id"] == ids["revision"]
    assert current["statement"] == STATEMENT
    assert current["status"] == {
        "contested": "conflicted",
        "rejected": "insufficient_evidence",
    }[target_status]
    assert [row["relationship"] for row in links] == ["supports"]


def test_supersede_requires_a_replacement_and_closes_terminal_transitions(
    tmp_path: Path,
) -> None:
    registry, ids = seed_review_registry(tmp_path, key="supersede")
    reviews = ResearchReviewService(registry.database)

    with pytest.raises(InvalidClaimTransition, match="INVALID_CLAIM_TRANSITION"):
        reviews.review(
            {
                "protocol": "research-review/v2",
                "idempotency_key": "supersede-without-replacement",
                "entity": {"kind": "claim_revision", "id": ids["revision"]},
                "action": "supersede",
                "expected_revision_id": ids["revision"],
                "expected_state": "unreviewed",
            }
        )

    with pytest.raises(InvalidClaimTransition, match="only valid for supersede"):
        reviews.review(
            {
                "protocol": "research-review/v2",
                "idempotency_key": "contest-with-replacement",
                "entity": {"kind": "claim_revision", "id": ids["revision"]},
                "action": "contest",
                "expected_revision_id": ids["revision"],
                "expected_state": "unreviewed",
                "new_revision": {
                    "title": "Unrelated replacement",
                    "statement": "This must not inherit old evidence.",
                    "status": "contested",
                    "confidence": 0.5,
                },
            }
        )

    result = reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": "supersede-with-replacement",
            "entity": {"kind": "claim_revision", "id": ids["revision"]},
            "action": "supersede",
            "expected_revision_id": ids["revision"],
            "expected_state": "unreviewed",
            "new_revision": {
                "title": "Replacement conclusion",
                "statement": "The replacement statement is explicit.",
                "status": "partial",
                "confidence": 0.6,
            },
        }
    )

    assert result.current_state.status == "partial"
    assert result.current_state.review_state == "unreviewed"
    with registry.connect() as conn:
        event = conn.execute(
            "SELECT * FROM review_events WHERE id = ?", (result.event_id,)
        ).fetchone()
        old = conn.execute(
            "SELECT status, statement FROM claim_revisions WHERE id = ?",
            (ids["revision"],),
        ).fetchone()
        inherited = conn.execute(
            "SELECT COUNT(*) AS count FROM claim_evidence "
            "WHERE claim_revision_id = ?",
            (result.current_revision_id,),
        ).fetchone()["count"]
    assert event["action"] == "supersede"
    assert json.loads(event["metadata_json"])["evidence_mode"] == "none"
    assert inherited == 0
    assert old["status"] == "supported"
    assert old["statement"] == STATEMENT


def test_supersede_evidence_inheritance_is_explicit_and_audited(
    tmp_path: Path,
) -> None:
    registry, ids = seed_review_registry(tmp_path, key="supersede-inherit")
    result = ResearchReviewService(registry.database).review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": "supersede-explicit-inherit",
            "entity": {"kind": "claim_revision", "id": ids["revision"]},
            "action": "supersede",
            "expected_revision_id": ids["revision"],
            "expected_state": "unreviewed",
            "new_revision": {
                "title": "Clarified wording",
                "statement": STATEMENT,
                "status": "supported",
                "confidence": 0.8,
                "evidence_mode": "inherit",
            },
        }
    )
    with registry.connect() as conn:
        links = conn.execute(
            "SELECT relationship FROM claim_evidence "
            "WHERE claim_revision_id = ?",
            (result.current_revision_id,),
        ).fetchall()
        event = conn.execute(
            "SELECT metadata_json FROM review_events WHERE id = ?",
            (result.event_id,),
        ).fetchone()
    assert [row["relationship"] for row in links] == ["supports"]
    assert json.loads(event["metadata_json"])["evidence_mode"] == "inherit"


def test_refuting_evidence_derives_contested_state_without_statement_mutation(
    tmp_path: Path,
) -> None:
    registry, ids = seed_review_registry(
        tmp_path,
        key="refuting",
        status="partial",
        include_refuting_evidence=True,
    )
    reviews = ResearchReviewService(registry.database)

    state = reviews.current_claim_state(ids["claim"])

    assert state.status == "partial"
    assert state.conflict_state == "conflicted"
    assert state.review_state == "unreviewed"
    retrieved = CurrentRetrievalAdapter(registry.database).get_record(
        ids["claim"],
        access=ReadAccess(
            include_private=True,
            namespace_kind="user",
            namespace_id="local",
            local_trusted=True,
        ),
    )
    assert retrieved is not None
    assert retrieved.conflict_state == "conflicted"
    contested = reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": "contest-refuting-evidence",
            "entity": {"kind": "claim_revision", "id": ids["revision"]},
            "action": "contest",
            "expected_revision_id": ids["revision"],
            "expected_state": "unreviewed",
        }
    )
    assert contested.current_state is not None
    assert contested.current_state.status == "contested"
    assert contested.current_state.conflict_state == "conflicted"
    with registry.connect() as conn:
        old_revision = conn.execute(
            "SELECT statement FROM claim_revisions WHERE id = ?",
            (ids["revision"],),
        ).fetchone()
        current_revision = conn.execute(
            "SELECT statement FROM claim_revisions WHERE id = ?",
            (contested.current_revision_id,),
        ).fetchone()
        revision_count = conn.execute(
            "SELECT COUNT(*) AS count FROM claim_revisions WHERE claim_id = ?",
            (ids["claim"],),
        ).fetchone()["count"]
    assert old_revision["statement"] == STATEMENT
    assert current_revision["statement"] == STATEMENT
    assert revision_count == 2
