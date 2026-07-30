from __future__ import annotations

from dataclasses import dataclass
import json
from math import log2
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from ..application.search import ResearchSearchService
from ..contracts.v2 import ResearchSearchRequest
from ..persistence.read_adapter import CurrentRetrievalAdapter, ReadAccess


_MAX_CORPUS_BYTES = 5 * 1024 * 1024
_MAX_CASES = 5_000
_TOP_LEVEL_FIELDS = {"protocol", "cases"}
_CASE_FIELDS = {
    "id",
    "query",
    "scope",
    "expected_record_ids",
    "relevant_evidence_ids",
    "expected_state",
    "kinds",
    "include_rejected",
    "category",
    "notes",
}
_STATE_FIELDS = {"review", "conflict", "freshness"}


@dataclass(frozen=True)
class KnownAnswerCaseResult:
    case_id: str
    expected_record_ids: tuple[str, ...]
    top_record_ids: tuple[str, ...]
    first_relevant_rank: int | None
    evidence_resolved: bool
    state_accurate: bool | None
    latency_ms: float
    response_byte_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "expected_record_ids": list(self.expected_record_ids),
            "top_record_ids": list(self.top_record_ids),
            "first_relevant_rank": self.first_relevant_rank,
            "evidence_resolved": self.evidence_resolved,
            "state_accurate": self.state_accurate,
            "latency_ms": self.latency_ms,
            "response_byte_count": self.response_byte_count,
            "search_calls": 1,
        }


@dataclass(frozen=True)
class KnownAnswerEvaluationResult:
    protocol: str
    case_count: int
    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    mean_reciprocal_rank: float
    ndcg_at_10: float
    precision_at_5: float
    evidence_resolvability: float
    state_accuracy: float
    p50_latency_ms: float
    p95_latency_ms: float
    response_byte_count: int
    search_call_count: int
    cases: tuple[KnownAnswerCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "case_count": self.case_count,
            "recall_at_1": self.recall_at_1,
            "recall_at_5": self.recall_at_5,
            "recall_at_10": self.recall_at_10,
            "mean_reciprocal_rank": self.mean_reciprocal_rank,
            "ndcg_at_10": self.ndcg_at_10,
            "precision_at_5": self.precision_at_5,
            "evidence_resolvability": self.evidence_resolvability,
            "state_accuracy": self.state_accuracy,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "response_byte_count": self.response_byte_count,
            "search_call_count": self.search_call_count,
            "cases": [case.to_dict() for case in self.cases],
        }


def run_known_answer_evaluation(
    corpus_path: str | Path,
    *,
    database: str | Path,
) -> KnownAnswerEvaluationResult:
    """Evaluate an operator-local corpus without copying it into output."""
    payload = _load_corpus(Path(corpus_path))
    retrieval = CurrentRetrievalAdapter(database)
    search = ResearchSearchService(retrieval)
    access = ReadAccess(include_private=True, local_trusted=True)
    results: list[KnownAnswerCaseResult] = []
    for case in payload["cases"]:
        started = perf_counter_ns()
        response = search.search(
            ResearchSearchRequest(
                protocol="research-search/v2",
                query=case["query"],
                kinds=case.get("kinds", []),
                scope=case.get("scope"),
                include_rejected=bool(case.get("include_rejected", False)),
                limit=10,
            ),
            access=access,
        )
        latency_ms = round((perf_counter_ns() - started) / 1_000_000, 6)
        top_ids = tuple(hit.id for hit in response.hits)
        expected = tuple(case["expected_record_ids"])
        first_rank = next(
            (
                rank
                for rank, record_id in enumerate(top_ids, start=1)
                if record_id in expected
            ),
            None,
        )
        first_hit = (
            response.hits[first_rank - 1]
            if first_rank is not None
            else None
        )
        evidence_ids = case.get("relevant_evidence_ids", [])
        evidence_resolved = all(
            retrieval.get_record(evidence_id, access=access) is not None
            for evidence_id in evidence_ids
        )
        expected_state = case.get("expected_state")
        state_accurate = (
            _state_matches(first_hit, expected_state)
            if expected_state is not None
            else None
        )
        results.append(
            KnownAnswerCaseResult(
                case_id=case["id"],
                expected_record_ids=expected,
                top_record_ids=top_ids,
                first_relevant_rank=first_rank,
                evidence_resolved=evidence_resolved,
                state_accurate=state_accurate,
                latency_ms=latency_ms,
                response_byte_count=len(
                    response.model_dump_json().encode("utf-8")
                ),
            )
        )
    cases = tuple(results)
    relevant = [case for case in cases if case.expected_record_ids]
    state_cases = [case for case in cases if case.state_accurate is not None]
    return KnownAnswerEvaluationResult(
        protocol="research-known-answer-evaluation/v1",
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
        ndcg_at_10=_ndcg(relevant, 10),
        precision_at_5=_precision(relevant, 5),
        evidence_resolvability=_rate(
            [case.evidence_resolved for case in cases]
        ),
        state_accuracy=_rate(
            [bool(case.state_accurate) for case in state_cases]
        ),
        p50_latency_ms=_percentile(
            [case.latency_ms for case in cases], 0.50
        ),
        p95_latency_ms=_percentile(
            [case.latency_ms for case in cases], 0.95
        ),
        response_byte_count=sum(
            case.response_byte_count for case in cases
        ),
        search_call_count=len(cases),
        cases=cases,
    )


def _load_corpus(path: Path) -> dict[str, Any]:
    if path.stat().st_size > _MAX_CORPUS_BYTES:
        raise ValueError("known-answer corpus exceeds the 5 MiB limit")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("known-answer corpus must be an object")
    _reject_unknown(payload, _TOP_LEVEL_FIELDS, "corpus")
    if payload.get("protocol") != "research-known-answer-corpus/v1":
        raise ValueError("unsupported known-answer corpus protocol")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("known-answer corpus requires cases")
    if len(cases) > _MAX_CASES:
        raise ValueError("known-answer corpus has too many cases")
    ids: list[str] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"known-answer case[{index}] must be an object")
        _reject_unknown(case, _CASE_FIELDS, f"case[{index}]")
        required = {"id", "query", "expected_record_ids"}
        missing = required - set(case)
        if missing:
            raise ValueError(
                f"known-answer case[{index}] is missing: "
                + ", ".join(sorted(missing))
            )
        if not isinstance(case["id"], str) or not case["id"]:
            raise ValueError(f"known-answer case[{index}] id is invalid")
        ids.append(case["id"])
        if not isinstance(case["query"], str) or not case["query"]:
            raise ValueError(f"known-answer case[{index}] query is invalid")
        for field in ("expected_record_ids", "relevant_evidence_ids"):
            values = case.get(field, [])
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value for value in values
            ):
                raise ValueError(
                    f"known-answer case[{index}] {field} must contain ids"
                )
            if len(values) != len(set(values)):
                raise ValueError(
                    f"known-answer case[{index}] {field} contains duplicates"
                )
        state = case.get("expected_state")
        if state is not None:
            if not isinstance(state, dict):
                raise ValueError(
                    f"known-answer case[{index}] expected_state is invalid"
                )
            _reject_unknown(
                state,
                _STATE_FIELDS,
                f"case[{index}].expected_state",
            )
    if len(ids) != len(set(ids)):
        raise ValueError("known-answer case ids must be unique")
    return payload


def _recall(cases: list[KnownAnswerCaseResult], k: int) -> float:
    if not cases:
        return 1.0
    return round(
        sum(
            bool(
                set(case.expected_record_ids)
                & set(case.top_record_ids[:k])
            )
            for case in cases
        )
        / len(cases),
        6,
    )


def _precision(cases: list[KnownAnswerCaseResult], k: int) -> float:
    if not cases:
        return 1.0
    return round(
        sum(
            len(
                set(case.expected_record_ids)
                & set(case.top_record_ids[:k])
            )
            / k
            for case in cases
        )
        / len(cases),
        6,
    )


def _ndcg(cases: list[KnownAnswerCaseResult], k: int) -> float:
    if not cases:
        return 1.0
    values = []
    for case in cases:
        expected = set(case.expected_record_ids)
        dcg = sum(
            1.0 / log2(rank + 1)
            for rank, record_id in enumerate(
                case.top_record_ids[:k], start=1
            )
            if record_id in expected
        )
        ideal = sum(
            1.0 / log2(rank + 1)
            for rank in range(1, min(k, len(expected)) + 1)
        )
        values.append(dcg / ideal if ideal else 1.0)
    return round(sum(values) / len(values), 6)


def _rate(values: list[bool]) -> float:
    return round(sum(values) / max(1, len(values)), 6)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * quantile)))
    return round(ordered[index], 6)


def _state_matches(hit: Any, expected: dict[str, str]) -> bool:
    if hit is None:
        return False
    actual = {
        "review": hit.review_state,
        "conflict": hit.conflict_state,
        "freshness": hit.freshness,
    }
    return all(actual[field] == value for field, value in expected.items())


def _reject_unknown(
    value: dict[str, Any],
    allowed: set[str],
    label: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            f"known-answer {label} has unknown field(s): "
            + ", ".join(unknown)
        )
