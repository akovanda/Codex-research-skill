from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from research_registry.application.refresh import (
    InvalidRefreshTransition,
    RefreshIdempotencyConflict,
    ResearchRefreshService,
)
from research_registry.application.review import ResearchReviewService
from research_registry.persistence.read_adapter import (
    CurrentRetrievalAdapter,
    ReadAccess,
)
from tests.fixtures.v2_review import seed_review_registry


def _queue_rows(registry) -> list[dict]:
    with registry.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM refresh_queue ORDER BY entity_kind, entity_id"
            ).fetchall()
        ]


def test_inspect_is_read_only_and_enqueue_expands_affected_claims_and_reports(
    tmp_path: Path,
) -> None:
    registry, ids = seed_review_registry(tmp_path, key="refresh-expand")
    refresh = ResearchRefreshService(registry.database)
    inspect_request = {
        "protocol": "research-refresh/v2",
        "mode": "inspect",
        "entities": [{"kind": "evidence", "id": ids["supporting"]}],
        "priority": 0.8,
    }

    inspected = refresh.refresh(inspect_request)

    assert inspected.status == "inspected"
    assert inspected.committed is False
    assert _queue_rows(registry) == []
    assert {
        (item.entity.kind, item.entity.id) for item in inspected.items
    } == {
        ("evidence", ids["supporting"]),
        ("claim", ids["claim"]),
        ("report", ids["report"]),
    }

    enqueue_request = {
        **inspect_request,
        "mode": "enqueue",
        "idempotency_key": "enqueue-evidence",
    }
    first = refresh.refresh(enqueue_request)
    replay = refresh.refresh(enqueue_request)

    assert first.status == "enqueued"
    assert first.committed is True
    assert first.idempotent_replay is False
    assert replay.model_copy(update={"idempotent_replay": False}) == first
    assert replay.idempotent_replay is True
    assert len(_queue_rows(registry)) == 3
    assert {item.queue_status for item in first.items} == {"pending"}
    with pytest.raises(
        RefreshIdempotencyConflict,
        match="IDEMPOTENCY_CONFLICT",
    ):
        refresh.refresh({**enqueue_request, "priority": 0.2})
    state = ResearchReviewService(registry.database).current_claim_state(
        ids["claim"]
    )
    assert state.freshness == "needs_refresh"
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
    assert retrieved.freshness == "needs_refresh"


def test_queue_dedupes_without_an_idempotency_key_and_dismiss_is_terminal(
    tmp_path: Path,
) -> None:
    registry, ids = seed_review_registry(tmp_path, key="refresh-dedupe")
    refresh = ResearchRefreshService(registry.database)
    request = {
        "protocol": "research-refresh/v2",
        "mode": "enqueue",
        "entities": [{"kind": "claim", "id": ids["claim"]}],
    }

    first = refresh.refresh(request)
    second = refresh.refresh(request)

    assert first.items[0].refresh_item_id == second.items[0].refresh_item_id
    assert first.items[0].created is True
    assert second.items[0].created is False
    assert len(_queue_rows(registry)) == 2
    claim_item = next(
        item for item in first.items if item.entity.kind == "claim"
    )
    reviews = ResearchReviewService(registry.database)
    dismissed = reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": "dismiss-claim-refresh",
            "entity": {
                "kind": "refresh_item",
                "id": claim_item.refresh_item_id,
            },
            "action": "dismiss_refresh",
            "expected_state": "pending",
            "note": "No refresh is needed.",
        }
    )

    assert dismissed.refresh_item_ids == [claim_item.refresh_item_id]
    with registry.connect() as conn:
        item = conn.execute(
            "SELECT * FROM refresh_queue WHERE id = ?",
            (claim_item.refresh_item_id,),
        ).fetchone()
        event = conn.execute(
            "SELECT * FROM review_events WHERE id = ?",
            (dismissed.event_id,),
        ).fetchone()
    assert item["status"] == "dismissed"
    assert item["resolved_at"] is not None
    assert event["entity_kind"] == "claim_revision"
    assert event["entity_id"] == ids["revision"]
    assert event["action"] == "refresh_resolved"

    with pytest.raises(InvalidRefreshTransition, match="INVALID_CLAIM_TRANSITION"):
        reviews.review(
            {
                "protocol": "research-review/v2",
                "idempotency_key": "dismiss-again",
                "entity": {
                    "kind": "refresh_item",
                    "id": claim_item.refresh_item_id,
                },
                "action": "dismiss_refresh",
                "expected_state": "dismissed",
            }
        )


def test_request_refresh_is_idempotent_and_records_append_only_history(
    tmp_path: Path,
) -> None:
    registry, ids = seed_review_registry(tmp_path, key="review-refresh")
    reviews = ResearchReviewService(registry.database)
    request = {
        "protocol": "research-review/v2",
        "idempotency_key": "request-refresh",
        "entity": {"kind": "claim_revision", "id": ids["revision"]},
        "action": "request_refresh",
        "expected_revision_id": ids["revision"],
        "expected_state": "unreviewed",
    }

    first = reviews.review(request)
    replay = reviews.review(request)

    assert replay.model_copy(update={"idempotent_replay": False}) == first
    assert first.refresh_item_ids
    with registry.connect() as conn:
        events = conn.execute(
            "SELECT * FROM review_events WHERE action = 'refresh_requested'"
        ).fetchall()
        pending = conn.execute(
            "SELECT * FROM refresh_queue WHERE status = 'pending'"
        ).fetchall()
    assert len(events) == 1
    assert len(pending) == 2


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres review/refresh parity requires TEST_DATABASE_URL",
)
def test_postgres_review_and_refresh_match_sqlite_contract(
    tmp_path: Path,
) -> None:
    key = f"review-postgres-{uuid4().hex}"
    registry, ids = seed_review_registry(
        tmp_path,
        key=key,
        database=os.environ["TEST_DATABASE_URL"],
    )
    reviewed = ResearchReviewService(registry.database).review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"{key}-contest",
            "entity": {"kind": "claim_revision", "id": ids["revision"]},
            "action": "contest",
            "expected_revision_id": ids["revision"],
            "expected_state": "unreviewed",
        }
    )
    refreshed = ResearchRefreshService(registry.database).refresh(
        {
            "protocol": "research-refresh/v2",
            "mode": "enqueue",
            "idempotency_key": f"{key}-refresh",
            "entities": [{"kind": "claim", "id": ids["claim"]}],
        }
    )

    assert reviewed.current_state is not None
    assert reviewed.current_state.status == "contested"
    assert reviewed.current_state.conflict_state == "conflicted"
    assert {item.entity.kind for item in refreshed.items} == {"claim", "report"}
