from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from research_registry.application.deposit import (
    DepositError,
    ResearchDepositService,
)
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.service import RegistryService
from tests.test_v2_deposit import CONTENT, QUOTE, _bundle


def _unique_bundle(*, key: str, suffix: str) -> dict:
    payload = deepcopy(_bundle(key=key))
    payload["inquiry"]["prompt"] += f" {suffix}"
    payload["inquiry"]["topic_label"] += f" {suffix}"
    payload["sources"][0]["identity"]["locator"] += f"-{suffix}"
    payload["sources"][0]["identity"]["canonical_key"] += f"-{suffix}"
    payload["sources"][0]["version"]["version_key"] += f"-{suffix}"
    payload["sources"][0]["version"]["canonical_locator"] += f"-{suffix}"
    content = f"{CONTENT} {suffix}"
    encoded = content.encode("utf-8")
    payload["sources"][0]["version"]["snapshot"]["text"] = content
    payload["sources"][0]["version"]["snapshot"]["byte_count"] = len(encoded)
    payload["sources"][0]["version"]["content_sha256"] = sha256(
        encoded
    ).hexdigest()
    payload["claims"][0]["canonical_key"] += f"-{suffix}"
    return payload


def _service(
    tmp_path: Path,
    *,
    database: str | Path | None = None,
    suffix: str = "sqlite",
) -> tuple[RegistryService, FilesystemBlobStore, ResearchDepositService]:
    registry = RegistryService(database or tmp_path / "registry.sqlite3")
    registry.initialize()
    blobs = FilesystemBlobStore(tmp_path / f"anchor-blobs-{suffix}")
    return registry, blobs, ResearchDepositService(registry.database, blobs)


def _evidence_row(registry: RegistryService, evidence_id: str):
    with registry.connect() as conn:
        return conn.execute(
            "SELECT * FROM evidence_spans WHERE id = ?",
            (evidence_id,),
        ).fetchone()


def _table_counts(registry: RegistryService) -> dict[str, int]:
    tables = (
        "sources",
        "source_versions",
        "content_objects",
        "evidence_spans",
        "excerpts",
        "claims",
        "claim_revisions",
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


def test_deposit_resolves_exact_evidence_and_records_resolution(
    tmp_path: Path,
) -> None:
    registry, _, deposits = _service(tmp_path)
    result = deposits.deposit(_bundle(key="anchor-resolved"))
    evidence_id = result.records.evidence_ids["evidence"]
    row = _evidence_row(registry, evidence_id)
    metadata = json.loads(row["metadata_json"])

    assert result.warnings == []
    assert row["anchor_state"] == "resolved"
    assert row["last_resolved_at"] is not None
    assert metadata["anchor_validation"] == {
        "status": "resolved",
        "content_basis": "request_snapshot",
        "resolved_at": row["last_resolved_at"],
        "resolution": {
            "selector_type": "text_quote",
            "start": CONTENT.index(QUOTE),
            "end": CONTENT.index(QUOTE) + len(QUOTE),
        },
    }


def test_deposit_resolves_external_retained_source_version(
    tmp_path: Path,
) -> None:
    registry, _, deposits = _service(tmp_path)
    first = deposits.deposit(_bundle(key="anchor-external-first"))

    second = _unique_bundle(
        key="anchor-external-second",
        suffix="external",
    )
    second["sources"] = []
    second["claims"] = []
    second["report"] = None
    second["evidence"][0]["source_version"] = {
        "id": first.records.source_version_ids["source"]
    }
    result = deposits.deposit(second)
    row = _evidence_row(
        registry,
        result.records.evidence_ids["evidence"],
    )
    metadata = json.loads(row["metadata_json"])

    assert result.warnings == []
    assert row["anchor_state"] == "resolved"
    assert metadata["anchor_validation"]["content_basis"] == "retained_blob"


def test_evidence_only_text_is_validated_without_retention(
    tmp_path: Path,
) -> None:
    registry, _, deposits = _service(tmp_path)
    payload = _bundle(key="anchor-evidence-only")
    payload["sources"][0]["version"]["snapshot"]["policy"] = "evidence_only"

    result = deposits.deposit(payload)
    row = _evidence_row(
        registry,
        result.records.evidence_ids["evidence"],
    )
    metadata = json.loads(row["metadata_json"])
    with registry.connect() as conn:
        content_objects = conn.execute(
            "SELECT COUNT(*) AS count FROM content_objects"
        ).fetchone()["count"]

    assert content_objects == 0
    assert row["anchor_state"] == "resolved"
    assert metadata["anchor_validation"]["content_basis"] == "transient_snapshot"


def test_unavailable_source_content_is_explicitly_unverified(
    tmp_path: Path,
) -> None:
    registry, _, deposits = _service(tmp_path)
    payload = _bundle(key="anchor-metadata-only")
    snapshot = payload["sources"][0]["version"]["snapshot"]
    snapshot["policy"] = "metadata_only"
    snapshot.pop("text")

    result = deposits.deposit(payload)
    row = _evidence_row(
        registry,
        result.records.evidence_ids["evidence"],
    )
    metadata = json.loads(row["metadata_json"])

    assert result.warnings == [
        "EVIDENCE_ANCHOR_UNVERIFIED:source_content_unavailable:1"
    ]
    assert row["anchor_state"] == "unverified"
    assert row["last_resolved_at"] is None
    assert metadata["anchor_validation"] == {
        "status": "unverified",
        "selector_type": "text_quote",
        "content_basis": "none",
        "reason": "source_content_unavailable",
    }


def test_page_selector_without_page_index_is_unverified_not_fabricated(
    tmp_path: Path,
) -> None:
    registry, _, deposits = _service(tmp_path)
    payload = _bundle(key="anchor-page-index")
    payload["evidence"][0]["selector"] = {
        "type": "page_range",
        "start_page": 1,
        "end_page": 1,
        "exact": QUOTE,
    }

    result = deposits.deposit(payload)
    row = _evidence_row(
        registry,
        result.records.evidence_ids["evidence"],
    )
    metadata = json.loads(row["metadata_json"])

    assert result.warnings == [
        "EVIDENCE_ANCHOR_UNVERIFIED:page_index_unavailable:1"
    ]
    assert row["anchor_state"] == "unverified"
    assert metadata["anchor_validation"]["reason"] == "page_index_unavailable"


@pytest.mark.parametrize(
    ("key", "mutate", "error"),
    [
        (
            "anchor-offset-mismatch",
            lambda payload: payload["evidence"][0]["selector"].update(
                {"start": 0, "end": len(QUOTE)}
            ),
            "EVIDENCE_ANCHOR_UNRESOLVED",
        ),
        (
            "anchor-exact-mismatch",
            lambda payload: payload["evidence"][0]["selector"].update(
                {"exact": "different exact text"}
            ),
            "EVIDENCE_SELECTOR_INVALID",
        ),
    ],
)
def test_invalid_anchor_rejects_atomically(
    tmp_path: Path,
    key: str,
    mutate,
    error: str,
) -> None:
    registry, blobs, deposits = _service(tmp_path)
    payload = _bundle(key=key)
    mutate(payload)

    with pytest.raises(DepositError, match=error):
        deposits.deposit(payload)

    assert _table_counts(registry) == {
        "sources": 0,
        "source_versions": 0,
        "content_objects": 0,
        "evidence_spans": 0,
        "excerpts": 0,
        "claims": 0,
        "claim_revisions": 0,
        "reports": 0,
        "idempotency_keys": 0,
    }
    assert blobs.staged_count() == 0
    assert blobs.inspect([]).stored_objects == 0


def test_ambiguous_anchor_rejects_atomically(tmp_path: Path) -> None:
    registry, blobs, deposits = _service(tmp_path)
    payload = _bundle(key="anchor-ambiguous")
    content = f"{QUOTE} and then {QUOTE}"
    encoded = content.encode("utf-8")
    payload["sources"][0]["version"]["content_sha256"] = sha256(
        encoded
    ).hexdigest()
    payload["sources"][0]["version"]["snapshot"]["text"] = content
    payload["sources"][0]["version"]["snapshot"]["byte_count"] = len(encoded)
    payload["evidence"][0]["selector"] = {
        "type": "text_quote",
        "exact": QUOTE,
    }

    with pytest.raises(
        DepositError,
        match="EVIDENCE_ANCHOR_AMBIGUOUS",
    ):
        deposits.deposit(payload)

    assert _table_counts(registry)["evidence_spans"] == 0
    assert _table_counts(registry)["idempotency_keys"] == 0
    assert blobs.staged_count() == 0
    assert blobs.inspect([]).stored_objects == 0


def test_validate_only_performs_anchor_validation_without_writes(
    tmp_path: Path,
) -> None:
    registry, blobs, deposits = _service(tmp_path)
    payload = _bundle(key="anchor-validate-only", validate_only=True)

    result = deposits.deposit(payload)

    assert result.status == "validated"
    assert result.committed is False
    assert result.warnings == []
    assert all(value == 0 for value in _table_counts(registry).values())
    assert blobs.staged_count() == 0
    assert blobs.inspect([]).stored_objects == 0


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="PostgreSQL anchor validation requires TEST_DATABASE_URL",
)
def test_postgres_deposit_anchor_resolution_and_atomic_rejection(
    tmp_path: Path,
) -> None:
    suffix = uuid4().hex[:10]
    registry, blobs, deposits = _service(
        tmp_path,
        database=os.environ["TEST_DATABASE_URL"],
        suffix=suffix,
    )
    valid = _unique_bundle(
        key=f"pg-anchor-valid-{suffix}",
        suffix=suffix,
    )
    result = deposits.deposit(valid)
    row = _evidence_row(
        registry,
        result.records.evidence_ids["evidence"],
    )
    assert row["anchor_state"] == "resolved"

    invalid = _unique_bundle(
        key=f"pg-anchor-invalid-{suffix}",
        suffix=f"{suffix}-invalid",
    )
    invalid["evidence"][0]["selector"].update(
        {"start": 0, "end": len(QUOTE)}
    )
    before = _table_counts(registry)
    with pytest.raises(
        DepositError,
        match="EVIDENCE_ANCHOR_UNRESOLVED",
    ):
        deposits.deposit(invalid)
    assert _table_counts(registry) == before
    assert blobs.staged_count() == 0
