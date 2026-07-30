from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import ValidationError

from research_registry.application.deposit import (
    DepositError,
    DepositReferenceNotFound,
    IdempotencyConflict,
    ResearchDepositService,
)
from research_registry.contracts.v2 import ResearchDepositRequest
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.service import RegistryService


CONTENT = "Atomic deposits preserve source-backed evidence."
QUOTE = "preserve source-backed evidence"


def _bundle(
    *,
    key: str = "deposit-one",
    validate_only: bool = False,
    claim_id: str | None = None,
    expected_revision_id: str | None = None,
    title: str = "Deposits are atomic",
    status: str = "supported",
    relationship: str = "supports",
) -> dict:
    content_bytes = CONTENT.encode("utf-8")
    return {
        "protocol": "research-deposit/v2",
        "idempotency_key": key,
        "validate_only": validate_only,
        "inquiry": {
            "client_ref": "question",
            "prompt": "How does the v2 deposit preserve atomicity?",
            "topic_label": "V2 atomic deposit",
            "focus": {"component": "deposit"},
        },
        "run": {
            "client_ref": "run",
            "mode": "research",
            "provenance": {
                "actor_type": "agent",
                "model": "test-model",
                "model_version": "1",
            },
        },
        "sources": [
            {
                "client_ref": "source",
                "identity": {
                    "locator": "note:atomic-deposit",
                    "title": "Atomic deposit note",
                    "source_type": "note",
                    "canonical_key": "atomic-deposit-note",
                },
                "version": {
                    "version_key": "note:atomic-deposit-v1",
                    "version_kind": "note",
                    "retrieved_at": "2026-07-30T00:00:00Z",
                    "content_sha256": sha256(content_bytes).hexdigest(),
                    "canonical_locator": "note:atomic-deposit",
                    "snapshot": {
                        "policy": "extracted_text",
                        "text": CONTENT,
                        "media_type": "text/plain",
                        "byte_count": len(content_bytes),
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
                    "type": "text_quote",
                    "exact": QUOTE,
                    "start": CONTENT.index(QUOTE),
                    "end": CONTENT.index(QUOTE) + len(QUOTE),
                },
            }
        ],
        "claims": [
            {
                "client_ref": "claim",
                "claim_id": claim_id,
                "expected_revision_id": expected_revision_id,
                "canonical_key": "atomic-deposit-claim",
                "title": title,
                "statement": "A v2 deposit is committed as one transaction.",
                "status": status,
                "confidence": 0.95,
                "evidence": [
                    {
                        "evidence": {"ref": "evidence"},
                        "relationship": relationship,
                    }
                ],
            }
        ],
        "report": {
            "client_ref": "report",
            "title": "Atomic deposit report",
            "summary_md": "The deposit is atomic.",
            "claims": [{"ref": "claim"}],
        },
    }


def _service(
    tmp_path: Path,
    *,
    fault_injector=None,
) -> tuple[RegistryService, FilesystemBlobStore, ResearchDepositService]:
    registry = RegistryService(tmp_path / "registry.sqlite3")
    registry.initialize()
    blobs = FilesystemBlobStore(tmp_path / "blobs")
    return (
        registry,
        blobs,
        ResearchDepositService(
            registry.database,
            blobs,
            fault_injector=fault_injector,
        ),
    )


def _counts(registry: RegistryService) -> dict[str, int]:
    tables = (
        "topics",
        "questions",
        "research_sessions",
        "sources",
        "source_versions",
        "content_objects",
        "excerpts",
        "evidence_spans",
        "claims",
        "claim_revisions",
        "claim_evidence",
        "reports",
        "idempotency_keys",
    )
    with registry.connect() as conn:
        return {
            table: int(
                conn.execute(
                    f"SELECT COUNT(*) AS count FROM {table}"
                ).fetchone()["count"]
            )
            for table in tables
        }


def test_complete_deposit_is_atomic_private_and_idempotent(
    tmp_path: Path,
) -> None:
    registry, blobs, deposits = _service(tmp_path)
    request = ResearchDepositRequest.model_validate(_bundle())

    first = deposits.deposit(request)
    replay = deposits.deposit(request)

    assert first.status == "committed"
    assert first.committed is True
    assert first.idempotent_replay is False
    assert replay.model_dump(exclude={"idempotent_replay"}) == first.model_dump(
        exclude={"idempotent_replay"}
    )
    assert replay.idempotent_replay is True
    assert first.records.question_id
    assert first.records.run_id
    assert first.records.report_id
    assert _counts(registry) == {
        "topics": 1,
        "questions": 1,
        "research_sessions": 1,
        "sources": 1,
        "source_versions": 1,
        "content_objects": 1,
        "excerpts": 1,
        "evidence_spans": 1,
        "claims": 1,
        "claim_revisions": 1,
        "claim_evidence": 1,
        "reports": 1,
        "idempotency_keys": 1,
    }
    with registry.connect() as conn:
        source = conn.execute("SELECT * FROM sources").fetchone()
        excerpt = conn.execute("SELECT * FROM excerpts").fetchone()
        claim = conn.execute("SELECT * FROM claims").fetchone()
        revision = conn.execute("SELECT * FROM claim_revisions").fetchone()
        relationship = conn.execute("SELECT * FROM claim_evidence").fetchone()
        report = conn.execute("SELECT * FROM reports").fetchone()
    assert source["visibility"] == "private"
    assert source["review_state"] == "unreviewed"
    assert excerpt["visibility"] == "private"
    assert excerpt["review_state"] == "unreviewed"
    assert claim["current_revision_id"] == revision["id"]
    assert claim["title"] == revision["title"]
    assert claim["statement"] == revision["statement"]
    assert claim["status"] == revision["status"] == "supported"
    assert relationship["relationship"] == "supports"
    assert relationship["review_state"] == "unreviewed"
    assert report["visibility"] == "private"
    assert report["review_state"] == "unreviewed"
    assert blobs.inspect([]).stored_objects == 1

    changed = deepcopy(_bundle())
    changed["claims"][0]["statement"] = "A different request body."
    with pytest.raises(IdempotencyConflict):
        deposits.deposit(changed)
    assert _counts(registry)["claim_revisions"] == 1


def test_concurrent_identical_deposits_commit_once_and_replay(
    tmp_path: Path,
) -> None:
    registry, _, deposits = _service(tmp_path)
    barrier = Barrier(2)

    def commit() -> str:
        barrier.wait()
        result = deposits.deposit(_bundle(key="concurrent-identical"))
        return "replay" if result.idempotent_replay else "commit"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: commit(), range(2)))

    assert sorted(outcomes) == ["commit", "replay"]
    assert _counts(registry)["claims"] == 1
    assert _counts(registry)["claim_revisions"] == 1


def test_concurrent_same_key_different_body_has_deterministic_conflict(
    tmp_path: Path,
) -> None:
    registry, _, deposits = _service(tmp_path)
    barrier = Barrier(2)
    requests = [
        _bundle(key="concurrent-conflict", title="First body"),
        _bundle(key="concurrent-conflict", title="Second body"),
    ]

    def commit(request: dict) -> str:
        barrier.wait()
        try:
            deposits.deposit(request)
        except IdempotencyConflict:
            return "conflict"
        return "commit"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(commit, requests))

    assert sorted(outcomes) == ["commit", "conflict"]
    assert _counts(registry)["claims"] == 1


def test_idempotency_isolated_by_namespace_kind_with_same_identifier(
    tmp_path: Path,
) -> None:
    registry, _, deposits = _service(tmp_path)
    user = _bundle(key="same-idempotency-key")
    user["namespace"] = {"kind": "user", "id": "shared-id"}
    organization = deepcopy(user)
    organization["namespace"] = {"kind": "org", "id": "shared-id"}

    user_result = deposits.deposit(user)
    org_result = deposits.deposit(organization)

    assert user_result.committed and org_result.committed
    assert user_result.records.claim_ids != org_result.records.claim_ids
    with registry.connect() as conn:
        rows = conn.execute(
            """
            SELECT namespace_kind FROM idempotency_keys
            WHERE namespace_id = ? AND "key" = ?
            ORDER BY namespace_kind
            """,
            ("shared-id", "same-idempotency-key"),
        ).fetchall()
    assert [row["namespace_kind"] for row in rows] == ["org", "user"]


def test_concurrent_distinct_keys_share_one_canonical_claim_identity(
    tmp_path: Path,
) -> None:
    registry, _, deposits = _service(tmp_path)
    barrier = Barrier(2)

    def commit(key: str) -> None:
        barrier.wait()
        deposits.deposit(_bundle(key=key))

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(commit, ("canonical-one", "canonical-two")))

    with registry.connect() as conn:
        claims = conn.execute(
            "SELECT COUNT(*) AS count FROM claims "
            "WHERE canonical_key = 'atomic-deposit-claim'"
        ).fetchone()["count"]
        revisions = conn.execute(
            "SELECT COUNT(*) AS count FROM claim_revisions"
        ).fetchone()["count"]
    assert claims == 1
    assert revisions == 2


def test_validate_only_runs_full_validation_without_persistent_state(
    tmp_path: Path,
) -> None:
    registry, blobs, deposits = _service(tmp_path)

    result = deposits.deposit(_bundle(validate_only=True))

    assert result.status == "validated"
    assert result.committed is False
    assert result.records.model_dump() == {
        "question_id": None,
        "run_id": None,
        "source_ids": {},
        "source_version_ids": {},
        "evidence_ids": {},
        "claim_ids": {},
        "claim_revision_ids": {},
        "report_id": None,
    }
    assert all(count == 0 for count in _counts(registry).values())
    assert blobs.staged_count() == 0
    assert blobs.inspect([]).stored_objects == 0

    committed = deepcopy(_bundle())
    committed["validate_only"] = False
    result = deposits.deposit(committed)
    assert result.committed is True


def test_evidence_only_text_is_hash_verified_then_discarded(
    tmp_path: Path,
) -> None:
    registry, blobs, deposits = _service(tmp_path)
    request = _bundle(key="evidence-only-discard")
    request["sources"][0]["version"]["snapshot"]["policy"] = "evidence_only"

    result = deposits.deposit(request)

    with registry.connect() as conn:
        version = conn.execute(
            "SELECT content_object_id, metadata_json FROM source_versions "
            "WHERE id = ?",
            (result.records.source_version_ids["source"],),
        ).fetchone()
    assert json.loads(version["metadata_json"])["snapshot_policy"] == "evidence_only"
    assert version["content_object_id"] is None
    assert blobs.inspect([]).stored_objects == 0


def test_revising_claim_reuses_source_version_and_updates_v1_mirror(
    tmp_path: Path,
) -> None:
    registry, _, deposits = _service(tmp_path)
    first = deposits.deposit(_bundle())
    claim_id = first.records.claim_ids["claim"]
    revision_id = first.records.claim_revision_ids["claim"]

    second = deposits.deposit(
        _bundle(
            key="deposit-two",
            claim_id=claim_id,
            expected_revision_id=revision_id,
            title="Deposits remain atomic",
        )
    )

    assert second.records.source_ids["source"] == first.records.source_ids["source"]
    assert (
        second.records.source_version_ids["source"]
        == first.records.source_version_ids["source"]
    )
    assert second.records.claim_ids["claim"] == claim_id
    assert second.records.claim_revision_ids["claim"] != revision_id
    with registry.connect() as conn:
        counts = conn.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM sources) AS sources, "
            "(SELECT COUNT(*) FROM source_versions) AS versions, "
            "(SELECT COUNT(*) FROM claims) AS claims, "
            "(SELECT COUNT(*) FROM claim_revisions) AS revisions, "
            "(SELECT COUNT(*) FROM claim_excerpts) AS legacy_links"
        ).fetchone()
        claim = conn.execute(
            "SELECT * FROM claims WHERE id = ?", (claim_id,)
        ).fetchone()
        revision = conn.execute(
            "SELECT * FROM claim_revisions WHERE id = ?",
            (second.records.claim_revision_ids["claim"],),
        ).fetchone()
    assert dict(counts) == {
        "sources": 1,
        "versions": 1,
        "claims": 1,
        "revisions": 2,
        "legacy_links": 1,
    }
    assert revision["revision_number"] == 2
    assert revision["supersedes_revision_id"] == revision_id
    assert claim["current_revision_id"] == revision["id"]
    assert claim["title"] == revision["title"] == "Deposits remain atomic"


@pytest.mark.parametrize(
    "step",
    [
        "after_staged_blobs",
        "before_idempotency",
        "after_idempotency",
        "after_source_identity",
        "after_source_version",
        "after_evidence",
        "after_claim",
        "after_claim_relationship",
        "after_report",
        "before_current_pointer",
        "after_current_pointer",
        "after_response_serialization",
        "before_blob_finalize",
        "after_blob_finalize",
        "before_commit",
    ],
)
def test_fault_injection_never_leaves_partial_database_state(
    tmp_path: Path,
    step: str,
) -> None:
    def inject(current: str) -> None:
        if current == step:
            raise RuntimeError(f"injected:{step}")

    registry, blobs, deposits = _service(
        tmp_path,
        fault_injector=inject,
    )

    with pytest.raises(RuntimeError, match=f"injected:{step}"):
        deposits.deposit(_bundle())

    assert all(count == 0 for count in _counts(registry).values())
    assert blobs.staged_count() == 0
    health = blobs.inspect([])
    assert health.stored_objects == 0
    assert health.orphan_keys == ()


def test_dependent_records_require_inquiry_and_supported_claims_require_evidence(
    tmp_path: Path,
) -> None:
    _, _, deposits = _service(tmp_path)
    without_inquiry = _bundle()
    without_inquiry.pop("inquiry")
    with pytest.raises(DepositError, match="inquiry is required"):
        deposits.deposit(without_inquiry)

    unsupported = _bundle()
    unsupported["claims"][0]["evidence"] = [
        {
            "evidence": {"ref": "evidence"},
            "relationship": "refutes",
        }
    ]
    with pytest.raises(ValidationError, match="supports or qualifies"):
        deposits.deposit(unsupported)


def test_deposit_cannot_publish_and_public_request_creates_no_state(
    tmp_path: Path,
) -> None:
    registry, blobs, deposits = _service(tmp_path)
    request = _bundle()
    request["visibility"] = "public"

    with pytest.raises(DepositError, match="cannot publish"):
        deposits.deposit(request)

    assert all(count == 0 for count in _counts(registry).values())
    assert blobs.staged_count() == 0
    assert blobs.inspect([]).stored_objects == 0


def test_external_references_resolve_within_namespace_only(
    tmp_path: Path,
) -> None:
    registry, _, deposits = _service(tmp_path)
    first = deposits.deposit(_bundle())
    request = _bundle(key="external-references")
    request["sources"] = []
    request["evidence"][0]["source_version"] = {
        "id": first.records.source_version_ids["source"]
    }
    request["claims"][0]["canonical_key"] = "external-reference-claim"
    request["claims"][0]["evidence"][0]["evidence"] = {
        "id": first.records.evidence_ids["evidence"]
    }
    request["report"]["claims"] = [
        {"id": first.records.claim_ids["claim"]},
        {"ref": "claim"},
    ]

    result = deposits.deposit(request)

    assert result.records.source_ids == {}
    assert result.records.source_version_ids == {}
    assert result.records.evidence_ids["evidence"]
    assert result.records.claim_ids["claim"]
    with registry.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM report_claims WHERE report_id = ?",
            (result.records.report_id,),
        ).fetchone()[0] == 2

    crossing = deepcopy(request)
    crossing["idempotency_key"] = "cross-namespace"
    crossing["namespace"] = {"kind": "org", "id": "other"}
    with pytest.raises(DepositReferenceNotFound):
        deposits.deposit(crossing)


@pytest.mark.parametrize(
    ("status", "relationship", "legacy_status"),
    [
        ("contested", "refutes", "conflicted"),
        ("draft", "contextualizes", "insufficient_evidence"),
        ("rejected", "refutes", "insufficient_evidence"),
    ],
)
def test_v2_claim_status_keeps_exact_revision_and_safe_v1_mirror(
    tmp_path: Path,
    status: str,
    relationship: str,
    legacy_status: str,
) -> None:
    registry, _, deposits = _service(tmp_path)

    result = deposits.deposit(
        _bundle(status=status, relationship=relationship)
    )

    with registry.connect() as conn:
        claim = conn.execute(
            "SELECT * FROM claims WHERE id = ?",
            (result.records.claim_ids["claim"],),
        ).fetchone()
        revision = conn.execute(
            "SELECT * FROM claim_revisions WHERE id = ?",
            (result.records.claim_revision_ids["claim"],),
        ).fetchone()
    assert revision["status"] == status
    assert claim["status"] == legacy_status


def test_minimal_source_free_deposit_has_null_question_and_run(
    tmp_path: Path,
) -> None:
    registry, _, deposits = _service(tmp_path)
    result = deposits.deposit(
        {
            "protocol": "research-deposit/v2",
            "idempotency_key": "minimal",
            "run": {
                "client_ref": "run",
                "mode": "manual",
                "provenance": {},
            },
            "sources": [],
            "evidence": [],
            "claims": [],
        }
    )

    assert result.records.question_id is None
    assert result.records.run_id is None
    assert _counts(registry)["idempotency_keys"] == 1


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres atomic deposit test requires TEST_DATABASE_URL",
)
def test_postgres_atomic_deposit_uses_the_same_application_path(
    tmp_path: Path,
) -> None:
    suffix = uuid4().hex[:8]
    registry = RegistryService(os.environ["TEST_DATABASE_URL"])
    registry.initialize()
    deposits = ResearchDepositService(
        registry.database,
        FilesystemBlobStore(tmp_path / "postgres-blobs"),
    )
    request = _bundle(key=f"postgres-v2-{suffix}")
    request["inquiry"]["prompt"] += f" {suffix}"
    request["inquiry"]["topic_label"] += f" {suffix}"
    request["sources"][0]["identity"]["locator"] += f"-{suffix}"
    request["sources"][0]["identity"]["canonical_key"] += f"-{suffix}"
    request["sources"][0]["version"]["version_key"] += f"-{suffix}"
    request["sources"][0]["version"]["canonical_locator"] += f"-{suffix}"
    request["claims"][0]["canonical_key"] += f"-{suffix}"

    first = deposits.deposit(request)
    replay = deposits.deposit(request)

    assert first.committed is True
    assert replay.idempotent_replay is True
    assert replay.records == first.records
    assert registry.database.kind == "postgres"
