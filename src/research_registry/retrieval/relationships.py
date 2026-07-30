from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..db import DatabaseTarget, connect_database, resolve_database_target
from .models import LexicalMatch


@dataclass(frozen=True)
class RelationshipMatch:
    score: float
    matched_by: tuple[str, ...]


def expand_relationships(
    database: str | Path | DatabaseTarget,
    anchors: Iterable[LexicalMatch],
    *,
    max_anchors: int = 20,
    max_related: int = 250,
) -> dict[str, RelationshipMatch]:
    target = (
        database
        if isinstance(database, DatabaseTarget)
        else resolve_database_target(database)
    )
    strong = [
        match
        for match in anchors
        if match.exact >= 0.8 or match.lexical >= 0.45
    ][:max_anchors]
    related: dict[str, RelationshipMatch] = {}
    with connect_database(target) as conn:
        for anchor in strong:
            if len(related) >= max_related:
                break
            _expand_one(conn, anchor, related, max_related=max_related)
    for anchor in strong:
        related.pop(anchor.document.id, None)
    return related


def _expand_one(conn, anchor, related, *, max_related: int) -> None:
    kind = anchor.document.kind
    record_id = anchor.document.id
    if kind == "claim":
        _add_rows(
            related,
            conn.execute(
                """
                SELECT ce.evidence_span_id AS id
                FROM claims c
                JOIN claim_evidence ce
                  ON ce.claim_revision_id = c.current_revision_id
                WHERE c.id = ?
                """,
                (record_id,),
            ).fetchall(),
            0.80,
            "relationship: claim to evidence",
            max_related,
        )
        _add_rows(
            related,
            conn.execute(
                "SELECT report_id AS id FROM report_claims WHERE claim_id = ?",
                (record_id,),
            ).fetchall(),
            0.80,
            "relationship: claim to report",
            max_related,
        )
        rows = conn.execute(
            """
            SELECT DISTINCT sv.id, sv.source_id
            FROM claims c
            JOIN claim_evidence ce
              ON ce.claim_revision_id = c.current_revision_id
            JOIN evidence_spans e ON e.id = ce.evidence_span_id
            JOIN source_versions sv ON sv.id = e.source_version_id
            WHERE c.id = ?
            """,
            (record_id,),
        ).fetchall()
        _add_dual(
            related,
            rows,
            0.55,
            "relationship: claim evidence source",
            max_related,
        )
    elif kind == "report":
        rows = conn.execute(
            "SELECT claim_id AS id FROM report_claims WHERE report_id = ?",
            (record_id,),
        ).fetchall()
        _add_rows(
            related,
            rows,
            0.80,
            "relationship: report to claim",
            max_related,
        )
    elif kind == "evidence":
        rows = conn.execute(
            """
            SELECT c.id
            FROM claim_evidence ce
            JOIN claims c ON c.current_revision_id = ce.claim_revision_id
            WHERE ce.evidence_span_id = ?
            """,
            (record_id,),
        ).fetchall()
        _add_rows(
            related,
            rows,
            0.80,
            "relationship: evidence to claim",
            max_related,
        )
        rows = conn.execute(
            """
            SELECT sv.id, sv.source_id
            FROM evidence_spans e
            JOIN source_versions sv ON sv.id = e.source_version_id
            WHERE e.id = ?
            """,
            (record_id,),
        ).fetchall()
        _add_dual(
            related,
            rows,
            0.70,
            "relationship: evidence to source",
            max_related,
        )
    elif kind == "source":
        _add_rows(
            related,
            conn.execute(
                "SELECT id FROM source_versions WHERE source_id = ?",
                (record_id,),
            ).fetchall(),
            0.75,
            "relationship: source to version",
            max_related,
        )
        _add_rows(
            related,
            conn.execute(
                """
                SELECT e.id
                FROM evidence_spans e
                JOIN source_versions sv ON sv.id = e.source_version_id
                WHERE sv.source_id = ?
                """,
                (record_id,),
            ).fetchall(),
            0.65,
            "relationship: source to evidence",
            max_related,
        )
    elif kind == "source_version":
        rows = conn.execute(
            "SELECT source_id FROM source_versions WHERE id = ?",
            (record_id,),
        ).fetchall()
        for row in rows:
            _add(
                related,
                row["source_id"],
                0.75,
                "relationship: version to source",
                max_related,
            )
        _add_rows(
            related,
            conn.execute(
                "SELECT id FROM evidence_spans WHERE source_version_id = ?",
                (record_id,),
            ).fetchall(),
            0.75,
            "relationship: version to evidence",
            max_related,
        )
        _add_rows(
            related,
            conn.execute(
                """
                SELECT newer.id
                FROM source_versions current
                JOIN source_versions newer
                  ON newer.source_id = current.source_id
                WHERE current.id = ? AND newer.id <> current.id
                ORDER BY newer.retrieved_at DESC
                LIMIT 20
                """,
                (record_id,),
            ).fetchall(),
            0.60,
            "relationship: newer source version",
            max_related,
        )
    elif kind == "question":
        for table, reason in (
            ("claims", "relationship: question to claim"),
            ("reports", "relationship: question to report"),
            ("evidence_spans", "relationship: question to evidence"),
        ):
            _add_rows(
                related,
                conn.execute(
                    f"SELECT id FROM {table} WHERE question_id = ?",
                    (record_id,),
                ).fetchall(),
                0.70,
                reason,
                max_related,
            )


def _add_dual(
    related,
    rows,
    score: float,
    reason: str,
    max_related: int,
) -> None:
    for row in rows:
        _add(related, row["id"], score, reason, max_related)
        _add(related, row["source_id"], score, reason, max_related)


def _add_rows(
    related,
    rows,
    score: float,
    reason: str,
    max_related: int,
) -> None:
    for row in rows:
        _add(related, row["id"], score, reason, max_related)


def _add(
    related: dict[str, RelationshipMatch],
    record_id: str,
    score: float,
    reason: str,
    max_related: int,
) -> None:
    if record_id not in related and len(related) >= max_related:
        return
    current = related.get(record_id)
    current_reasons = current.matched_by if current else ()
    related[record_id] = RelationshipMatch(
        score=max(score, current.score if current else 0.0),
        matched_by=tuple(dict.fromkeys((*current_reasons, reason))),
    )
