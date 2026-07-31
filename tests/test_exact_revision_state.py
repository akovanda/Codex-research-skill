from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from research_registry.application.deposit import ResearchDepositService
from research_registry.application.migrate_v2 import run_v2_backfill
from research_registry.application.review import ResearchReviewService
from research_registry.application.search import ResearchSearchService
from research_registry.contracts.v2 import ResearchSearchRequest
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.models import ReviewRequest
from research_registry.persistence.read_adapter import (
    CurrentRetrievalAdapter,
    ReadAccess,
)
from research_registry.retrieval.projection import SearchIndexService
from research_registry.service import RegistryService
from research_registry.web_v2 import V2WebViewService
from tests.test_v2_deposit import _bundle


def _native_bundle(*, key: str, marker: str) -> dict:
    request = deepcopy(_bundle(key=key, title=f"Exact state A {marker}"))
    request["inquiry"]["prompt"] += f" {marker}"
    request["inquiry"]["topic_label"] += f" {marker}"
    request["sources"][0]["identity"]["locator"] += f"-{marker}"
    request["sources"][0]["identity"]["canonical_key"] += f"-{marker}"
    request["sources"][0]["version"]["version_key"] += f"-{marker}"
    request["sources"][0]["version"]["canonical_locator"] += f"-{marker}"
    request["claims"][0]["canonical_key"] += f"-{marker}"
    content = request["sources"][0]["version"]["snapshot"]["text"] + marker
    encoded = content.encode()
    request["sources"][0]["version"]["snapshot"]["text"] = content
    request["sources"][0]["version"]["snapshot"]["byte_count"] = len(encoded)
    request["sources"][0]["version"]["content_sha256"] = sha256(
        encoded
    ).hexdigest()
    return request


def _revision_bundle(
    original: dict,
    *,
    key: str,
    claim_id: str,
    expected_revision_id: str,
    label: str,
    relationship: str,
) -> dict:
    request = deepcopy(original)
    request["idempotency_key"] = key
    claim = request["claims"][0]
    claim["claim_id"] = claim_id
    claim["expected_revision_id"] = expected_revision_id
    claim["title"] = label
    if relationship == "refutes":
        refuting = deepcopy(request["evidence"][0])
        refuting["client_ref"] = "refuting"
        request["evidence"].append(refuting)
        claim["evidence"].append(
            {
                "evidence": {"ref": "refuting"},
                "relationship": "refutes",
            }
        )
    else:
        claim["evidence"][0]["relationship"] = relationship
    return request


def exercise_exact_revision_and_conflict_state(
    service: RegistryService,
    tmp_path: Path,
    *,
    suffix: str,
) -> None:
    service.initialize()
    marker = f"exact{suffix.replace('-', '')}"
    deposits = ResearchDepositService(
        service.database,
        FilesystemBlobStore(tmp_path / f"{marker}-blobs"),
    )
    request_a = _native_bundle(key=f"{marker}-a", marker=marker)
    receipt_a = deposits.deposit(request_a)
    claim_id = receipt_a.records.claim_ids["claim"]
    revision_a = receipt_a.records.claim_revision_ids["claim"]
    evidence_a = receipt_a.records.evidence_ids["evidence"]
    source_version_id = receipt_a.records.source_version_ids["source"]
    source_id = receipt_a.records.source_ids["source"]
    with service.connect() as conn:
        conn.execute(
            """
            UPDATE claims
            SET review_state = 'reviewed', conflict_state = 'conflicted',
                human_reviewed = 1
            WHERE id = ?
            """,
            (claim_id,),
        )
        conn.execute(
            """
            DELETE FROM legacy_projection_identity
            WHERE legacy_kind = 'claim' AND legacy_id = ?
            """,
            (claim_id,),
        )
    run_v2_backfill(service.database_url, resume=True)
    with service.connect() as conn:
        repaired_claim = conn.execute(
            """
            SELECT review_state, conflict_state, human_reviewed
            FROM claims WHERE id = ?
            """,
            (claim_id,),
        ).fetchone()
        invented_migration_events = conn.execute(
            """
            SELECT COUNT(*) AS count FROM review_events
            WHERE entity_kind = 'claim_revision' AND entity_id = ?
              AND actor_type = 'migration'
            """,
            (revision_a,),
        ).fetchone()["count"]
    assert (
        repaired_claim["review_state"],
        repaired_claim["conflict_state"],
        repaired_claim["human_reviewed"],
    ) == ("unreviewed", "none", 0)
    assert invented_migration_events == 0
    reviews = ResearchReviewService(service.database)
    reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"{marker}-approve-claim-a",
            "entity": {"kind": "claim_revision", "id": revision_a},
            "action": "approve",
            "expected_revision_id": revision_a,
            "expected_state": "unreviewed",
        }
    )

    request_b = _revision_bundle(
        request_a,
        key=f"{marker}-b",
        claim_id=claim_id,
        expected_revision_id=revision_a,
        label=f"Exact state B {marker}",
        relationship="refutes",
    )
    receipt_b = deposits.deposit(request_b)
    revision_b = receipt_b.records.claim_revision_ids["claim"]
    evidence_b = receipt_b.records.evidence_ids["refuting"]
    with service.connect() as conn:
        mirror_b = conn.execute(
            """
            SELECT review_state, conflict_state, human_reviewed
            FROM claims WHERE id = ?
            """,
            (claim_id,),
        ).fetchone()
    assert (
        mirror_b["review_state"],
        mirror_b["conflict_state"],
        mirror_b["human_reviewed"],
    ) == ("unreviewed", "conflicted", 0)

    request_c = _revision_bundle(
        request_a,
        key=f"{marker}-c",
        claim_id=claim_id,
        expected_revision_id=revision_b,
        label=f"Exact state C {marker}",
        relationship="supports",
    )
    receipt_c = deposits.deposit(request_c)
    revision_c = receipt_c.records.claim_revision_ids["claim"]
    with service.connect() as conn:
        mirror_c = conn.execute(
            """
            SELECT review_state, conflict_state, human_reviewed
            FROM claims WHERE id = ?
            """,
            (claim_id,),
        ).fetchone()
    assert (
        mirror_c["review_state"],
        mirror_c["conflict_state"],
        mirror_c["human_reviewed"],
    ) == ("unreviewed", "none", 0)

    retained = ReviewRequest(kind="claim", record_id=claim_id)
    service.review(retained)
    service.review(retained)
    retrieval = CurrentRetrievalAdapter(service.database)
    access = ReadAccess(include_private=True, local_trusted=True)
    revisions = {
        item["id"]: item for item in retrieval.list_revisions(claim_id)
    }
    assert {
        revision_id: (
            revisions[revision_id]["review_state"],
            revisions[revision_id]["conflict_state"],
        )
        for revision_id in (revision_a, revision_b, revision_c)
    } == {
        revision_a: ("reviewed", "none"),
        revision_b: ("unreviewed", "conflicted"),
        revision_c: ("reviewed", "none"),
    }
    claim_record = retrieval.get_record(claim_id, access=access)
    assert claim_record is not None
    assert (claim_record.review_state, claim_record.conflict_state) == (
        "reviewed",
        "none",
    )
    claim_candidate = next(
        item
        for item in retrieval.list_candidates(
            kinds=["claim"], access=access
        )
        if item.id == claim_id
    )
    assert (
        claim_candidate.review_state,
        claim_candidate.conflict_state,
    ) == ("reviewed", "none")

    reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"{marker}-contest-evidence-a",
            "entity": {"kind": "evidence", "id": evidence_a},
            "action": "contest",
            "expected_state": "unreviewed",
        }
    )
    records = {
        record_id: retrieval.get_record(record_id, access=access)
        for record_id in (evidence_a, evidence_b, source_version_id)
    }
    assert records[evidence_a] is not None
    assert records[evidence_b] is not None
    assert records[source_version_id] is not None
    assert records[evidence_a].conflict_state == "conflicted"  # type: ignore[union-attr]
    assert records[evidence_b].conflict_state == "none"  # type: ignore[union-attr]
    assert records[source_version_id].conflict_state == "none"  # type: ignore[union-attr]
    inbox = V2WebViewService(  # type: ignore[arg-type]
        service.database, None
    ).review_inbox(access=access)
    assert evidence_a in {item.id for item in inbox.items}

    reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"{marker}-approve-evidence-a",
            "entity": {"kind": "evidence", "id": evidence_a},
            "action": "approve",
            "expected_state": "flagged",
        }
    )
    reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"{marker}-contest-source",
            "entity": {"kind": "source_version", "id": source_version_id},
            "action": "contest",
            "expected_state": "unreviewed",
        }
    )
    source_record = retrieval.get_record(source_version_id, access=access)
    evidence_record = retrieval.get_record(evidence_a, access=access)
    assert source_record is not None and evidence_record is not None
    assert source_record.conflict_state == "conflicted"
    assert evidence_record.conflict_state == "none"

    search = ResearchSearchService(retrieval).search(
        ResearchSearchRequest(
            protocol="research-search/v2",
            query=marker,
            kinds=["source_version", "evidence", "claim"],
            include_private=True,
            limit=100,
        ),
        access=access,
    )
    hits = {hit.id: hit for hit in search.hits}
    for record_id in (claim_id, evidence_a, evidence_b, source_version_id):
        assert record_id in hits
    assert hits[source_version_id].conflict_state == "conflicted"
    assert hits[evidence_a].conflict_state == "none"
    detail = V2WebViewService(  # type: ignore[arg-type]
        service.database, None
    ).claim_detail(claim_id, access=access, can_review=True)
    history = {item.id: item for item in detail.revisions}
    assert history[revision_b].conflict_state == "conflicted"
    assert history[revision_c].review_state == "reviewed"

    reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"{marker}-approve-source",
            "entity": {"kind": "source_version", "id": source_version_id},
            "action": "approve",
            "expected_state": "flagged",
        }
    )
    with service.connect() as conn:
        source_mirror = conn.execute(
            """
            SELECT review_state, conflict_state
            FROM sources WHERE id = ?
            """,
            (source_id,),
        ).fetchone()
        retained_events = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM review_events
            WHERE entity_kind = 'claim_revision' AND entity_id = ?
              AND action = 'approve'
            """,
            (revision_c,),
        ).fetchone()["count"]
    assert (
        source_mirror["review_state"],
        source_mirror["conflict_state"],
    ) == ("reviewed", "none")
    assert retained_events == 1
    with service.connect() as conn:
        before_documents = [
            (row["id"], row["review_state"], row["conflict_state"])
            for row in conn.execute(
                """
                SELECT id, review_state, conflict_state
                FROM search_documents
                WHERE id IN (?, ?, ?, ?)
                ORDER BY id
                """,
                (claim_id, evidence_a, evidence_b, source_version_id),
            ).fetchall()
        ]
        before_event_count = conn.execute(
            "SELECT COUNT(*) AS count FROM review_events"
        ).fetchone()["count"]
    SearchIndexService(service.database).rebuild(verify=True)
    run_v2_backfill(service.database_url, resume=True)
    with service.connect() as conn:
        after_documents = [
            (row["id"], row["review_state"], row["conflict_state"])
            for row in conn.execute(
                """
                SELECT id, review_state, conflict_state
                FROM search_documents
                WHERE id IN (?, ?, ?, ?)
                ORDER BY id
                """,
                (claim_id, evidence_a, evidence_b, source_version_id),
            ).fetchall()
        ]
        after_event_count = conn.execute(
            "SELECT COUNT(*) AS count FROM review_events"
        ).fetchone()["count"]
    assert after_documents == before_documents
    assert after_event_count == before_event_count


def test_sqlite_exact_revision_and_conflict_state(tmp_path: Path) -> None:
    exercise_exact_revision_and_conflict_state(
        RegistryService(tmp_path / "exact-state.sqlite3"),
        tmp_path,
        suffix=uuid4().hex[:10],
    )
