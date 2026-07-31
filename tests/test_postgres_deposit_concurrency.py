from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

from research_registry.application.deposit import (
    IdempotencyConflict,
    ResearchDepositService,
)
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.service import RegistryService
from tests.test_v2_deposit import _bundle


pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres deposit concurrency requires TEST_DATABASE_URL",
)


def _postgres_bundle(
    *,
    suffix: str,
    key: str,
    title: str = "Deposits are atomic",
) -> dict:
    payload = deepcopy(_bundle(key=key, title=title))
    namespace_id = f"pg-deposit-concurrency-{suffix}"
    payload["namespace"] = {"kind": "user", "id": namespace_id}
    payload["inquiry"]["prompt"] = (
        f"How does the v2 deposit preserve atomicity? {suffix}"
    )
    payload["inquiry"]["topic_label"] = f"V2 atomic deposit {suffix}"
    payload["sources"][0]["identity"]["locator"] = (
        f"note:atomic-deposit-{suffix}"
    )
    payload["sources"][0]["identity"]["canonical_key"] = (
        f"atomic-deposit-note-{suffix}"
    )
    payload["sources"][0]["version"]["version_key"] = (
        f"note:atomic-deposit-v1-{suffix}"
    )
    payload["sources"][0]["version"]["canonical_locator"] = (
        f"note:atomic-deposit-{suffix}"
    )
    payload["claims"][0]["canonical_key"] = (
        f"atomic-deposit-claim-{suffix}"
    )
    content = (
        payload["sources"][0]["version"]["snapshot"]["text"]
        + f" {suffix}"
    )
    content_bytes = content.encode("utf-8")
    payload["sources"][0]["version"]["content_sha256"] = sha256(
        content_bytes
    ).hexdigest()
    payload["sources"][0]["version"]["snapshot"]["text"] = content
    payload["sources"][0]["version"]["snapshot"]["byte_count"] = len(
        content_bytes
    )
    return payload


def _service(tmp_path: Path) -> tuple[RegistryService, ResearchDepositService]:
    registry = RegistryService(os.environ["TEST_DATABASE_URL"])
    registry.initialize()
    deposits = ResearchDepositService(
        registry.database,
        FilesystemBlobStore(tmp_path / "postgres-deposit-concurrency-blobs"),
    )
    return registry, deposits


def test_postgres_concurrent_identical_deposits_commit_once_and_replay(
    tmp_path: Path,
) -> None:
    registry, deposits = _service(tmp_path)
    suffix = uuid4().hex
    request = _postgres_bundle(
        suffix=suffix,
        key=f"postgres-concurrent-identical-{suffix}",
    )
    barrier = Barrier(2)

    def commit(_: int):
        barrier.wait(timeout=30)
        return deposits.deposit(deepcopy(request))

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(commit, range(2)))

    assert sorted(receipt.idempotent_replay for receipt in receipts) == [
        False,
        True,
    ]
    assert receipts[0].records == receipts[1].records
    claim_id = receipts[0].records.claim_ids["claim"]
    namespace_id = request["namespace"]["id"]
    with registry.connect() as conn:
        claim_count = conn.execute(
            """
            SELECT COUNT(*) AS count FROM claims
            WHERE id = ? AND namespace_kind = 'user' AND namespace_id = ?
            """,
            (claim_id, namespace_id),
        ).fetchone()["count"]
        revision_count = conn.execute(
            "SELECT COUNT(*) AS count FROM claim_revisions WHERE claim_id = ?",
            (claim_id,),
        ).fetchone()["count"]
        idempotency_count = conn.execute(
            """
            SELECT COUNT(*) AS count FROM idempotency_keys
            WHERE namespace_kind = 'user' AND namespace_id = ?
              AND operation = 'research_deposit_v2' AND "key" = ?
            """,
            (namespace_id, request["idempotency_key"]),
        ).fetchone()["count"]
    assert claim_count == 1
    assert revision_count == 1
    assert idempotency_count == 1


def test_postgres_concurrent_same_key_different_body_conflicts(
    tmp_path: Path,
) -> None:
    registry, deposits = _service(tmp_path)
    suffix = uuid4().hex
    key = f"postgres-concurrent-conflict-{suffix}"
    requests = [
        _postgres_bundle(suffix=suffix, key=key, title="First body"),
        _postgres_bundle(suffix=suffix, key=key, title="Second body"),
    ]
    barrier = Barrier(2)

    def commit(request: dict) -> str:
        barrier.wait(timeout=30)
        try:
            deposits.deposit(deepcopy(request))
        except IdempotencyConflict:
            return "conflict"
        return "commit"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(commit, requests))

    assert sorted(outcomes) == ["commit", "conflict"]
    namespace_id = requests[0]["namespace"]["id"]
    canonical_key = requests[0]["claims"][0]["canonical_key"]
    with registry.connect() as conn:
        claim_count = conn.execute(
            """
            SELECT COUNT(*) AS count FROM claims
            WHERE namespace_kind = 'user' AND namespace_id = ?
              AND canonical_key = ?
            """,
            (namespace_id, canonical_key),
        ).fetchone()["count"]
        idempotency_count = conn.execute(
            """
            SELECT COUNT(*) AS count FROM idempotency_keys
            WHERE namespace_kind = 'user' AND namespace_id = ?
              AND operation = 'research_deposit_v2' AND "key" = ?
            """,
            (namespace_id, key),
        ).fetchone()["count"]
    assert claim_count == 1
    assert idempotency_count == 1


def test_postgres_concurrent_distinct_keys_share_canonical_claim(
    tmp_path: Path,
) -> None:
    registry, deposits = _service(tmp_path)
    suffix = uuid4().hex
    requests = [
        _postgres_bundle(
            suffix=suffix,
            key=f"postgres-canonical-one-{suffix}",
        ),
        _postgres_bundle(
            suffix=suffix,
            key=f"postgres-canonical-two-{suffix}",
        ),
    ]
    barrier = Barrier(2)

    def commit(request: dict):
        barrier.wait(timeout=30)
        return deposits.deposit(deepcopy(request))

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(commit, requests))

    claim_ids = {receipt.records.claim_ids["claim"] for receipt in receipts}
    revision_ids = {
        receipt.records.claim_revision_ids["claim"] for receipt in receipts
    }
    assert len(claim_ids) == 1
    assert len(revision_ids) == 2

    claim_id = next(iter(claim_ids))
    namespace_id = requests[0]["namespace"]["id"]
    canonical_key = requests[0]["claims"][0]["canonical_key"]
    with registry.connect() as conn:
        claim = conn.execute(
            """
            SELECT id, current_revision_id FROM claims
            WHERE namespace_kind = 'user' AND namespace_id = ?
              AND canonical_key = ?
            """,
            (namespace_id, canonical_key),
        ).fetchone()
        revisions = conn.execute(
            """
            SELECT id, revision_number FROM claim_revisions
            WHERE claim_id = ? ORDER BY revision_number
            """,
            (claim_id,),
        ).fetchall()
        topic_count = conn.execute(
            """
            SELECT COUNT(*) AS count FROM topics
            WHERE namespace_kind = 'user' AND namespace_id = ?
            """,
            (namespace_id,),
        ).fetchone()["count"]
        question_count = conn.execute(
            """
            SELECT COUNT(*) AS count FROM questions
            WHERE namespace_kind = 'user' AND namespace_id = ?
            """,
            (namespace_id,),
        ).fetchone()["count"]
        source_count = conn.execute(
            """
            SELECT COUNT(*) AS count FROM sources
            WHERE namespace_kind = 'user' AND namespace_id = ?
            """,
            (namespace_id,),
        ).fetchone()["count"]
        version_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM source_versions sv
            JOIN sources s ON s.id = sv.source_id
            WHERE s.namespace_kind = 'user' AND s.namespace_id = ?
            """,
            (namespace_id,),
        ).fetchone()["count"]
        idempotency_count = conn.execute(
            """
            SELECT COUNT(*) AS count FROM idempotency_keys
            WHERE namespace_kind = 'user' AND namespace_id = ?
              AND operation = 'research_deposit_v2'
              AND "key" IN (?, ?)
            """,
            (
                namespace_id,
                requests[0]["idempotency_key"],
                requests[1]["idempotency_key"],
            ),
        ).fetchone()["count"]

    assert claim is not None
    assert claim["id"] == claim_id
    assert claim["current_revision_id"] in revision_ids
    assert [row["revision_number"] for row in revisions] == [1, 2]
    assert {row["id"] for row in revisions} == revision_ids
    assert topic_count == 1
    assert question_count == 1
    assert source_count == 1
    assert version_count == 1
    assert idempotency_count == 2
