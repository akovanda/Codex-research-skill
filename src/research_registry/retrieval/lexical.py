from __future__ import annotations

import re
from typing import Any, Iterable

from ..db import DatabaseTarget, DbConnection, connect_database
from .models import LexicalMatch, SearchDocument
from .projection import normalize_doi


_TOKEN = re.compile(r"\w+", re.UNICODE)
_MAX_QUERY_TERMS = 32


class LexicalSearchAdapter:
    """Dialect-owned FTS syntax behind one normalized candidate contract."""

    def __init__(self, database: DatabaseTarget):
        self.database = database

    def search(
        self,
        query: str,
        *,
        access: Any,
        limit: int,
    ) -> list[LexicalMatch]:
        normalized_query = _normalize(query)
        if not normalized_query:
            return []
        with connect_database(self.database) as conn:
            exact_rows = self._exact_rows(
                conn,
                normalized_query,
                access=access,
                limit=limit,
            )
            fts_rows = self._fts_rows(
                conn,
                normalized_query,
                access=access,
                limit=limit,
            )
        matches: dict[str, LexicalMatch] = {}
        for row in exact_rows:
            document = SearchDocument.from_row(row)
            exact, reasons = _exact_score(document, normalized_query)
            if exact:
                matches[document.id] = LexicalMatch(
                    document=document,
                    exact=exact,
                    matched_by=reasons,
                )
        for row in fts_rows:
            document = SearchDocument.from_row(row)
            lexical, reason = _lexical_score(document, normalized_query)
            if lexical <= 0:
                continue
            prior = matches.get(document.id)
            prior_reasons = prior.matched_by if prior else ()
            matches[document.id] = LexicalMatch(
                document=document,
                exact=prior.exact if prior else 0.0,
                lexical=max(lexical, prior.lexical if prior else 0.0),
                matched_by=tuple(
                    dict.fromkeys((*prior_reasons, reason))
                ),
            )
        return sorted(
            matches.values(),
            key=lambda match: (
                match.exact,
                match.lexical,
                match.document.updated_at or "",
                match.document.id,
            ),
            reverse=True,
        )[:limit]

    def fetch(
        self,
        document_ids: Iterable[str],
        *,
        access: Any,
    ) -> list[SearchDocument]:
        ids = list(dict.fromkeys(document_ids))
        if not ids:
            return []
        clause, parameters = _access_clause("d", access)
        with connect_database(self.database) as conn:
            rows = conn.execute(
                "SELECT d.* FROM search_documents d WHERE d.id IN ("
                + ",".join("?" for _ in ids)
                + f") AND {clause}",
                (*ids, *parameters),
            ).fetchall()
        return [SearchDocument.from_row(row) for row in rows]

    def _exact_rows(
        self,
        conn: DbConnection,
        query: str,
        *,
        access: Any,
        limit: int,
    ) -> list[Any]:
        doi = normalize_doi(query)
        locator_variants = {query}
        if doi:
            locator_variants.update(
                {
                    doi,
                    f"doi:{doi}",
                    f"https://doi.org/{doi}",
                    f"http://doi.org/{doi}",
                }
            )
        locators = sorted(locator_variants)
        clause, access_parameters = _access_clause("d", access)
        conditions = [
            "lower(d.id) = ?",
            "lower(d.path) = ?",
            "lower(d.canonical_key) = ?",
            "lower(d.dedupe_key) = ?",
            "lower(d.topic_slug) = ?",
            "lower(d.repository) = ?",
            "lower(d.title) = ?",
            "lower(d.locator) IN ("
            + ",".join("?" for _ in locators)
            + ")",
        ]
        parameters: list[Any] = [query] * 7 + locators
        if doi:
            conditions.append("lower(d.doi) = ?")
            parameters.append(doi)
        rows = conn.execute(
            "SELECT d.* FROM search_documents d WHERE ("
            + " OR ".join(conditions)
            + f") AND {clause} ORDER BY d.id ASC LIMIT ?",
            (*parameters, *access_parameters, limit),
        ).fetchall()
        return rows

    def _fts_rows(
        self,
        conn: DbConnection,
        query: str,
        *,
        access: Any,
        limit: int,
    ) -> list[Any]:
        raise NotImplementedError


class SQLiteLexicalSearchAdapter(LexicalSearchAdapter):
    def _fts_rows(
        self,
        conn: DbConnection,
        query: str,
        *,
        access: Any,
        limit: int,
    ) -> list[Any]:
        terms = _query_terms(query)
        if not terms:
            return []
        fts_query = " OR ".join(
            '"' + term.replace('"', '""') + '"' for term in terms
        )
        clause, parameters = _access_clause("d", access)
        return conn.execute(
            """
            SELECT d.*
            FROM search_documents_fts
            JOIN search_documents d
              ON d.rowid = search_documents_fts.rowid
            WHERE search_documents_fts MATCH ?
              AND """
            + clause
            + """
            ORDER BY bm25(
                search_documents_fts,
                5.0, 3.0, 1.0, 4.0, 2.0, 3.0, 3.0, 2.0, 1.0
            ) ASC, d.id ASC
            LIMIT ?
            """,
            (fts_query, *parameters, limit),
        ).fetchall()


class PostgresLexicalSearchAdapter(LexicalSearchAdapter):
    def _fts_rows(
        self,
        conn: DbConnection,
        query: str,
        *,
        access: Any,
        limit: int,
    ) -> list[Any]:
        terms = _query_terms(query)
        if not terms:
            return []
        web_query = " OR ".join(f'"{term}"' for term in terms)
        clause, parameters = _access_clause("d", access)
        return conn.execute(
            """
            SELECT d.*
            FROM search_documents d
            WHERE d.search_vector @@ websearch_to_tsquery('simple', ?)
              AND """
            + clause
            + """
            ORDER BY ts_rank_cd(
                d.search_vector, websearch_to_tsquery('simple', ?)
            ) DESC, d.id ASC
            LIMIT ?
            """,
            (web_query, *parameters, web_query, limit),
        ).fetchall()


def create_lexical_adapter(database: DatabaseTarget) -> LexicalSearchAdapter:
    if database.kind == "sqlite":
        return SQLiteLexicalSearchAdapter(database)
    return PostgresLexicalSearchAdapter(database)


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _query_terms(query: str) -> list[str]:
    return list(dict.fromkeys(_TOKEN.findall(query)))[:_MAX_QUERY_TERMS]


def _exact_score(
    document: SearchDocument,
    query: str,
) -> tuple[float, tuple[str, ...]]:
    doi = normalize_doi(query)
    comparisons = (
        (document.id, 1.0, "exact registry id"),
        (
            document.locator if document.kind == "source" else None,
            1.0,
            "exact source locator",
        ),
        (
            document.locator if document.kind == "source_version" else None,
            0.99,
            "exact source-version locator",
        ),
        (
            document.doi if document.kind == "source" else None,
            1.0,
            "exact DOI",
        ),
        (
            document.path if document.kind == "source_version" else None,
            1.0,
            "exact repository path",
        ),
        (document.canonical_key, 0.98, "exact canonical key"),
        (document.dedupe_key, 0.98, "exact dedupe key"),
        (document.topic_slug, 0.95, "exact topic slug"),
        (document.repository, 0.90, "exact repository scope"),
        (document.title, 0.95, "exact title phrase"),
    )
    score = 0.0
    reasons: list[str] = []
    for value, candidate_score, reason in comparisons:
        if value is None:
            continue
        normalized = _normalize(value)
        matched = normalized == query
        if reason in {"exact source locator", "exact DOI"} and doi:
            matched = matched or normalize_doi(value) == doi
        if matched:
            score = max(score, candidate_score)
            reasons.append(reason)
    return score, tuple(reasons)


def _lexical_score(
    document: SearchDocument,
    query: str,
) -> tuple[float, str]:
    terms = _query_terms(query)
    if not terms:
        return 0.0, ""
    haystack = _normalize(document.search_text)
    title = _normalize(document.title)
    matched = sum(term in haystack for term in terms)
    title_matched = sum(term in title for term in terms)
    coverage = matched / len(terms)
    title_coverage = title_matched / len(terms)
    phrase = 1.0 if query in haystack else 0.0
    score = min(1.0, 0.65 * coverage + 0.25 * title_coverage + 0.10 * phrase)
    return score, f"full-text: {matched}/{len(terms)} query terms"


def _access_clause(alias: str, access: Any) -> tuple[str, tuple[Any, ...]]:
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
