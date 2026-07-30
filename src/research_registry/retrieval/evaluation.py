from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4

from ..application.search import ResearchSearchService
from ..contracts.v2 import ResearchSearchRequest
from ..db import connect_database
from ..persistence.read_adapter import CurrentRetrievalAdapter, ReadAccess
from ..service import RegistryService
from .models import SearchDocument
from .projection import delete_search_documents, upsert_search_documents
from .ranking import RANKING_PROFILE_VERSION


_EXACT_CATEGORIES = {"exact_id", "exact_locator", "exact_path", "exact_doi"}
_MAX_CORPUS_BYTES = 5 * 1024 * 1024
_MAX_CORPUS_DOCUMENTS = 5_000
_MAX_CORPUS_CASES = 5_000
_ID_PREFIX = {
    "question": "q",
    "source": "src",
    "source_version": "srcv",
    "evidence": "evd",
    "claim": "clm",
    "report": "rpt",
}


@dataclass(frozen=True)
class RetrievalCaseResult:
    case_id: str
    category: str
    expected_document_keys: tuple[str, ...]
    top_document_keys: tuple[str, ...]
    first_relevant_rank: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "expected_document_keys": list(self.expected_document_keys),
            "top_document_keys": list(self.top_document_keys),
            "first_relevant_rank": self.first_relevant_rank,
        }


@dataclass(frozen=True)
class RetrievalEvaluationResult:
    protocol: str
    ranking_profile: str
    case_count: int
    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    mean_reciprocal_rank: float
    exact_recall_at_1: float
    sqlite_postgres_overlap: float | None
    postgres_status: str
    cases: tuple[RetrievalCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "ranking_profile": self.ranking_profile,
            "case_count": self.case_count,
            "recall_at_1": self.recall_at_1,
            "recall_at_5": self.recall_at_5,
            "recall_at_10": self.recall_at_10,
            "mean_reciprocal_rank": self.mean_reciprocal_rank,
            "exact_recall_at_1": self.exact_recall_at_1,
            "sqlite_postgres_overlap": self.sqlite_postgres_overlap,
            "postgres_status": self.postgres_status,
            "cases": [case.to_dict() for case in self.cases],
        }


def run_retrieval_evaluation(
    corpus_path: str | Path,
    *,
    postgres_url: str | None = None,
) -> RetrievalEvaluationResult:
    payload = _load_corpus(Path(corpus_path))
    sqlite_run = _evaluate_sqlite(payload)
    overlap = None
    postgres_status = "not_configured"
    if postgres_url is not None:
        postgres_run = _evaluate_database(
            payload,
            RegistryService(postgres_url),
            suffix=uuid4().hex[:12],
        )
        overlap = _top_five_overlap(sqlite_run, postgres_run)
        postgres_status = "evaluated"
    cases = tuple(sqlite_run)
    relevant = [case for case in cases if case.expected_document_keys]
    exact = [case for case in relevant if case.category in _EXACT_CATEGORIES]
    return RetrievalEvaluationResult(
        protocol="research-retrieval-evaluation/v1",
        ranking_profile=RANKING_PROFILE_VERSION,
        case_count=len(cases),
        recall_at_1=_recall(relevant, 1),
        recall_at_5=_recall(relevant, 5),
        recall_at_10=_recall(relevant, 10),
        mean_reciprocal_rank=round(
            sum(
                1.0 / case.first_relevant_rank
                for case in relevant
                if case.first_relevant_rank is not None
            )
            / max(1, len(relevant)),
            6,
        ),
        exact_recall_at_1=_recall(exact, 1),
        sqlite_postgres_overlap=overlap,
        postgres_status=postgres_status,
        cases=cases,
    )


def _load_corpus(path: Path) -> dict[str, Any]:
    if path.stat().st_size > _MAX_CORPUS_BYTES:
        raise ValueError("retrieval corpus exceeds the 5 MiB limit")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("retrieval corpus must be an object")
    if payload.get("protocol") != "research-retrieval-corpus/v1":
        raise ValueError("unsupported retrieval corpus protocol")
    if payload.get("ranking_profile") != RANKING_PROFILE_VERSION:
        raise ValueError("retrieval corpus ranking profile does not match")
    documents = payload.get("documents")
    cases = payload.get("cases")
    if not isinstance(documents, list) or not isinstance(cases, list):
        raise ValueError("retrieval corpus requires document and case lists")
    if len(documents) > _MAX_CORPUS_DOCUMENTS:
        raise ValueError("retrieval corpus has too many documents")
    if len(cases) > _MAX_CORPUS_CASES:
        raise ValueError("retrieval corpus has too many cases")
    keys = [document.get("key") for document in documents]
    if any(not isinstance(key, str) or not key for key in keys):
        raise ValueError("retrieval corpus document keys must be non-empty strings")
    if len(keys) != len(set(keys)):
        raise ValueError("retrieval corpus document keys must be unique")
    return payload


def _evaluate_sqlite(payload: dict[str, Any]) -> list[RetrievalCaseResult]:
    with tempfile.TemporaryDirectory(prefix="research-registry-retrieval-") as temp:
        service = RegistryService(Path(temp) / "evaluation.sqlite3")
        return _evaluate_database(payload, service, suffix="sqlite")


def _evaluate_database(
    payload: dict[str, Any],
    service: RegistryService,
    *,
    suffix: str,
) -> list[RetrievalCaseResult]:
    service.initialize()
    namespace_id = f"retrieval-eval-{suffix}"
    documents, ids_by_key = _documents(
        payload["documents"],
        namespace_id=namespace_id,
        suffix=suffix,
    )
    keys_by_id = {record_id: key for key, record_id in ids_by_key.items()}
    with connect_database(service.database) as conn:
        upsert_search_documents(conn, documents)
    try:
        retrieval = CurrentRetrievalAdapter(service.database)
        search = ResearchSearchService(retrieval)
        access = ReadAccess(
            include_private=True,
            namespace_kind="user",
            namespace_id=namespace_id,
        )
        results: list[RetrievalCaseResult] = []
        documents_by_key = {
            document["key"]: document for document in payload["documents"]
        }
        for case in payload["cases"]:
            query = _case_query(case["query"], documents_by_key, ids_by_key)
            response = search.search(
                ResearchSearchRequest(
                    protocol="research-search/v2",
                    query=query,
                    kinds=case.get("kinds", []),
                    include_rejected=bool(case.get("include_rejected", False)),
                    limit=10,
                ),
                access=access,
            )
            top_keys = tuple(
                keys_by_id.get(hit.id, f"external:{hit.id}")
                for hit in response.hits
            )
            expected = tuple(case.get("expected_document_keys", []))
            first_rank = next(
                (
                    index
                    for index, key in enumerate(top_keys, start=1)
                    if key in expected
                ),
                None,
            )
            results.append(
                RetrievalCaseResult(
                    case_id=case["id"],
                    category=case["category"],
                    expected_document_keys=expected,
                    top_document_keys=top_keys,
                    first_relevant_rank=first_rank,
                )
            )
        return results
    finally:
        with connect_database(service.database) as conn:
            delete_search_documents(
                conn,
                [document.id for document in documents],
            )


def _documents(
    raw_documents: list[dict[str, Any]],
    *,
    namespace_id: str,
    suffix: str,
) -> tuple[list[SearchDocument], dict[str, str]]:
    ids_by_key = {
        raw["key"]: f"{_ID_PREFIX[raw['kind']]}_eval_{suffix}_{raw['key']}"
        for raw in raw_documents
    }
    documents = [
        SearchDocument(
            id=ids_by_key[raw["key"]],
            kind=raw["kind"],
            title=raw["title"],
            summary=raw["summary"],
            body=raw.get("body", ""),
            locator=raw.get("locator"),
            doi=raw.get("doi"),
            repository=raw.get("repository"),
            path=raw.get("path"),
            canonical_key=raw.get("canonical_key"),
            topic_slug=raw.get("topic_slug"),
            quote_hash=raw.get("quote_hash"),
            dedupe_key=raw.get("dedupe_key"),
            review_state=raw.get("review_state"),
            trust_tier=raw.get("trust_tier"),
            conflict_state=raw.get("conflict_state"),
            freshness=raw.get("freshness"),
            status=raw.get("status"),
            evidence_count=int(raw.get("evidence_count", 0)),
            updated_at=raw.get("updated_at", "2026-07-30T00:00:00+00:00"),
            created_at=raw.get("created_at", "2026-07-30T00:00:00+00:00"),
            url=raw.get("url", raw.get("locator")),
            source_type=raw.get("source_type"),
            topic_id=raw.get("topic_id"),
            visibility="private",
            namespace_kind="user",
            namespace_id=namespace_id,
            public_index_state="private",
        )
        for raw in raw_documents
    ]
    return documents, ids_by_key


def _case_query(
    query: str | dict[str, str],
    documents_by_key: dict[str, dict[str, Any]],
    ids_by_key: dict[str, str],
) -> str:
    if isinstance(query, str):
        return query
    key = query["document"]
    field = query["field"]
    if field == "id":
        return ids_by_key[key]
    value = documents_by_key[key].get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"retrieval case query field is missing: {key}.{field}")
    return value


def _recall(cases: list[RetrievalCaseResult], k: int) -> float:
    if not cases:
        return 1.0
    hits = sum(
        bool(set(case.expected_document_keys) & set(case.top_document_keys[:k]))
        for case in cases
    )
    return round(hits / len(cases), 6)


def _top_five_overlap(
    left: list[RetrievalCaseResult],
    right: list[RetrievalCaseResult],
) -> float:
    right_by_id = {case.case_id: case for case in right}
    overlaps: list[float] = []
    for case in left:
        right_case = right_by_id[case.case_id]
        left_top = set(case.top_document_keys[:5])
        right_top = set(right_case.top_document_keys[:5])
        denominator = max(1, len(left_top), len(right_top))
        overlaps.append(len(left_top & right_top) / denominator)
    return round(sum(overlaps) / max(1, len(overlaps)), 6)
