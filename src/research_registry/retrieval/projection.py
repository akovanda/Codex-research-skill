from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable

from ..db import (
    DatabaseTarget,
    DbConnection,
    connect_database,
    resolve_database_target,
)
from ..persistence.conflict_state import (
    claim_revision_conflict_state_sql,
    effective_conflict_state_sql,
)
from ..persistence.review_state import effective_review_state_sql
from ..timestamps import freshness_case
from .models import SearchDocument


_DOI = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_DOCUMENT_COLUMNS = tuple(SearchDocument.__dataclass_fields__)


@dataclass(frozen=True)
class SearchIndexRebuildResult:
    database_kind: str
    document_count: int
    counts_by_kind: dict[str, int]
    projection_sha256: str
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "database_kind": self.database_kind,
            "document_count": self.document_count,
            "counts_by_kind": self.counts_by_kind,
            "projection_sha256": self.projection_sha256,
            "verified": self.verified,
        }


class SearchIndexService:
    """Rebuild and verify the canonical projection in one transaction."""

    def __init__(self, database: str | Path | DatabaseTarget):
        self.database = (
            database
            if isinstance(database, DatabaseTarget)
            else resolve_database_target(database)
        )

    def rebuild(
        self,
        *,
        verify: bool = True,
        now: datetime | None = None,
    ) -> SearchIndexRebuildResult:
        with connect_database(self.database) as conn:
            documents = rebuild_search_documents(conn, now=now)
            verified = _verify_index(conn) if verify else False
        return _rebuild_result(
            self.database.kind,
            documents,
            verified=verified,
        )


def normalize_doi(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    for prefix in ("doi:", "https://doi.org/", "http://doi.org/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    normalized = normalized.rstrip(".,;")
    return normalized if _DOI.fullmatch(normalized) else None


def rebuild_search_documents(
    conn: DbConnection,
    *,
    now: datetime | None = None,
) -> list[SearchDocument]:
    current = now or datetime.now(timezone.utc).replace(microsecond=0)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    now_text = current.astimezone(timezone.utc).isoformat()
    documents = list(_project_documents(conn, now_text=now_text))
    conn.execute("DELETE FROM search_documents")
    upsert_search_documents(conn, documents)
    return documents


def upsert_search_documents(
    conn: DbConnection,
    documents: Iterable[SearchDocument],
) -> None:
    columns = ", ".join(_DOCUMENT_COLUMNS)
    placeholders = ", ".join("?" for _ in _DOCUMENT_COLUMNS)
    assignments = ", ".join(
        f"{column} = excluded.{column}"
        for column in _DOCUMENT_COLUMNS
        if column != "id"
    )
    sql = (
        f"INSERT INTO search_documents ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {assignments}"
    )
    for document in documents:
        values = asdict(document)
        conn.execute(sql, tuple(values[column] for column in _DOCUMENT_COLUMNS))


def delete_search_documents(conn: DbConnection, document_ids: Iterable[str]) -> None:
    ids = list(dict.fromkeys(document_ids))
    if not ids:
        return
    conn.execute(
        "DELETE FROM search_documents WHERE id IN ("
        + ",".join("?" for _ in ids)
        + ")",
        tuple(ids),
    )


def _project_documents(
    conn: DbConnection,
    *,
    now_text: str,
) -> Iterable[SearchDocument]:
    for row in conn.execute(_QUESTION_SQL).fetchall():
        yield _document(row)
    source_sql = _SOURCE_SQL.replace(
        "__FRESHNESS_CASE__",
        freshness_case("s.refresh_due_at", dialect=conn.target.kind),
    )
    for row in conn.execute(source_sql, (now_text,)).fetchall():
        yield _document(row, doi=normalize_doi(row["locator"]))
    for row in conn.execute(_SOURCE_VERSION_SQL).fetchall():
        yield _document(row, doi=normalize_doi(row["locator"]))
    for row in conn.execute(_EVIDENCE_SQL).fetchall():
        yield _document(row, doi=normalize_doi(row["locator"]))
    for row in conn.execute(_CLAIM_SQL).fetchall():
        scope = _json_object(row["scope_json"])
        paths = scope.get("paths")
        path = (
            paths[0]
            if isinstance(paths, list) and paths and isinstance(paths[0], str)
            else scope.get("path")
            if isinstance(scope.get("path"), str)
            else None
        )
        repository = (
            scope.get("repository")
            if isinstance(scope.get("repository"), str)
            else None
        )
        yield _document(row, repository=repository, path=path)
    for row in conn.execute(_REPORT_SQL).fetchall():
        yield _document(row)


def _document(row: Any, **overrides: Any) -> SearchDocument:
    values = {column: row[column] for column in _DOCUMENT_COLUMNS}
    values.update(overrides)
    values["title"] = (values["title"] or "")[:500]
    values["summary"] = (values["summary"] or "")[:4_000]
    values["body"] = values["body"] or ""
    values["evidence_count"] = int(values["evidence_count"] or 0)
    return SearchDocument(**values)


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _verify_index(conn: DbConnection) -> bool:
    if conn.target.kind == "sqlite":
        conn.execute(
            "INSERT INTO search_documents_fts(search_documents_fts) "
            "VALUES ('integrity-check')"
        )
        return True
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM search_documents "
        "WHERE search_vector IS NULL"
    ).fetchone()
    return int(row["count"]) == 0


def _rebuild_result(
    database_kind: str,
    documents: list[SearchDocument],
    *,
    verified: bool,
) -> SearchIndexRebuildResult:
    counts: dict[str, int] = {}
    digest = sha256()
    for document in sorted(documents, key=lambda item: item.id):
        counts[document.kind] = counts.get(document.kind, 0) + 1
        digest.update(
            json.dumps(
                asdict(document),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return SearchIndexRebuildResult(
        database_kind=database_kind,
        document_count=len(documents),
        counts_by_kind=dict(sorted(counts.items())),
        projection_sha256=digest.hexdigest(),
        verified=verified,
    )


_QUESTION_SQL = """
SELECT
    q.id, 'question' AS kind, q.prompt AS title, q.prompt AS summary,
    (q.prompt || ' ' || q.normalized_prompt || ' ' ||
     COALESCE(t.label, '')) AS body,
    NULL AS locator, NULL AS doi, NULL AS repository, NULL AS path,
    NULL AS canonical_key, t.slug AS topic_slug, NULL AS quote_hash,
    q.dedupe_key,
    CASE WHEN q.human_reviewed = 1 THEN 'reviewed'
         ELSE 'unreviewed' END AS review_state,
    NULL AS trust_tier, NULL AS conflict_state,
    COALESCE((
        SELECT rs.freshness_state FROM research_sessions rs
        WHERE rs.question_id = q.id
        ORDER BY rs.created_at DESC LIMIT 1
    ), 'unknown') AS freshness,
    q.status,
    (SELECT COUNT(*) FROM evidence_spans e
     WHERE e.question_id = q.id) AS evidence_count,
    q.created_at AS updated_at, q.created_at,
    NULL AS url, NULL AS source_type, q.topic_id,
    q.visibility, q.namespace_kind, q.namespace_id, q.public_index_state
FROM questions q
LEFT JOIN topics t ON t.id = q.topic_id
"""

_SOURCE_SQL = """
SELECT
    s.id, 'source' AS kind, s.title,
    COALESCE(s.snippet, s.locator) AS summary,
    (s.title || ' ' || s.locator || ' ' || COALESCE(s.snippet, '')) AS body,
    s.locator, NULL AS doi, NULL AS repository, NULL AS path,
    NULL AS canonical_key, NULL AS topic_slug, NULL AS quote_hash,
    s.dedupe_key, s.review_state, s.trust_tier, s.conflict_state,
    __FRESHNESS_CASE__ AS freshness,
    NULL AS status,
    (SELECT COUNT(*) FROM evidence_spans e
     JOIN source_versions sv ON sv.id = e.source_version_id
     WHERE sv.source_id = s.id) AS evidence_count,
    COALESCE(s.last_verified_at, s.created_at) AS updated_at,
    s.created_at, s.locator AS url, s.source_type, NULL AS topic_id,
    s.visibility, s.namespace_kind, s.namespace_id, s.public_index_state
FROM sources s
"""

_SOURCE_VERSION_SQL = f"""
SELECT
    sv.id, 'source_version' AS kind, s.title,
    sv.canonical_locator AS summary,
    (s.title || ' ' || sv.canonical_locator || ' ' ||
     COALESCE(sv.repository_locator, '') || ' ' ||
     COALESCE(sv.path, '')) AS body,
    sv.canonical_locator AS locator, NULL AS doi,
    sv.repository_locator AS repository, sv.path,
    NULL AS canonical_key, NULL AS topic_slug, NULL AS quote_hash,
    NULL AS dedupe_key,
    {effective_review_state_sql(
        entity_kind="source_version",
        entity_id_sql="sv.id",
        fallback_sql="'unreviewed'",
    )} AS review_state,
    s.trust_tier,
    {effective_conflict_state_sql(
        entity_kind="source_version",
        entity_id_sql="sv.id",
    )} AS conflict_state,
    'unknown' AS freshness, NULL AS status,
    (SELECT COUNT(*) FROM evidence_spans e
     WHERE e.source_version_id = sv.id) AS evidence_count,
    sv.retrieved_at AS updated_at, sv.created_at,
    sv.canonical_locator AS url, s.source_type, NULL AS topic_id,
    s.visibility, s.namespace_kind, s.namespace_id, s.public_index_state
FROM source_versions sv
JOIN sources s ON s.id = sv.source_id
"""

_EVIDENCE_SQL = f"""
SELECT
    e.id, 'evidence' AS kind, s.title,
    e.quote_text AS summary,
    (e.quote_text || ' ' || COALESCE(e.note, '') || ' ' ||
     s.title || ' ' || s.locator) AS body,
    s.locator, NULL AS doi, sv.repository_locator AS repository, sv.path,
    NULL AS canonical_key, t.slug AS topic_slug,
    e.quote_sha256 AS quote_hash, NULL AS dedupe_key,
    {effective_review_state_sql(
        entity_kind="evidence",
        entity_id_sql="e.id",
        fallback_sql="e.review_state",
    )} AS review_state,
    e.trust_tier,
    {effective_conflict_state_sql(
        entity_kind="evidence",
        entity_id_sql="e.id",
    )} AS conflict_state,
    CASE WHEN e.anchor_state = 'stale' THEN 'stale'
         WHEN e.anchor_state IN ('resolved', 'relocated') THEN 'fresh'
         ELSE 'unknown' END AS freshness,
    e.anchor_state AS status, 1 AS evidence_count,
    e.created_at AS updated_at, e.created_at, s.locator AS url,
    s.source_type, e.topic_id, s.visibility, s.namespace_kind,
    s.namespace_id, s.public_index_state
FROM evidence_spans e
JOIN source_versions sv ON sv.id = e.source_version_id
JOIN sources s ON s.id = sv.source_id
LEFT JOIN topics t ON t.id = e.topic_id
"""

_CLAIM_SQL = f"""
SELECT
    c.id, 'claim' AS kind, COALESCE(cr.title, c.title) AS title,
    COALESCE(cr.statement, c.statement) AS summary,
    (COALESCE(cr.title, c.title) || ' ' ||
     COALESCE(cr.statement, c.statement) || ' ' ||
     COALESCE(c.focal_label, '') || ' ' ||
     COALESCE(c.canonical_key, '')) AS body,
    NULL AS locator, NULL AS doi, NULL AS repository, NULL AS path,
    c.canonical_key, t.slug AS topic_slug, NULL AS quote_hash,
    c.dedupe_key,
    {effective_review_state_sql(
        entity_kind="claim_revision",
        entity_id_sql="c.current_revision_id",
        fallback_sql="'unreviewed'",
    )} AS review_state,
    c.trust_tier,
    {claim_revision_conflict_state_sql(
        revision_id_sql="c.current_revision_id",
        status_sql="cr.status",
    )} AS conflict_state,
    CASE
        WHEN EXISTS (
            SELECT 1 FROM claim_evidence stale_ce
            JOIN evidence_spans stale_e
              ON stale_e.id = stale_ce.evidence_span_id
            WHERE stale_ce.claim_revision_id = c.current_revision_id
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
                          SELECT 1 FROM claim_evidence failed_ce
                          WHERE failed_ce.claim_revision_id = c.current_revision_id
                            AND failed_ce.evidence_span_id = failed_rq.entity_id
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
                          SELECT 1 FROM claim_evidence pending_ce
                          WHERE pending_ce.claim_revision_id = c.current_revision_id
                            AND pending_ce.evidence_span_id = pending_rq.entity_id
                      )
                  )
              )
        )
        THEN 'needs_refresh'
        ELSE COALESCE(rs.freshness_state, 'unknown')
    END AS freshness,
    COALESCE(cr.status, c.status) AS status,
    (SELECT COUNT(*) FROM claim_evidence ce
     WHERE ce.claim_revision_id = c.current_revision_id) AS evidence_count,
    COALESCE(c.updated_at, c.created_at) AS updated_at,
    c.created_at, NULL AS url, NULL AS source_type, c.topic_id,
    c.visibility, c.namespace_kind, c.namespace_id, c.public_index_state,
    c.scope_json
FROM claims c
LEFT JOIN claim_revisions cr ON cr.id = c.current_revision_id
LEFT JOIN research_sessions rs ON rs.id = c.session_id
LEFT JOIN topics t ON t.id = c.topic_id
"""

_REPORT_SQL = f"""
SELECT
    r.id, 'report' AS kind, r.title, r.summary_md AS summary,
    (r.title || ' ' || r.summary_md || ' ' ||
     COALESCE(r.guidance_json, '')) AS body,
    NULL AS locator, NULL AS doi, NULL AS repository, NULL AS path,
    NULL AS canonical_key, t.slug AS topic_slug, NULL AS quote_hash,
    r.dedupe_key,
    {effective_review_state_sql(
        entity_kind="report",
        entity_id_sql="r.id",
        fallback_sql="r.review_state",
    )} AS review_state,
    r.trust_tier, r.conflict_state,
    COALESCE(rs.freshness_state, 'unknown') AS freshness,
    NULL AS status,
    (SELECT COUNT(*) FROM report_claims rc
     JOIN claims c ON c.id = rc.claim_id
     JOIN claim_evidence ce
       ON ce.claim_revision_id = c.current_revision_id
     WHERE rc.report_id = r.id) AS evidence_count,
    r.created_at AS updated_at, r.created_at,
    NULL AS url, NULL AS source_type, q.topic_id,
    r.visibility, r.namespace_kind, r.namespace_id, r.public_index_state
FROM reports r
LEFT JOIN research_sessions rs ON rs.id = r.session_id
LEFT JOIN questions q ON q.id = r.question_id
LEFT JOIN topics t ON t.id = q.topic_id
"""
