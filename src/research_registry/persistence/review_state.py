from __future__ import annotations

from typing import Any

from ..db import DbConnection


DECISION_REVIEW_ACTIONS = (
    "approve",
    "contest",
    "reject",
    "supersede",
)
_DECISION_REVIEW_ACTIONS_SQL = ", ".join(
    f"'{action}'" for action in DECISION_REVIEW_ACTIONS
)


def effective_review_state_sql(
    *,
    entity_kind: str,
    entity_id_sql: str,
    fallback_sql: str,
) -> str:
    """Return the portable SQL expression for append-only review state."""
    if entity_kind not in {
        "claim_revision",
        "evidence",
        "source_version",
        "report",
    }:
        raise ValueError("unsupported review entity kind")
    return f"""
        COALESCE((
            SELECT re.to_state FROM review_events re
            WHERE re.entity_kind = '{entity_kind}'
              AND re.entity_id = {entity_id_sql}
              AND re.action IN ({_DECISION_REVIEW_ACTIONS_SQL})
            ORDER BY re.created_at DESC, re.id DESC
            LIMIT 1
        ), {fallback_sql})
    """.strip()


def latest_effective_review_state(
    conn: DbConnection,
    *,
    entity_kind: str,
    entity_id: str,
    fallback: str,
) -> str:
    """Read the latest semantic event state, or immutable record state."""
    row: Any | None = conn.execute(
        f"""
        SELECT to_state FROM review_events
        WHERE entity_kind = ? AND entity_id = ?
          AND action IN ({_DECISION_REVIEW_ACTIONS_SQL})
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (entity_kind, entity_id),
    ).fetchone()
    return row["to_state"] if row is not None else fallback
