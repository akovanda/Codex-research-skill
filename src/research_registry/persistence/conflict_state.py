from __future__ import annotations

from typing import Any

from ..db import DbConnection
from .review_state import DECISION_REVIEW_ACTIONS


CONFLICT_STATES = ("none", "conflicted")
_DECISION_ACTIONS_SQL = ", ".join(
    f"'{action}'" for action in DECISION_REVIEW_ACTIONS
)
_LEGACY_CONFLICTED_MIGRATION_SQL = (
    "actor_type = 'migration' AND action = 'contest' "
    "AND to_state = 'conflicted'"
)


def effective_conflict_state_sql(
    *,
    entity_kind: str,
    entity_id_sql: str,
) -> str:
    """Return the conflict state from the latest exact decision event."""
    if entity_kind not in {
        "claim_revision",
        "evidence",
        "source_version",
        "report",
    }:
        raise ValueError("unsupported conflict entity kind")
    return f"""
        COALESCE((
            SELECT CASE
                WHEN re.action = 'contest' THEN 'conflicted'
                ELSE 'none'
            END
            FROM review_events re
            WHERE re.entity_kind = '{entity_kind}'
              AND re.entity_id = {entity_id_sql}
              AND re.action IN ({_DECISION_ACTIONS_SQL})
            ORDER BY
                re.created_at DESC,
                CASE WHEN re.{_LEGACY_CONFLICTED_MIGRATION_SQL}
                     THEN 1 ELSE 0 END DESC,
                re.id DESC
            LIMIT 1
        ), 'none')
    """.strip()


def claim_revision_conflict_state_sql(
    *,
    revision_id_sql: str,
    status_sql: str,
) -> str:
    """Derive one claim revision's conflict state from exact inputs."""
    return f"""
        CASE
            WHEN {status_sql} = 'contested'
              OR EXISTS (
                  SELECT 1
                  FROM claim_evidence conflict_ce
                  WHERE conflict_ce.claim_revision_id = {revision_id_sql}
                    AND conflict_ce.relationship = 'refutes'
              )
              OR {effective_conflict_state_sql(
                  entity_kind="claim_revision",
                  entity_id_sql=revision_id_sql,
              )} = 'conflicted'
            THEN 'conflicted'
            ELSE 'none'
        END
    """.strip()


def latest_effective_conflict_state(
    conn: DbConnection,
    *,
    entity_kind: str,
    entity_id: str,
) -> str:
    """Read the latest exact conflict decision, defaulting to no conflict."""
    if entity_kind not in {
        "claim_revision",
        "evidence",
        "source_version",
        "report",
    }:
        raise ValueError("unsupported conflict entity kind")
    row: Any | None = conn.execute(
        f"""
        SELECT action
        FROM review_events
        WHERE entity_kind = ? AND entity_id = ?
          AND action IN ({_DECISION_ACTIONS_SQL})
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
        "conflicted"
        if row is not None and row["action"] == "contest"
        else "none"
    )


def latest_claim_revision_conflict_state(
    conn: DbConnection,
    *,
    revision_id: str,
    status: str,
) -> str:
    """Derive one exact claim revision's conflict state."""
    if status == "contested":
        return "conflicted"
    refuting = conn.execute(
        """
        SELECT 1 AS present
        FROM claim_evidence
        WHERE claim_revision_id = ? AND relationship = 'refutes'
        LIMIT 1
        """,
        (revision_id,),
    ).fetchone()
    if refuting is not None:
        return "conflicted"
    return latest_effective_conflict_state(
        conn,
        entity_kind="claim_revision",
        entity_id=revision_id,
    )
