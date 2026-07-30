from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_registry.evaluation.comparative import run_comparative_evaluation
from research_registry.evaluation.known_answers import (
    run_known_answer_evaluation,
)
from research_registry.evaluation.registry_metrics import collect_registry_metrics
from research_registry.retrieval.evaluation import run_retrieval_evaluation
from research_registry.retrieval.models import SearchDocument
from research_registry.retrieval.projection import upsert_search_documents
from research_registry.service import RegistryService


ROOT = Path(__file__).parents[1]
RETRIEVAL_CORPUS = ROOT / "evals" / "retrieval" / "synthetic.json"
COMPARATIVE_CORPUS = ROOT / "evals" / "comparative" / "synthetic.json"


def test_checked_in_retrieval_corpus_reports_complete_rr2_metrics() -> None:
    result = run_retrieval_evaluation(RETRIEVAL_CORPUS)
    payload = result.to_dict()

    assert result.recall_at_1 >= 0.0
    assert result.recall_at_5 >= 0.80
    assert result.recall_at_10 >= result.recall_at_5
    assert result.mean_reciprocal_rank >= 0.0
    assert result.ndcg_at_10 >= 0.0
    assert result.precision_at_5 >= 0.0
    assert result.evidence_resolvability >= 0.95
    assert result.state_accuracy == 1.0
    assert result.exact_recall_at_1 == 1.0
    assert result.duplicate_result_rate == 0.0
    assert result.search_call_count == result.case_count
    assert result.useful_answer_within_two_calls == 1.0
    assert result.p50_latency_ms >= 0.0
    assert result.p95_latency_ms >= result.p50_latency_ms
    assert result.response_byte_count > 0
    assert "queries" not in payload
    assert all("query" not in case for case in payload["cases"])


def test_retrieval_corpus_rejects_unknown_fields_and_bad_references(
    tmp_path: Path,
) -> None:
    payload = json.loads(RETRIEVAL_CORPUS.read_text(encoding="utf-8"))
    payload["cases"][0]["unexpected"] = "must fail closed"
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown field"):
        run_retrieval_evaluation(path)

    payload = json.loads(RETRIEVAL_CORPUS.read_text(encoding="utf-8"))
    payload["cases"][0]["expected_document_keys"] = ["not-a-document"]
    path = tmp_path / "bad-reference.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown document"):
        run_retrieval_evaluation(path)


def test_comparative_harness_scores_recorded_modes_without_agent_loop() -> None:
    result = run_comparative_evaluation(COMPARATIVE_CORPUS)
    payload = result.to_dict()

    assert set(payload["modes"]) == {
        "memory_only",
        "registry_only",
        "both",
        "research_again",
    }
    assert payload["case_count"] >= 2
    assert payload["modes"]["registry_only"]["evidence_resolution_rate"] == 1.0
    assert payload["modes"]["both"]["correct_prior_finding_rate"] == 1.0
    assert payload["modes"]["both"]["average_tool_calls"] <= 2.0
    assert "query" not in json.dumps(payload)


def test_comparative_harness_requires_all_fixed_modes(tmp_path: Path) -> None:
    payload = json.loads(COMPARATIVE_CORPUS.read_text(encoding="utf-8"))
    del payload["cases"][0]["modes"]["research_again"]
    path = tmp_path / "missing-mode.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly"):
        run_comparative_evaluation(path)


def test_operator_local_known_answers_use_existing_records_without_leaking_query(
    tmp_path: Path,
) -> None:
    database = tmp_path / "private.sqlite3"
    registry = RegistryService(database)
    registry.initialize()
    with registry.connect() as conn:
        upsert_search_documents(
            conn,
            [
                SearchDocument(
                    id="clm_private_known_answer",
                    kind="claim",
                    title="Private known answer",
                    summary="A private operator-only conclusion.",
                    body="operator corpus private query sentinel",
                    review_state="reviewed",
                    conflict_state="none",
                    freshness="fresh",
                    evidence_count=1,
                    visibility="private",
                    namespace_kind="user",
                    namespace_id="local",
                )
            ],
        )
    corpus = tmp_path / "known-answers.json"
    corpus.write_text(
        json.dumps(
            {
                "protocol": "research-known-answer-corpus/v1",
                "cases": [
                    {
                        "id": "private-case",
                        "query": "operator corpus private query sentinel",
                        "expected_record_ids": ["clm_private_known_answer"],
                        "relevant_evidence_ids": [],
                        "expected_state": {
                            "review": "reviewed",
                            "conflict": "none",
                            "freshness": "fresh",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    before = database.read_bytes()
    result = run_known_answer_evaluation(corpus, database=database)
    rendered = json.dumps(result.to_dict(), sort_keys=True)

    assert result.recall_at_5 == 1.0
    assert result.state_accuracy == 1.0
    assert result.evidence_resolvability == 1.0
    assert "operator corpus private query sentinel" not in rendered
    assert database.read_bytes() == before


def test_local_metrics_are_content_free_and_cover_release_health(
    tmp_path: Path,
) -> None:
    database = tmp_path / "metrics.sqlite3"
    RegistryService(database).initialize()

    result = collect_registry_metrics(database, since="30d")
    rendered = json.dumps(result, sort_keys=True)

    assert result["protocol"] == "research-registry-metrics/v1"
    assert set(result) >= {"evidence", "deposit", "migration", "storage"}
    assert result["migration"]["unresolved_error_count"] == 0
    assert "query" not in rendered
    assert "quote_text" not in rendered
    assert "statement" not in rendered
