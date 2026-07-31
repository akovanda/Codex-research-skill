from __future__ import annotations

from typing import Any

from ..db import DbConnection


DECISION_REVIEW_ACTIONS = (
    "approve",
    "contest",
    "reject",
    "supersede",
)
REVIEW_STATES = ("unreviewed", "reviewed", "flagged")
_DECISION_REVIEW_ACTIONS_SQL = ", ".join(
    f"'{action}'" for action in DECISION_REVIEW_ACTIONS
)
_REVIEW_STATES_SQL = ", ".join(f"'{state}'" for state in REVIEW_STATES)
_LEGACY_CONFLICTED_MIGRATION_SQL = (
    "actor_type = 'migration' AND action = 'contest' "
    "AND to_state = 'conflicted'"
)


def normalize_review_state(value: str | None) -> str:
    """Return a closed v2 review state for a persisted or legacy value."""
    return value if value in REVIEW_STATES else "unreviewed"


def normalize_review_state_sql(state_sql: str) -> str:
    """Return the portable SQL expression for a closed v2 review state."""
    return (
        f"CASE WHEN {state_sql} IN ({_REVIEW_STATES_SQL}) "
        f"THEN {state_sql} ELSE 'unreviewed' END"
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
    normalized_fallback = normalize_review_state_sql(fallback_sql)
    return f"""
        COALESCE((
            SELECT CASE
                WHEN re.{_LEGACY_CONFLICTED_MIGRATION_SQL}
                THEN 'flagged'
                ELSE re.to_state
            END
            FROM review_events re
            WHERE re.entity_kind = '{entity_kind}'
              AND re.entity_id = {entity_id_sql}
              AND re.action IN ({_DECISION_REVIEW_ACTIONS_SQL})
              AND (
                  re.to_state IN ({_REVIEW_STATES_SQL})
                  OR re.{_LEGACY_CONFLICTED_MIGRATION_SQL}
              )
            ORDER BY
                re.created_at DESC,
                CASE WHEN re.{_LEGACY_CONFLICTED_MIGRATION_SQL}
                     THEN 1 ELSE 0 END DESC,
                re.id DESC
            LIMIT 1
        ), {normalized_fallback})
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
        SELECT
            CASE WHEN {_LEGACY_CONFLICTED_MIGRATION_SQL}
                 THEN 'flagged' ELSE to_state END AS to_state
        FROM review_events
        WHERE entity_kind = ? AND entity_id = ?
          AND action IN ({_DECISION_REVIEW_ACTIONS_SQL})
          AND (
              to_state IN ({_REVIEW_STATES_SQL})
              OR ({_LEGACY_CONFLICTED_MIGRATION_SQL})
          )
        ORDER BY
            created_at DESC,
            CASE WHEN {_LEGACY_CONFLICTED_MIGRATION_SQL}
                 THEN 1 ELSE 0 END DESC,
            id DESC
        LIMIT 1
        """,
        (entity_kind, entity_id),
    ).fetchone()
    return (
        normalize_review_state(row["to_state"] if row is not None else fallback)
    )
