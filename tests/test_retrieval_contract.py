from __future__ import annotations

from hashlib import sha256
from datetime import datetime, timezone
import os
from pathlib import Path

import pytest

from research_registry.application.deposit import ResearchDepositService
from research_registry.application.search import ResearchSearchService
from research_registry.contracts.v2 import ResearchSearchRequest
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.persistence.read_adapter import (
    CurrentRetrievalAdapter,
    ReadAccess,
)
from research_registry.retrieval.evaluation import run_retrieval_evaluation
from research_registry.retrieval.projection import SearchIndexService
from research_registry.retrieval.projection import rebuild_search_documents
from research_registry.service import RegistryService


CORPUS = Path(__file__).parents[1] / "evals" / "retrieval" / "synthetic.json"
CONTENT = (
    "class EventPublisher:\n"
    "    def publish_with_retry(self):\n"
    "        for attempt in range(max_attempts):\n"
    "            publish(event)\n"
)
QUOTE = "for attempt in range(max_attempts):"


def _bundle(*, key: str = "retrieval-contract", rejected: bool = False) -> dict:
    content_bytes = CONTENT.encode("utf-8")
    return {
        "protocol": "research-deposit/v2",
        "idempotency_key": key,
        "inquiry": {
            "client_ref": "question",
            "prompt": "How does EventPublisher bound delivery retries?",
            "topic_label": "Publisher retry policy",
        },
        "run": {
            "client_ref": "run",
            "mode": "research",
            "provenance": {"actor_type": "agent"},
        },
        "sources": [
            {
                "client_ref": "source",
                "identity": {
                    "locator": "https://doi.org/10.5555/retrieval.2026",
                    "title": "EventPublisher retry implementation",
                    "source_type": "git_file",
                    "canonical_key": "artifact:src/events/publisher.py",
                },
                "version": {
                    "version_key": (
                        "git:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:"
                        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                    ),
                    "version_kind": "git_blob",
                    "retrieved_at": "2026-07-30T00:00:00Z",
                    "content_sha256": sha256(content_bytes).hexdigest(),
                    "canonical_locator": (
                        "git:artifact:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:"
                        "src/events/publisher.py"
                    ),
                    "snapshot": {
                        "policy": "extracted_text",
                        "text": CONTENT,
                        "media_type": "text/x-python",
                        "byte_count": len(content_bytes),
                    },
                    "repository": {
                        "repository_id": "artifact",
                        "commit_sha": "a" * 40,
                        "blob_sha": "b" * 40,
                        "path": "src/events/publisher.py",
                        "file_mode": "100644",
                    },
                },
            }
        ],
        "evidence": [
            {
                "client_ref": "evidence",
                "source_version": {"ref": "source"},
                "quote_text": QUOTE,
                "selector": {
                    "type": "git_line_range",
                    "path": "src/events/publisher.py",
                    "commit_sha": "a" * 40,
                    "blob_sha": "b" * 40,
                    "start_line": 3,
                    "end_line": 3,
                    "exact": QUOTE,
                },
                "note": "The retry loop has a fixed attempt budget.",
                "review_state": "reviewed",
                "trust_tier": "high",
            }
        ],
        "claims": [
            {
                "client_ref": "claim",
                "canonical_key": (
                    "artifact:event-publisher:rejected-unlimited-retries"
                    if rejected
                    else "artifact:event-publisher:bounded-retries"
                ),
                "title": (
                    "Unlimited retry storms are acceptable"
                    if rejected
                    else "Publisher retries use a bounded attempt budget"
                ),
                "statement": (
                    "This rejected claim must not rank by default."
                    if rejected
                    else "EventPublisher caps transient delivery retries."
                ),
                "status": "rejected" if rejected else "supported",
                "confidence": 0.95,
                "scope": {
                    "repository": "artifact",
                    "paths": ["src/events/publisher.py"],
                },
                "evidence": [
                    {
                        "evidence": {"ref": "evidence"},
                        "relationship": "supports",
                    }
                ],
            }
        ],
        "report": {
            "client_ref": "report",
            "title": "Operational delivery conclusion",
            "summary_md": "Use the implementation currently linked from this report.",
            "claims": [{"ref": "claim"}],
        },
    }


def _registry(tmp_path: Path) -> tuple[RegistryService, ResearchDepositService]:
    registry = RegistryService(tmp_path / "registry.sqlite3")
    registry.initialize()
    return (
        registry,
        ResearchDepositService(
            registry.database,
            FilesystemBlobStore(tmp_path / "blobs"),
        ),
    )


def _search(
    registry: RegistryService,
    query: str,
    *,
    kinds: list[str] | None = None,
    include_rejected: bool = False,
):
    service = ResearchSearchService(CurrentRetrievalAdapter(registry.database))
    return service.search(
        ResearchSearchRequest(
            protocol="research-search/v2",
            query=query,
            kinds=kinds or [],
            include_rejected=include_rejected,
            limit=10,
        ),
        access=ReadAccess(include_private=True, local_trusted=True),
    )


def test_source_freshness_uses_controlled_due_time_and_affects_ranking(
    tmp_path: Path,
) -> None:
    registry, _ = _registry(tmp_path)
    now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    rows = (
        ("src_due_before", "2026-07-30T11:59:59+00:00"),
        ("src_due_exact", "2026-07-30T12:00:00+00:00"),
        ("src_due_after", "2026-07-30T12:00:01+00:00"),
        ("src_due_unknown", None),
    )
    with registry.connect() as conn:
        for source_id, due_at in rows:
            conn.execute(
                """
                INSERT INTO sources (
                    id, locator, title, source_type, visibility,
                    refresh_due_at, created_at
                ) VALUES (?, ?, 'Freshness ranking marker', 'note',
                          'private', ?, '2026-07-30T00:00:00+00:00')
                """,
                (source_id, f"note:{source_id}", due_at),
            )
        rebuild_search_documents(conn, now=now)
        projected = conn.execute(
            "SELECT id, freshness FROM search_documents "
            "WHERE kind = 'source' ORDER BY id"
        ).fetchall()

    assert {row["id"]: row["freshness"] for row in projected} == {
        "src_due_after": "fresh",
        "src_due_before": "needs_refresh",
        "src_due_exact": "needs_refresh",
        "src_due_unknown": "unknown",
    }
    direct = CurrentRetrievalAdapter(
        registry.database,
        clock=lambda: now,
    )
    due_record = direct.get_record(
        "src_due_exact",
        access=ReadAccess(include_private=True, local_trusted=True),
    )
    future_record = direct.get_record(
        "src_due_after",
        access=ReadAccess(include_private=True, local_trusted=True),
    )
    assert due_record is not None and due_record.freshness == "needs_refresh"
    assert future_record is not None and future_record.freshness == "fresh"
    response = _search(
        registry,
        "Freshness ranking marker",
        kinds=["source"],
    )
    scores = {item.id: item.score for item in response.hits}
    assert scores["src_due_after"] > scores["src_due_before"]


def test_sqlite_projection_fts_exact_lookup_and_explained_ranking(
    tmp_path: Path,
) -> None:
    registry, deposits = _registry(tmp_path)
    receipt = deposits.deposit(_bundle())
    ids = receipt.records

    with registry.connect() as conn:
        tables = registry._list_tables(conn)
        projected = conn.execute(
            "SELECT COUNT(*) AS count FROM search_documents"
        ).fetchone()
    assert "search_documents" in tables
    assert "search_documents_fts" in tables
    assert int(projected["count"]) == 6

    exact_cases = {
        ids.claim_ids["claim"]: ids.claim_ids["claim"],
        "https://doi.org/10.5555/retrieval.2026": ids.source_ids["source"],
        "10.5555/retrieval.2026": ids.source_ids["source"],
        "src/events/publisher.py": ids.source_version_ids["source"],
    }
    for query, expected_id in exact_cases.items():
        response = _search(registry, query)
        assert response.hits[0].id == expected_id
        assert response.hits[0].score_components["exact"] == 1.0
        assert response.hits[0].matched_by

    lexical = _search(registry, "attempt budget publisher", kinds=["claim"])
    assert lexical.hits[0].id == ids.claim_ids["claim"]
    assert 0.0 < lexical.hits[0].score_components["lexical"] <= 1.0
    assert any("full-text" in reason for reason in lexical.hits[0].matched_by)
    assert all(
        -1.0 <= value <= 1.0
        for value in lexical.hits[0].score_components.values()
    )


def test_relationship_expansion_requires_a_lexical_anchor(
    tmp_path: Path,
) -> None:
    registry, deposits = _registry(tmp_path)
    receipt = deposits.deposit(_bundle())

    related = _search(
        registry,
        "bounded attempt budget",
        kinds=["report"],
    )
    assert related.hits[0].id == receipt.records.report_id
    assert related.hits[0].score_components["relationship"] > 0
    assert related.hits[0].score_components["lexical"] == 0
    assert any("relationship" in reason for reason in related.hits[0].matched_by)

    assert _search(registry, "term-with-no-lexical-anchor").hits == []
    assert _search(registry, "   ").hits == []


def test_rejected_default_state_penalties_and_rebuild_equivalence(
    tmp_path: Path,
) -> None:
    registry, deposits = _registry(tmp_path)
    accepted = deposits.deposit(_bundle())
    rejected = deposits.deposit(_bundle(key="rejected", rejected=True))

    default = _search(registry, "unlimited retry storms")
    assert rejected.records.claim_ids["claim"] not in {
        hit.id for hit in default.hits
    }
    included = _search(
        registry,
        "unlimited retry storms",
        include_rejected=True,
    )
    rejected_hit = next(
        hit
        for hit in included.hits
        if hit.id == rejected.records.claim_ids["claim"]
    )
    assert rejected_hit.score_components["review"] < 0

    with registry.connect() as conn:
        conn.execute(
            "UPDATE claims SET conflict_state = 'conflicted' WHERE id = ?",
            (accepted.records.claim_ids["claim"],),
        )
        conn.execute(
            "UPDATE research_sessions SET freshness_state = 'stale' WHERE id = ?",
            (accepted.records.run_id,),
        )
    SearchIndexService(registry.database).rebuild(verify=True)

    before = _search(registry, "bounded attempt budget", kinds=["claim"])
    hit = before.hits[0]
    assert hit.conflict_state == "conflicted"
    assert hit.freshness == "stale"
    assert hit.score_components["conflict"] < 0
    assert hit.score_components["freshness"] < 0

    with registry.connect() as conn:
        expected_document_count = int(
            conn.execute(
                "SELECT COUNT(*) AS count FROM search_documents"
            ).fetchone()["count"]
        )
        conn.execute("DELETE FROM search_documents")
    rebuilt = SearchIndexService(registry.database).rebuild(verify=True)
    after = _search(registry, "bounded attempt budget", kinds=["claim"])

    assert rebuilt.document_count == expected_document_count
    assert [
        (item.id, item.score, item.score_components, item.matched_by)
        for item in after.hits
    ] == [
        (item.id, item.score, item.score_components, item.matched_by)
        for item in before.hits
    ]


def test_synthetic_retrieval_evaluation_meets_phase_three_targets() -> None:
    result = run_retrieval_evaluation(CORPUS)

    assert result.recall_at_5 >= 0.70
    assert result.exact_recall_at_1 == 1.0
    assert result.sqlite_postgres_overlap is None
    assert result.case_count >= 10


def test_retrieval_evaluation_rejects_oversized_corpus(tmp_path: Path) -> None:
    corpus = tmp_path / "oversized.json"
    corpus.write_bytes(b" " * (5 * 1024 * 1024 + 1))

    with pytest.raises(ValueError, match="5 MiB limit"):
        run_retrieval_evaluation(corpus)


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres retrieval parity requires TEST_DATABASE_URL",
)
def test_postgres_sqlite_top_five_overlap() -> None:
    result = run_retrieval_evaluation(
        CORPUS,
        postgres_url=os.environ["TEST_DATABASE_URL"],
    )

    assert result.sqlite_postgres_overlap is not None
    assert result.sqlite_postgres_overlap >= 0.90
