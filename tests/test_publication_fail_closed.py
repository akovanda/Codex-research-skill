from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from research_registry.application.deposit import (
    DepositError,
    ResearchDepositService,
)
from research_registry.application.refresh import (
    CapturePolicy,
    RefreshModeDenied,
    ResearchRefreshService,
    SourceCaptureCoordinator,
)
from research_registry.application.review import (
    InvalidClaimTransition,
    ResearchReviewService,
)
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.models import PublishRequest, ReviewRequest, SourceCreate
from research_registry.retrieval.projection import rebuild_search_documents
from research_registry.service import RegistryService
from tests.test_v2_deposit import _bundle


def exercise_public_parent_fail_closed(
    service: RegistryService,
    tmp_path: Path,
    *,
    suffix: str,
) -> None:
    service.initialize()
    blobs = FilesystemBlobStore(tmp_path / f"publication-blobs-{suffix}")
    deposits = ResearchDepositService(service.database, blobs)
    retained_source = service.create_source(
        SourceCreate(
            locator=f"note:retained-publication-{suffix}",
            title="Retained static publication",
            source_type="note",
            snapshot_present=True,
            namespace_kind="user",
            namespace_id=suffix,
        )
    )
    service.review(
        ReviewRequest(kind="source", record_id=retained_source.id)
    )
    service.publish(
        PublishRequest(kind="source", record_id=retained_source.id)
    )
    assert service.get_source(retained_source.id).visibility == "public"

    initial_payload = _bundle(key=f"publication-initial-{suffix}")
    initial_payload["namespace"] = {"kind": "user", "id": suffix}
    initial_payload["inquiry"]["prompt"] += f" {suffix}"
    initial_payload["inquiry"]["topic_label"] += f" {suffix}"
    initial_payload["sources"][0]["identity"]["locator"] += f"-{suffix}"
    initial_payload["sources"][0]["identity"]["canonical_key"] += f"-{suffix}"
    initial_payload["sources"][0]["version"]["version_key"] += f"-{suffix}"
    initial_payload["sources"][0]["version"]["canonical_locator"] += f"-{suffix}"
    initial_payload["claims"][0]["canonical_key"] += f"-{suffix}"
    initial = deposits.deposit(initial_payload)
    ids = initial.records
    source_id = ids.source_ids["source"]
    version_id = ids.source_version_ids["source"]
    evidence_id = ids.evidence_ids["evidence"]
    claim_id = ids.claim_ids["claim"]
    revision_id = ids.claim_revision_ids["claim"]
    assert ids.question_id is not None

    with service.connect() as conn:
        audit_before = conn.execute(
            "SELECT COUNT(*) AS count FROM audit_log WHERE action = 'publish'"
        ).fetchone()["count"]
    with pytest.raises(PermissionError, match="native v2 publication is disabled"):
        service.publish(PublishRequest(kind="claim", record_id=claim_id))
    with service.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) AS count FROM audit_log WHERE action = 'publish'"
            ).fetchone()["count"]
            == audit_before
        )

        # Represent data that a pre-fix alpha already made public. The runtime
        # must keep it readable without allowing any new private observation
        # or child to inherit that public visibility.
        for table, record_id in (
            ("sources", source_id),
            ("claims", claim_id),
            ("questions", ids.question_id),
        ):
            conn.execute(
                f"""
                UPDATE {table}
                SET visibility = 'public', public_index_state = 'included'
                WHERE id = ?
                """,
                (record_id,),
            )
        rebuild_search_documents(conn)
        counts_before = {
            table: conn.execute(
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()["count"]
            for table in (
                "source_versions",
                "evidence_spans",
                "claim_revisions",
                "research_sessions",
                "reports",
                "idempotency_keys",
            )
        }
        pointer_before = conn.execute(
            "SELECT current_revision_id FROM claims WHERE id = ?",
            (claim_id,),
        ).fetchone()["current_revision_id"]

    public_claims_before = [
        hit.id
        for hit in service.search(
            "atomic", kind="claim", include_private=False
        ).hits
    ]
    public_question = service.get_question(ids.question_id)
    assert public_question.latest_session_id is None
    assert public_question.latest_report_id is None
    assert next(
        hit
        for hit in service.search(
            "atomic", kind="question", include_private=False
        ).hits
        if hit.id == ids.question_id
    ).id == ids.question_id

    new_version = deepcopy(initial_payload)
    new_version["idempotency_key"] = f"publication-version-{suffix}"
    new_version["inquiry"] = None
    new_version["claims"] = []
    new_version["report"] = None
    new_version["evidence"] = []
    new_version["sources"][0]["version"]["version_key"] += "-new"
    with pytest.raises(DepositError, match="PUBLIC_PARENT_MUTATION_DENIED"):
        deposits.deposit(new_version)

    new_evidence = deepcopy(initial_payload)
    new_evidence["idempotency_key"] = f"publication-evidence-{suffix}"
    new_evidence["inquiry"]["prompt"] += " independent evidence"
    new_evidence["inquiry"]["topic_label"] += " independent evidence"
    new_evidence["sources"] = []
    new_evidence["claims"] = []
    new_evidence["report"] = None
    new_evidence["evidence"][0]["source_version"] = {"id": version_id}
    with pytest.raises(DepositError, match="PUBLIC_PARENT_MUTATION_DENIED"):
        deposits.deposit(new_evidence)

    new_revision = deepcopy(initial_payload)
    new_revision["idempotency_key"] = f"publication-revision-{suffix}"
    new_revision["inquiry"]["prompt"] += " independent revision"
    new_revision["inquiry"]["topic_label"] += " independent revision"
    new_revision["sources"] = []
    new_revision["evidence"] = []
    new_revision["report"] = None
    new_revision["claims"][0]["claim_id"] = claim_id
    new_revision["claims"][0]["expected_revision_id"] = revision_id
    new_revision["claims"][0]["canonical_key"] = None
    new_revision["claims"][0]["evidence"] = [
        {"evidence": {"id": evidence_id}, "relationship": "supports"}
    ]
    with pytest.raises(DepositError, match="PUBLIC_PARENT_MUTATION_DENIED"):
        deposits.deposit(new_revision)

    public_question_reuse = deepcopy(initial_payload)
    public_question_reuse["idempotency_key"] = f"publication-question-{suffix}"
    public_question_reuse["sources"][0]["identity"]["locator"] += "-other"
    public_question_reuse["sources"][0]["identity"]["canonical_key"] += "-other"
    public_question_reuse["sources"][0]["version"]["version_key"] += "-other"
    public_question_reuse["sources"][0]["version"]["canonical_locator"] += "-other"
    public_question_reuse["claims"] = []
    public_question_reuse["report"] = None
    public_question_reuse["evidence"] = []
    with pytest.raises(DepositError, match="PUBLIC_PARENT_MUTATION_DENIED"):
        deposits.deposit(public_question_reuse)

    capture = SourceCaptureCoordinator(
        service.database,
        CapturePolicy(
            enabled_modes=frozenset({"capture"}),
            allowed_source_kinds=frozenset({"web", "doi", "git_blob"}),
        ),
    )
    refresh = ResearchRefreshService(
        service.database,
        capture_coordinator=capture,
    )
    with pytest.raises(RefreshModeDenied, match="PUBLIC_PARENT_MUTATION_DENIED"):
        refresh.refresh(
            {
                "protocol": "research-refresh/v2",
                "idempotency_key": f"publication-refresh-{suffix}",
                "mode": "capture",
                "entities": [{"kind": "source", "id": source_id}],
            },
            namespace_kind="user",
            namespace_id=suffix,
        )

    with pytest.raises(
        InvalidClaimTransition, match="INVALID_CLAIM_TRANSITION"
    ):
        ResearchReviewService(service.database).review(
            {
                "protocol": "research-review/v2",
                "idempotency_key": f"publication-review-{suffix}",
                "entity": {"kind": "claim_revision", "id": revision_id},
                "action": "contest",
                "expected_revision_id": revision_id,
                "expected_state": "unreviewed",
            },
            namespace_kind="user",
            namespace_id=suffix,
        )

    with service.connect() as conn:
        counts_after = {
            table: conn.execute(
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()["count"]
            for table in counts_before
        }
        assert counts_after == counts_before
        assert (
            conn.execute(
                "SELECT current_revision_id FROM claims WHERE id = ?",
                (claim_id,),
            ).fetchone()["current_revision_id"]
            == pointer_before
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) AS count FROM audit_log WHERE action = 'publish'"
            ).fetchone()["count"]
            == audit_before
        )
        assert (
            conn.execute(
                "SELECT visibility FROM claims WHERE id = ?",
                (claim_id,),
            ).fetchone()["visibility"]
            == "public"
        )

    assert [
        hit.id
        for hit in service.search(
            "atomic", kind="claim", include_private=False
        ).hits
    ] == public_claims_before
    public_question = service.get_question(ids.question_id)
    assert public_question.latest_session_id is None
    assert public_question.latest_report_id is None
    assert next(
        hit
        for hit in service.search(
            "atomic", kind="question", include_private=False
        ).hits
        if hit.id == ids.question_id
    ).id == ids.question_id


def test_public_parent_mutations_fail_closed_atomically(tmp_path: Path) -> None:
    exercise_public_parent_fail_closed(
        RegistryService(tmp_path / "registry.sqlite3"),
        tmp_path,
        suffix=f"sqlite-{uuid4().hex[:10]}",
    )
