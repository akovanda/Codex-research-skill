from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Iterable

from ..db import DatabaseTarget, connect_database, resolve_database_target
from ..retrieval.lexical import create_lexical_adapter
from ..retrieval.models import SearchDocument
from ..retrieval.relationships import expand_relationships
from ..timestamps import freshness_case, utc_text


@dataclass(frozen=True)
class ReadAccess:
    include_private: bool
    namespace_kind: str | None = None
    namespace_id: str | None = None
    is_admin: bool = False
    local_trusted: bool = False


@dataclass(frozen=True)
class RetrievalCandidate:
    id: str
    kind: str
    title: str
    summary: str
    search_text: str
    review_state: str | None
    conflict_state: str | None
    freshness: str | None
    evidence_count: int
    updated_at: str | None
    created_at: str | None
    url: str | None
    status: str | None = None
    repository: str | None = None
    path: str | None = None
    topic_id: str | None = None
    source_type: str | None = None
    trust_tier: str | None = None
    exact_score: float = 0.0
    lexical_score: float = 0.0
    relationship_score: float = 0.0
    matched_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReadRecord:
    id: str
    kind: str
    title: str
    text: str
    url: str | None
    review_state: str | None
    conflict_state: str | None
    freshness: str | None
    updated_at: str | None
    data: dict[str, Any]


class CurrentRetrievalAdapter:
    """V2 read adapter with dialect-owned FTS and portable hydration."""

    def __init__(
        self,
        database: str | Path | DatabaseTarget,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.database = (
            database
            if isinstance(database, DatabaseTarget)
            else resolve_database_target(database)
        )
        self.clock = clock or (
            lambda: datetime.now(timezone.utc).replace(microsecond=0)
        )
        self.lexical = create_lexical_adapter(self.database)

    @property
    def database_type(self) -> str:
        return self.database.kind

    def search_candidates(
        self,
        query: str,
        *,
        access: ReadAccess,
        max_candidates: int = 6_006,
    ) -> list[RetrievalCandidate]:
        matches = self.lexical.search(
            query,
            access=access,
            limit=max_candidates,
        )
        relationships = expand_relationships(self.database, matches)
        related_documents = {
            document.id: document
            for document in self.lexical.fetch(
                relationships,
                access=access,
            )
        }
        candidates: dict[str, RetrievalCandidate] = {}
        for match in matches:
            relationship = relationships.get(match.document.id)
            candidates[match.document.id] = self._candidate_from_document(
                match.document,
                exact_score=match.exact,
                lexical_score=match.lexical,
                relationship_score=(
                    relationship.score if relationship is not None else 0.0
                ),
                matched_by=(
                    tuple(
                        dict.fromkeys(
                            (*match.matched_by, *relationship.matched_by)
                        )
                    )
                    if relationship is not None
                    else match.matched_by
                ),
            )
        for document_id, relationship in relationships.items():
            if document_id in candidates:
                continue
            document = related_documents.get(document_id)
            if document is None:
                continue
            candidates[document_id] = self._candidate_from_document(
                document,
                relationship_score=relationship.score,
                matched_by=relationship.matched_by,
            )
        return list(candidates.values())[:max_candidates]

    def list_candidates(
        self,
        *,
        kinds: Iterable[str],
        access: ReadAccess,
        max_per_kind: int = 1_001,
    ) -> list[RetrievalCandidate]:
        requested = set(kinds) or {
            "question",
            "source",
            "source_version",
            "evidence",
            "claim",
            "report",
        }
        candidates: list[RetrievalCandidate] = []
        with connect_database(self.database) as conn:
            for kind in (
                "question",
                "source",
                "source_version",
                "evidence",
                "claim",
                "report",
            ):
                if kind not in requested:
                    continue
                clause, parameters = self._access_clause(
                    self._visibility_alias(kind), access
                )
                rows = conn.execute(
                    self._candidate_sql(kind, clause),
                    (
                        (utc_text(self.clock()),)
                        if kind == "source"
                        else ()
                    )
                    + (*parameters, max_per_kind),
                ).fetchall()
                candidates.extend(
                    self._candidate_from_row(kind, row) for row in rows
                )
        return candidates

    def get_record(
        self, record_id: str, *, access: ReadAccess
    ) -> ReadRecord | None:
        with connect_database(self.database) as conn:
            for kind in (
                "claim",
                "report",
                "evidence",
                "source_version",
                "source",
                "question",
                "refresh",
            ):
                alias = self._visibility_alias(kind)
                clause, parameters = self._access_clause(alias, access)
                row = conn.execute(
                    self._record_sql(kind, clause),
                    (
                        (
                            utc_text(self.clock()),
                            record_id,
                            *parameters,
                        )
                        if kind == "source"
                        else (record_id, *parameters)
                    ),
                ).fetchone()
                if row is not None:
                    return self._record_from_row(kind, row)
        return None

    def get_current_revision(self, claim_id: str) -> dict[str, Any] | None:
        with connect_database(self.database) as conn:
            row = conn.execute(
                """
                SELECT cr.*
                FROM claims c
                JOIN claim_revisions cr ON cr.id = c.current_revision_id
                WHERE c.id = ?
                """,
                (claim_id,),
            ).fetchone()
        return self._revision(row) if row is not None else None

    def list_revisions(self, claim_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with connect_database(self.database) as conn:
            rows = conn.execute(
                """
                SELECT * FROM claim_revisions
                WHERE claim_id = ?
                ORDER BY revision_number DESC, id ASC
                LIMIT ?
                """,
                (claim_id, limit),
            ).fetchall()
        return [self._revision(row) for row in rows]

    def list_evidence(
        self,
        record: ReadRecord,
        *,
        access: ReadAccess,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clause, parameters = self._access_clause("s", access)
        relationship = "NULL AS relationship, NULL AS rationale, 1.0 AS weight"
        if record.kind == "claim":
            joins = """
                JOIN claim_evidence ce ON ce.evidence_span_id = e.id
                JOIN claims c ON c.current_revision_id = ce.claim_revision_id
            """
            where = "c.id = ?"
            relationship = "ce.relationship, ce.rationale, ce.weight"
        elif record.kind == "report":
            joins = """
                JOIN claim_evidence ce ON ce.evidence_span_id = e.id
                JOIN claims c ON c.current_revision_id = ce.claim_revision_id
                JOIN report_claims rc ON rc.claim_id = c.id
            """
            where = "rc.report_id = ?"
            relationship = "ce.relationship, ce.rationale, ce.weight"
        elif record.kind == "source":
            joins = ""
            where = "sv.source_id = ?"
        elif record.kind == "source_version":
            joins = ""
            where = "e.source_version_id = ?"
        elif record.kind == "question":
            joins = ""
            where = "e.question_id = ?"
        elif record.kind == "evidence":
            joins = ""
            where = "e.id = ?"
        else:
            return []
        with connect_database(self.database) as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT
                    e.id, e.source_version_id, e.quote_text, e.selector_type,
                    e.selector_json, e.note, e.confidence, e.anchor_state,
                    e.review_state, e.trust_tier, e.created_at,
                    sv.source_id, s.title AS source_title,
                    s.locator AS source_url, {relationship}
                FROM evidence_spans e
                JOIN source_versions sv ON sv.id = e.source_version_id
                JOIN sources s ON s.id = sv.source_id
                {joins}
                WHERE {where} AND {clause}
                ORDER BY e.created_at DESC, e.id ASC
                LIMIT ?
                """,
                (record.id, *parameters, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "source_version_id": row["source_version_id"],
                "source_id": row["source_id"],
                "source_title": row["source_title"],
                "source_url": row["source_url"],
                "quote_text": row["quote_text"],
                "selector_type": row["selector_type"],
                "selector": self._json(row["selector_json"]),
                "note": row["note"],
                "confidence": float(row["confidence"]),
                "anchor_state": row["anchor_state"],
                "review_state": row["review_state"],
                "trust_tier": row["trust_tier"],
                "relationship": row["relationship"],
                "rationale": row["rationale"],
                "weight": float(row["weight"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_source_versions(
        self,
        *,
        source_id: str | None = None,
        version_ids: Iterable[str] = (),
        access: ReadAccess,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(version_ids))
        if source_id is None and not ids:
            return []
        clause, parameters = self._access_clause("s", access)
        selectors: list[str] = []
        selector_parameters: list[Any] = []
        if source_id is not None:
            selectors.append("sv.source_id = ?")
            selector_parameters.append(source_id)
        if ids:
            selectors.append(
                "sv.id IN (" + ",".join("?" for _ in ids) + ")"
            )
            selector_parameters.extend(ids)
        with connect_database(self.database) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    sv.*, s.title AS source_title, s.locator AS source_url,
                    s.source_type, s.review_state, s.trust_tier,
                    s.conflict_state
                FROM source_versions sv
                JOIN sources s ON s.id = sv.source_id
                WHERE ({' OR '.join(selectors)}) AND {clause}
                ORDER BY sv.retrieved_at DESC, sv.id ASC
                LIMIT ?
                """,
                (*selector_parameters, *parameters, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "source_id": row["source_id"],
                "source_title": row["source_title"],
                "source_url": row["source_url"],
                "source_type": row["source_type"],
                "version_key": row["version_key"],
                "version_kind": row["version_kind"],
                "retrieved_at": row["retrieved_at"],
                "published_at": row["published_at"],
                "content_sha256": row["content_sha256"],
                "canonical_locator": row["canonical_locator"],
                "media_type": row["media_type"],
                "byte_count": row["byte_count"],
                "parser_name": row["parser_name"],
                "parser_version": row["parser_version"],
                "repository": row["repository_locator"],
                "commit_sha": row["commit_sha"],
                "blob_sha": row["blob_sha"],
                "path": row["path"],
                "snapshot_policy": self._json_object(
                    row["metadata_json"]
                ).get("snapshot_policy"),
                "snapshot_available": row["content_object_id"] is not None,
                "review_state": row["review_state"],
                "trust_tier": row["trust_tier"],
                "conflict_state": row["conflict_state"],
            }
            for row in rows
        ]

    def list_claim_revisions_for_evidence(
        self,
        evidence_id: str,
        *,
        access: ReadAccess,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clause, parameters = self._access_clause("c", access)
        with connect_database(self.database) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    c.id AS claim_id, cr.id AS revision_id,
                    cr.revision_number, cr.title, cr.status,
                    ce.relationship, ce.rationale, ce.weight,
                    c.review_state, c.conflict_state
                FROM claim_evidence ce
                JOIN claim_revisions cr ON cr.id = ce.claim_revision_id
                JOIN claims c ON c.id = cr.claim_id
                WHERE ce.evidence_span_id = ? AND {clause}
                ORDER BY cr.created_at DESC, cr.id ASC
                LIMIT ?
                """,
                (evidence_id, *parameters, limit),
            ).fetchall()
        return [
            {
                "claim_id": row["claim_id"],
                "revision_id": row["revision_id"],
                "revision_number": int(row["revision_number"]),
                "title": row["title"],
                "status": row["status"],
                "relationship": row["relationship"],
                "rationale": row["rationale"],
                "weight": float(row["weight"]),
                "review_state": row["review_state"],
                "conflict_state": row["conflict_state"],
            }
            for row in rows
        ]

    def list_refresh_queue(
        self,
        *,
        access: ReadAccess,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clause, parameters = self._access_clause("owner", access)
        with connect_database(self.database) as conn:
            rows = conn.execute(
                f"""
                SELECT rq.*, owner.title
                FROM refresh_queue rq
                JOIN (
                    SELECT 'source' AS entity_kind, s.id, s.title,
                           s.visibility, s.namespace_kind, s.namespace_id,
                           s.public_index_state
                    FROM sources s
                    UNION ALL
                    SELECT 'evidence', e.id, s.title,
                           s.visibility, s.namespace_kind, s.namespace_id,
                           s.public_index_state
                    FROM evidence_spans e
                    JOIN source_versions sv ON sv.id = e.source_version_id
                    JOIN sources s ON s.id = sv.source_id
                    UNION ALL
                    SELECT 'claim', c.id, c.title,
                           c.visibility, c.namespace_kind, c.namespace_id,
                           c.public_index_state
                    FROM claims c
                    UNION ALL
                    SELECT 'report', r.id, r.title,
                           r.visibility, r.namespace_kind, r.namespace_id,
                           r.public_index_state
                    FROM reports r
                ) owner
                  ON owner.entity_kind = rq.entity_kind
                 AND owner.id = rq.entity_id
                WHERE {clause}
                ORDER BY rq.priority DESC, rq.detected_at DESC, rq.id ASC
                LIMIT ?
                """,
                (*parameters, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "entity_kind": row["entity_kind"],
                "entity_id": row["entity_id"],
                "title": row["title"],
                "reason": row["reason"],
                "status": row["status"],
                "priority": float(row["priority"]),
                "detected_at": row["detected_at"],
                "resolved_at": row["resolved_at"],
                "details": self._json(row["details_json"]) or {},
            }
            for row in rows
        ]

    def get_deposit_receipt(
        self,
        key: str,
        *,
        namespace_kind: str,
        namespace_id: str,
    ) -> dict[str, Any] | None:
        with connect_database(self.database) as conn:
            row = conn.execute(
                """
                SELECT response_json FROM idempotency_keys
                WHERE namespace_kind = ?
                  AND namespace_id = ?
                  AND operation = 'research_deposit_v2'
                  AND "key" = ?
                """,
                (namespace_kind, namespace_id, key),
            ).fetchone()
        if row is None:
            return None
        value = self._json(row["response_json"])
        return value if isinstance(value, dict) and "protocol" in value else None

    def list_reviews(
        self,
        *,
        entity_ids: Iterable[str],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(entity_ids))
        if not ids:
            return []
        with connect_database(self.database) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM review_events
                WHERE entity_id IN ({','.join('?' for _ in ids)})
                ORDER BY created_at DESC, id ASC
                LIMIT ?
                """,
                (*ids, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "entity_kind": row["entity_kind"],
                "entity_id": row["entity_id"],
                "action": row["action"],
                "from_state": row["from_state"],
                "to_state": row["to_state"],
                "note": row["note"],
                "actor_type": row["actor_type"],
                "actor_id": row["actor_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_reports(
        self,
        record: ReadRecord,
        *,
        access: ReadAccess,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if record.kind not in {"claim", "question"}:
            return []
        clause, parameters = self._access_clause("r", access)
        if record.kind == "claim":
            join = "JOIN report_claims rc ON rc.report_id = r.id"
            where = "rc.claim_id = ?"
        else:
            join = ""
            where = "r.question_id = ?"
        with connect_database(self.database) as conn:
            rows = conn.execute(
                f"""
                SELECT r.*
                FROM reports r {join}
                WHERE {where} AND {clause}
                ORDER BY r.created_at DESC, r.id ASC
                LIMIT ?
                """,
                (record.id, *parameters, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "summary": row["summary_md"][:4_000],
                "report_kind": row["report_kind"],
                "review_state": row["review_state"],
                "conflict_state": row["conflict_state"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_refresh(
        self, record: ReadRecord, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        entity_kind = "evidence" if record.kind == "evidence" else record.kind
        with connect_database(self.database) as conn:
            rows = conn.execute(
                """
                SELECT * FROM refresh_queue
                WHERE entity_kind = ? AND entity_id = ?
                ORDER BY detected_at DESC, id ASC
                LIMIT ?
                """,
                (entity_kind, record.id, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "entity_kind": row["entity_kind"],
                "entity_id": row["entity_id"],
                "reason": row["reason"],
                "status": row["status"],
                "priority": float(row["priority"]),
                "detected_at": row["detected_at"],
                "resolved_at": row["resolved_at"],
            }
            for row in rows
        ]

    def status_counts(self) -> tuple[str, dict[str, int], str]:
        with connect_database(self.database) as conn:
            migration_rows = conn.execute(
                "SELECT migration_id FROM schema_migrations ORDER BY migration_id"
            ).fetchall()
            backlog = {
                state: int(
                    conn.execute(
                        "SELECT COUNT(*) AS count FROM refresh_queue WHERE status = ?",
                        (state,),
                    ).fetchone()["count"]
                )
                for state in ("pending", "running", "failed")
            }
        schema_version = (
            migration_rows[-1]["migration_id"] if migration_rows else "uninitialized"
        )
        return schema_version, backlog, "current"

    @staticmethod
    def _visibility_alias(kind: str) -> str:
        if kind in {"source_version", "evidence"}:
            return "s"
        if kind == "refresh":
            return "owner"
        return {
            "question": "q",
            "source": "s",
            "claim": "c",
            "report": "r",
        }.get(kind, "owner")

    @staticmethod
    def _access_clause(alias: str, access: ReadAccess) -> tuple[str, tuple[Any, ...]]:
        if access.include_private and (access.local_trusted or access.is_admin):
            return "1 = 1", ()
        namespace = (
            access.namespace_kind is not None and access.namespace_id is not None
        )
        if access.include_private and namespace:
            return (
                f"(({alias}.namespace_kind = ? AND {alias}.namespace_id = ?) "
                f"OR ({alias}.visibility = 'public' AND "
                f"({alias}.public_index_state = 'included' OR "
                f"({alias}.namespace_kind = ? AND {alias}.namespace_id = ?))))",
                (
                    access.namespace_kind,
                    access.namespace_id,
                    access.namespace_kind,
                    access.namespace_id,
                ),
            )
        if namespace:
            return (
                f"({alias}.visibility = 'public' AND "
                f"({alias}.public_index_state = 'included' OR "
                f"({alias}.namespace_kind = ? AND {alias}.namespace_id = ?)))",
                (access.namespace_kind, access.namespace_id),
            )
        return (
            f"({alias}.visibility = 'public' "
            f"AND {alias}.public_index_state = 'included')",
            (),
        )

    def _candidate_sql(self, kind: str, access_clause: str) -> str:
        source_freshness = freshness_case(
            "s.refresh_due_at", dialect=self.database.kind
        )
        sql = {
            "question": f"""
                SELECT q.id, q.prompt AS title, q.prompt AS summary,
                    q.normalized_prompt AS search_text,
                    CASE WHEN q.human_reviewed = 1 THEN 'reviewed'
                         ELSE 'unreviewed' END AS review_state,
                    NULL AS conflict_state,
                    COALESCE((
                        SELECT rs.freshness_state FROM research_sessions rs
                        WHERE rs.question_id = q.id
                        ORDER BY rs.created_at DESC LIMIT 1
                    ), 'unknown') AS freshness,
                    (SELECT COUNT(*) FROM evidence_spans e
                     WHERE e.question_id = q.id) AS evidence_count,
                    q.created_at AS updated_at, q.created_at,
                    NULL AS url, q.status, NULL AS repository,
                    NULL AS path, q.topic_id, NULL AS source_type
                FROM questions q
                WHERE {access_clause}
                ORDER BY q.created_at DESC, q.id ASC
                LIMIT ?
            """,
            "source": f"""
                SELECT s.id, s.title, COALESCE(s.snippet, s.locator) AS summary,
                    (s.title || ' ' || s.locator || ' ' ||
                     COALESCE(s.snippet, '')) AS search_text,
                    s.review_state, s.conflict_state,
                    {source_freshness} AS freshness,
                    (SELECT COUNT(*) FROM evidence_spans e
                     JOIN source_versions sv ON sv.id = e.source_version_id
                     WHERE sv.source_id = s.id) AS evidence_count,
                    COALESCE(s.last_verified_at, s.created_at) AS updated_at,
                    s.created_at, s.locator AS url, NULL AS status,
                    NULL AS repository, NULL AS path, NULL AS topic_id,
                    s.source_type
                FROM sources s
                WHERE {access_clause}
                ORDER BY s.created_at DESC, s.id ASC
                LIMIT ?
            """,
            "source_version": f"""
                SELECT sv.id, s.title, sv.canonical_locator AS summary,
                    (s.title || ' ' || sv.canonical_locator || ' ' ||
                     COALESCE(sv.repository_locator, '') || ' ' ||
                     COALESCE(sv.path, '')) AS search_text,
                    s.review_state, s.conflict_state, 'unknown' AS freshness,
                    (SELECT COUNT(*) FROM evidence_spans e
                     WHERE e.source_version_id = sv.id) AS evidence_count,
                    sv.retrieved_at AS updated_at, sv.created_at,
                    sv.canonical_locator AS url, NULL AS status,
                    sv.repository_locator AS repository, sv.path,
                    NULL AS topic_id, s.source_type
                FROM source_versions sv
                JOIN sources s ON s.id = sv.source_id
                WHERE {access_clause}
                ORDER BY sv.retrieved_at DESC, sv.id ASC
                LIMIT ?
            """,
            "evidence": f"""
                SELECT e.id, s.title,
                    substr(e.quote_text, 1, 4000) AS summary,
                    (e.quote_text || ' ' || COALESCE(e.note, '') || ' ' ||
                     s.title || ' ' || s.locator) AS search_text,
                    e.review_state, s.conflict_state,
                    CASE WHEN e.anchor_state = 'stale' THEN 'stale'
                         WHEN e.anchor_state = 'resolved' THEN 'fresh'
                         ELSE 'unknown' END AS freshness,
                    1 AS evidence_count, e.created_at AS updated_at,
                    e.created_at, s.locator AS url, NULL AS status,
                    sv.repository_locator AS repository, sv.path,
                    e.topic_id, s.source_type
                FROM evidence_spans e
                JOIN source_versions sv ON sv.id = e.source_version_id
                JOIN sources s ON s.id = sv.source_id
                WHERE {access_clause}
                ORDER BY e.created_at DESC, e.id ASC
                LIMIT ?
            """,
            "claim": f"""
                SELECT c.id, COALESCE(cr.title, c.title) AS title,
                    substr(COALESCE(cr.statement, c.statement), 1, 4000) AS summary,
                    (COALESCE(cr.title, c.title) || ' ' ||
                     COALESCE(cr.statement, c.statement) || ' ' ||
                     COALESCE(c.canonical_key, '')) AS search_text,
                    c.review_state,
                    CASE
                        WHEN COALESCE(cr.status, c.status) = 'contested'
                          OR EXISTS (
                              SELECT 1 FROM claim_evidence conflict_ce
                              WHERE conflict_ce.claim_revision_id =
                                    c.current_revision_id
                                AND conflict_ce.relationship = 'refutes'
                          )
                        THEN 'conflicted'
                        ELSE c.conflict_state
                    END AS conflict_state,
                    CASE
                        WHEN EXISTS (
                            SELECT 1 FROM claim_evidence stale_ce
                            JOIN evidence_spans stale_e
                              ON stale_e.id = stale_ce.evidence_span_id
                            WHERE stale_ce.claim_revision_id =
                                  c.current_revision_id
                              AND stale_e.anchor_state = 'stale'
                        )
                        OR EXISTS (
                            SELECT 1 FROM refresh_queue failed_rq
                            WHERE failed_rq.status = 'failed'
                              AND (
                                  (failed_rq.entity_kind = 'claim'
                                   AND failed_rq.entity_id = c.id)
                                  OR (
                                      failed_rq.entity_kind = 'evidence'
                                      AND EXISTS (
                                          SELECT 1
                                          FROM claim_evidence failed_ce
                                          WHERE failed_ce.claim_revision_id =
                                                c.current_revision_id
                                            AND failed_ce.evidence_span_id =
                                                failed_rq.entity_id
                                      )
                                  )
                              )
                        )
                        THEN 'stale'
                        WHEN EXISTS (
                            SELECT 1 FROM refresh_queue pending_rq
                            WHERE pending_rq.status IN ('pending', 'running')
                              AND (
                                  (pending_rq.entity_kind = 'claim'
                                   AND pending_rq.entity_id = c.id)
                                  OR (
                                      pending_rq.entity_kind = 'evidence'
                                      AND EXISTS (
                                          SELECT 1
                                          FROM claim_evidence pending_ce
                                          WHERE pending_ce.claim_revision_id =
                                                c.current_revision_id
                                            AND pending_ce.evidence_span_id =
                                                pending_rq.entity_id
                                      )
                                  )
                              )
                        )
                        THEN 'needs_refresh'
                        ELSE COALESCE(rs.freshness_state, 'unknown')
                    END AS freshness,
                    (SELECT COUNT(*) FROM claim_evidence ce
                     WHERE ce.claim_revision_id = c.current_revision_id)
                     AS evidence_count,
                    COALESCE(c.updated_at, c.created_at) AS updated_at,
                    c.created_at, NULL AS url,
                    COALESCE(cr.status, c.status) AS status,
                    NULL AS repository, NULL AS path, c.topic_id,
                    NULL AS source_type
                FROM claims c
                LEFT JOIN claim_revisions cr ON cr.id = c.current_revision_id
                LEFT JOIN research_sessions rs ON rs.id = c.session_id
                WHERE {access_clause}
                ORDER BY COALESCE(c.updated_at, c.created_at) DESC, c.id ASC
                LIMIT ?
            """,
            "report": f"""
                SELECT r.id, r.title,
                    substr(r.summary_md, 1, 4000) AS summary,
                    (r.title || ' ' || r.summary_md) AS search_text,
                    r.review_state, r.conflict_state,
                    COALESCE(rs.freshness_state, 'unknown') AS freshness,
                    (SELECT COUNT(*) FROM report_claims rc
                     JOIN claims c ON c.id = rc.claim_id
                     JOIN claim_evidence ce
                       ON ce.claim_revision_id = c.current_revision_id
                     WHERE rc.report_id = r.id) AS evidence_count,
                    r.created_at AS updated_at, r.created_at,
                    NULL AS url, NULL AS status, NULL AS repository,
                    NULL AS path, NULL AS topic_id, NULL AS source_type
                FROM reports r
                LEFT JOIN research_sessions rs ON rs.id = r.session_id
                WHERE {access_clause}
                ORDER BY r.created_at DESC, r.id ASC
                LIMIT ?
            """,
        }
        return sql[kind]

    def _record_sql(self, kind: str, access_clause: str) -> str:
        source_freshness = freshness_case(
            "s.refresh_due_at", dialect=self.database.kind
        )
        sql = {
            "claim": f"""
                SELECT c.*, COALESCE(cr.title, c.title) AS read_title,
                    COALESCE(cr.statement, c.statement) AS read_text,
                    COALESCE(cr.status, c.status) AS revision_status,
                    CASE
                        WHEN COALESCE(cr.status, c.status) = 'contested'
                          OR EXISTS (
                              SELECT 1 FROM claim_evidence conflict_ce
                              WHERE conflict_ce.claim_revision_id =
                                    c.current_revision_id
                                AND conflict_ce.relationship = 'refutes'
                          )
                        THEN 'conflicted'
                        ELSE c.conflict_state
                    END AS derived_conflict_state,
                    CASE
                        WHEN EXISTS (
                            SELECT 1 FROM claim_evidence stale_ce
                            JOIN evidence_spans stale_e
                              ON stale_e.id = stale_ce.evidence_span_id
                            WHERE stale_ce.claim_revision_id =
                                  c.current_revision_id
                              AND stale_e.anchor_state = 'stale'
                        )
                        OR EXISTS (
                            SELECT 1 FROM refresh_queue failed_rq
                            WHERE failed_rq.status = 'failed'
                              AND (
                                  (failed_rq.entity_kind = 'claim'
                                   AND failed_rq.entity_id = c.id)
                                  OR (
                                      failed_rq.entity_kind = 'evidence'
                                      AND EXISTS (
                                          SELECT 1
                                          FROM claim_evidence failed_ce
                                          WHERE failed_ce.claim_revision_id =
                                                c.current_revision_id
                                            AND failed_ce.evidence_span_id =
                                                failed_rq.entity_id
                                      )
                                  )
                              )
                        )
                        THEN 'stale'
                        WHEN EXISTS (
                            SELECT 1 FROM refresh_queue pending_rq
                            WHERE pending_rq.status IN ('pending', 'running')
                              AND (
                                  (pending_rq.entity_kind = 'claim'
                                   AND pending_rq.entity_id = c.id)
                                  OR (
                                      pending_rq.entity_kind = 'evidence'
                                      AND EXISTS (
                                          SELECT 1
                                          FROM claim_evidence pending_ce
                                          WHERE pending_ce.claim_revision_id =
                                                c.current_revision_id
                                            AND pending_ce.evidence_span_id =
                                                pending_rq.entity_id
                                      )
                                  )
                              )
                        )
                        THEN 'needs_refresh'
                        ELSE COALESCE(rs.freshness_state, 'unknown')
                    END AS freshness
                FROM claims c
                LEFT JOIN claim_revisions cr ON cr.id = c.current_revision_id
                LEFT JOIN research_sessions rs ON rs.id = c.session_id
                WHERE c.id = ? AND {access_clause}
            """,
            "report": f"""
                SELECT r.*, r.title AS read_title, r.summary_md AS read_text,
                    COALESCE(rs.freshness_state, 'unknown') AS freshness
                FROM reports r
                LEFT JOIN research_sessions rs ON rs.id = r.session_id
                WHERE r.id = ? AND {access_clause}
            """,
            "evidence": f"""
                SELECT e.*, s.id AS source_id, s.title AS source_title,
                    s.locator AS source_url, s.conflict_state,
                    s.title AS read_title, e.quote_text AS read_text,
                    CASE WHEN e.anchor_state = 'stale' THEN 'stale'
                         WHEN e.anchor_state = 'resolved' THEN 'fresh'
                         ELSE 'unknown' END AS freshness
                FROM evidence_spans e
                JOIN source_versions sv ON sv.id = e.source_version_id
                JOIN sources s ON s.id = sv.source_id
                WHERE e.id = ? AND {access_clause}
            """,
            "source_version": f"""
                SELECT sv.*, s.title AS source_title, s.locator AS source_url,
                    s.review_state, s.conflict_state,
                    s.title AS read_title,
                    sv.canonical_locator AS read_text,
                    'unknown' AS freshness
                FROM source_versions sv
                JOIN sources s ON s.id = sv.source_id
                WHERE sv.id = ? AND {access_clause}
            """,
            "source": f"""
                SELECT s.*, s.title AS read_title,
                    COALESCE(s.snippet, s.locator) AS read_text,
                    {source_freshness} AS freshness
                FROM sources s
                WHERE s.id = ? AND {access_clause}
            """,
            "question": f"""
                SELECT q.*, q.prompt AS read_title, q.prompt AS read_text,
                    CASE WHEN q.human_reviewed = 1 THEN 'reviewed'
                         ELSE 'unreviewed' END AS review_state,
                    NULL AS conflict_state,
                    COALESCE((
                        SELECT rs.freshness_state FROM research_sessions rs
                        WHERE rs.question_id = q.id
                        ORDER BY rs.created_at DESC LIMIT 1
                    ), 'unknown') AS freshness
                FROM questions q
                WHERE q.id = ? AND {access_clause}
            """,
            "refresh": """
                SELECT rq.*, rq.id AS read_title, rq.reason AS read_text,
                    NULL AS review_state, NULL AS conflict_state,
                    CASE WHEN rq.status = 'failed' THEN 'needs_refresh'
                         WHEN rq.status IN ('pending', 'running') THEN 'stale'
                         ELSE 'fresh' END AS freshness,
                    owner.visibility, owner.namespace_kind, owner.namespace_id,
                    owner.public_index_state
                FROM refresh_queue rq
                JOIN (
                    SELECT id, visibility, namespace_kind, namespace_id,
                           public_index_state FROM sources
                    UNION ALL
                    SELECT id, visibility, namespace_kind, namespace_id,
                           public_index_state FROM claims
                    UNION ALL
                    SELECT id, visibility, namespace_kind, namespace_id,
                           public_index_state FROM reports
                    UNION ALL
                    SELECT e.id, s.visibility, s.namespace_kind, s.namespace_id,
                           s.public_index_state
                    FROM evidence_spans e
                    JOIN source_versions sv ON sv.id = e.source_version_id
                    JOIN sources s ON s.id = sv.source_id
                ) owner ON owner.id = rq.entity_id
                WHERE rq.id = ? AND """ + access_clause,
        }
        return sql[kind]

    @staticmethod
    def _candidate_from_row(kind: str, row: Any) -> RetrievalCandidate:
        return RetrievalCandidate(
            id=row["id"],
            kind=kind,
            title=row["title"] or "",
            summary=row["summary"] or "",
            search_text=row["search_text"] or "",
            review_state=row["review_state"],
            conflict_state=row["conflict_state"],
            freshness=row["freshness"],
            evidence_count=int(row["evidence_count"] or 0),
            updated_at=row["updated_at"],
            created_at=row["created_at"],
            url=row["url"],
            status=row["status"],
            repository=row["repository"],
            path=row["path"],
            topic_id=row["topic_id"],
            source_type=row["source_type"],
        )

    @staticmethod
    def _candidate_from_document(
        document: SearchDocument,
        *,
        exact_score: float = 0.0,
        lexical_score: float = 0.0,
        relationship_score: float = 0.0,
        matched_by: tuple[str, ...] = (),
    ) -> RetrievalCandidate:
        return RetrievalCandidate(
            id=document.id,
            kind=document.kind,
            title=document.title,
            summary=document.summary,
            search_text=document.search_text,
            review_state=document.review_state,
            conflict_state=document.conflict_state,
            freshness=document.freshness,
            evidence_count=document.evidence_count,
            updated_at=document.updated_at,
            created_at=document.created_at,
            url=document.url,
            status=document.status,
            repository=document.repository,
            path=document.path,
            topic_id=document.topic_id,
            source_type=document.source_type,
            trust_tier=document.trust_tier,
            exact_score=exact_score,
            lexical_score=lexical_score,
            relationship_score=relationship_score,
            matched_by=matched_by,
        )

    @classmethod
    def _record_from_row(cls, kind: str, row: Any) -> ReadRecord:
        data: dict[str, Any]
        if kind == "claim":
            data = {
                "question_id": row["question_id"],
                "current_revision_id": row["current_revision_id"],
                "canonical_key": row["canonical_key"],
                "status": row["revision_status"],
                "confidence": float(row["confidence"]),
                "scope": cls._json(row["scope_json"]),
            }
            url = None
            updated_at = row["updated_at"] or row["created_at"]
        elif kind == "report":
            data = {
                "question_id": row["question_id"],
                "report_kind": row["report_kind"],
                "guidance": cls._json(row["guidance_json"]),
            }
            url = None
            updated_at = row["created_at"]
        elif kind == "evidence":
            data = {
                "source_version_id": row["source_version_id"],
                "source_id": row["source_id"],
                "selector_type": row["selector_type"],
                "selector": cls._json(row["selector_json"]),
                "note": row["note"],
                "confidence": float(row["confidence"]),
                "anchor_state": row["anchor_state"],
                "trust_tier": row["trust_tier"],
            }
            url = row["source_url"]
            updated_at = row["created_at"]
        elif kind == "source_version":
            data = {
                "source_id": row["source_id"],
                "version_key": row["version_key"],
                "version_kind": row["version_kind"],
                "retrieved_at": row["retrieved_at"],
                "published_at": row["published_at"],
                "content_sha256": row["content_sha256"],
                "canonical_locator": row["canonical_locator"],
                "media_type": row["media_type"],
                "byte_count": row["byte_count"],
                "repository": row["repository_locator"],
                "commit_sha": row["commit_sha"],
                "blob_sha": row["blob_sha"],
                "path": row["path"],
            }
            url = row["canonical_locator"]
            updated_at = row["retrieved_at"]
        elif kind == "source":
            data = {
                "source_type": row["source_type"],
                "site_name": row["site_name"],
                "author": row["author"],
                "published_at": row["published_at"],
                "accessed_at": row["accessed_at"],
                "trust_tier": row["trust_tier"],
            }
            url = row["locator"]
            updated_at = row["last_verified_at"] or row["created_at"]
        elif kind == "question":
            data = {
                "topic_id": row["topic_id"],
                "status": row["status"],
                "focus": cls._json(row["focus_json"]),
            }
            url = None
            updated_at = row["created_at"]
        else:
            data = {
                "entity_kind": row["entity_kind"],
                "entity_id": row["entity_id"],
                "reason": row["reason"],
                "status": row["status"],
                "priority": float(row["priority"]),
                "detected_at": row["detected_at"],
                "resolved_at": row["resolved_at"],
            }
            url = None
            updated_at = row["detected_at"]
        return ReadRecord(
            id=row["id"],
            kind=kind,
            title=row["read_title"],
            text=row["read_text"],
            url=url,
            review_state=row["review_state"],
            conflict_state=(
                row["derived_conflict_state"]
                if kind == "claim"
                else row["conflict_state"]
            ),
            freshness=row["freshness"],
            updated_at=updated_at,
            data=data,
        )

    @classmethod
    def _revision(cls, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "claim_id": row["claim_id"],
            "revision_number": int(row["revision_number"]),
            "title": row["title"],
            "statement": row["statement"],
            "status": row["status"],
            "confidence": float(row["confidence"]),
            "valid_from": row["valid_from"],
            "valid_until": row["valid_until"],
            "supersedes_revision_id": row["supersedes_revision_id"],
            "created_by_model": row["created_by_model"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _json(value: str | None) -> Any:
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    @classmethod
    def _json_object(cls, value: str | None) -> dict[str, Any]:
        parsed = cls._json(value)
        return parsed if isinstance(parsed, dict) else {}
