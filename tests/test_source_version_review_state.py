from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from research_registry.application.migrate_v2 import run_v2_backfill
from research_registry.application.review import ResearchReviewService
from research_registry.application.search import ResearchSearchService
from research_registry.application.source_versions import SourceVersionService
from research_registry.contracts.v2 import ResearchSearchRequest
from research_registry.domain.sources import SourceVersionSpec
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.models import ReviewRequest, SourceCreate
from research_registry.persistence.read_adapter import (
    CurrentRetrievalAdapter,
    ReadAccess,
)
from research_registry.retrieval.projection import rebuild_search_documents
from research_registry.service import RegistryService
from research_registry.web_v2 import V2WebViewService


def _version_spec(
    source_id: str,
    marker: str,
    label: str,
    *,
    hour: int,
) -> SourceVersionSpec:
    content = f"{marker}:{label}".encode()
    return SourceVersionSpec(
        source_id=source_id,
        version_key=f"{marker}:{label}",
        version_kind="note",
        retrieved_at=f"2026-07-31T{hour:02d}:00:00+00:00",
        content_sha256=sha256(content).hexdigest(),
        canonical_locator=f"note:{marker}:{label}",
        snapshot_policy="metadata_only",
        byte_count=len(content),
    )


def _version_states(
    service: RegistryService,
    source_id: str,
    marker: str,
    version_ids: list[str],
    *,
    access: ReadAccess,
) -> tuple[dict[str, str | None], dict[str, str | None], dict[str, str | None]]:
    retrieval = CurrentRetrievalAdapter(service.database)
    listed = {
        row["id"]: row["review_state"]
        for row in retrieval.list_source_versions(
            source_id=source_id,
            access=access,
        )
        if row["id"] in version_ids
    }
    fetched = {}
    for version_id in version_ids:
        record = retrieval.get_record(version_id, access=access)
        assert record is not None
        fetched[version_id] = record.review_state
    response = ResearchSearchService(retrieval).search(
        ResearchSearchRequest(
            protocol="research-search/v2",
            query=marker,
            kinds=["source_version"],
            include_private=True,
            limit=100,
        ),
        access=access,
    )
    searched = {
        hit.id: hit.review_state
        for hit in response.hits
        if hit.id in version_ids
    }
    return listed, fetched, searched


def _version_conflicts(
    service: RegistryService,
    source_id: str,
    marker: str,
    version_ids: list[str],
    *,
    access: ReadAccess,
) -> tuple[dict[str, str | None], dict[str, str | None], dict[str, str | None]]:
    retrieval = CurrentRetrievalAdapter(service.database)
    listed = {
        row["id"]: row["conflict_state"]
        for row in retrieval.list_source_versions(
            source_id=source_id,
            access=access,
        )
        if row["id"] in version_ids
    }
    fetched = {}
    for version_id in version_ids:
        record = retrieval.get_record(version_id, access=access)
        assert record is not None
        fetched[version_id] = record.conflict_state
    response = ResearchSearchService(retrieval).search(
        ResearchSearchRequest(
            protocol="research-search/v2",
            query=marker,
            kinds=["source_version"],
            include_private=True,
            limit=100,
        ),
        access=access,
    )
    searched = {
        hit.id: hit.conflict_state
        for hit in response.hits
        if hit.id in version_ids
    }
    return listed, fetched, searched


def _inbox_ids(
    service: RegistryService,
    *,
    access: ReadAccess,
) -> set[str]:
    # review_inbox does not consult settings; this keeps the reusable
    # cross-dialect regression on the same presentation path as the UI.
    view = V2WebViewService(service.database, None).review_inbox(  # type: ignore[arg-type]
        access=access
    )
    return {item.id for item in view.items}


def exercise_source_version_review_state_isolation(
    service: RegistryService,
    tmp_path: Path,
    *,
    suffix: str,
) -> None:
    service.initialize()
    marker = f"svstate{suffix.replace('-', '')}"
    source = service.create_source(
        SourceCreate(
            locator=f"note:{marker}",
            title=f"Source version isolation {marker}",
            source_type="note",
        ),
        _project_v2=False,
    )
    versions = SourceVersionService(
        service.database,
        FilesystemBlobStore(tmp_path / f"{marker}-blobs"),
    )
    version_a = versions.create_or_reuse(
        _version_spec(source.id, marker, "a", hour=0)
    ).record
    version_b = versions.create_or_reuse(
        _version_spec(source.id, marker, "b", hour=1)
    ).record
    access = ReadAccess(
        include_private=True,
        namespace_kind="user",
        namespace_id="local",
        local_trusted=True,
    )

    reviewed_a = ResearchReviewService(service.database).review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"{marker}-approve-a",
            "entity": {"kind": "source_version", "id": version_a.id},
            "action": "approve",
            "expected_state": "unreviewed",
        }
    )

    assert reviewed_a.idempotent_replay is False
    expected = {version_a.id: "reviewed", version_b.id: "unreviewed"}
    listed, fetched, searched = _version_states(
        service,
        source.id,
        marker,
        list(expected),
        access=access,
    )
    assert listed == fetched == searched == expected
    assert version_b.id in _inbox_ids(service, access=access)
    assert version_a.id not in _inbox_ids(service, access=access)
    with service.connect() as conn:
        mirror = conn.execute(
            "SELECT review_state, conflict_state FROM sources WHERE id = ?",
            (source.id,),
        ).fetchone()
    assert (mirror["review_state"], mirror["conflict_state"]) == (
        "unreviewed",
        "none",
    )
    reviews = ResearchReviewService(service.database)
    reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"{marker}-contest-a",
            "entity": {"kind": "source_version", "id": version_a.id},
            "action": "contest",
            "expected_state": "reviewed",
        }
    )
    conflicts = _version_conflicts(
        service,
        source.id,
        marker,
        [version_a.id, version_b.id],
        access=access,
    )
    assert conflicts[0] == conflicts[1] == conflicts[2] == {
        version_a.id: "conflicted",
        version_b.id: "none",
    }
    reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"{marker}-contest-b",
            "entity": {"kind": "source_version", "id": version_b.id},
            "action": "contest",
            "expected_state": "unreviewed",
        }
    )
    conflicts = _version_conflicts(
        service,
        source.id,
        marker,
        [version_a.id, version_b.id],
        access=access,
    )
    assert conflicts[0] == conflicts[1] == conflicts[2] == {
        version_a.id: "conflicted",
        version_b.id: "conflicted",
    }
    reviews.review(
        {
            "protocol": "research-review/v2",
            "idempotency_key": f"{marker}-approve-b",
            "entity": {"kind": "source_version", "id": version_b.id},
            "action": "approve",
            "expected_state": "flagged",
        }
    )
    with service.connect() as conn:
        conn.execute(
            """
            INSERT INTO review_events (
                id, entity_kind, entity_id, action, from_state, to_state,
                actor_type, created_at, metadata_json
            ) VALUES (?, 'source_version', ?, 'reject', 'flagged', 'flagged',
                      'human', '2026-08-01T00:00:00+00:00', '{}')
            """,
            (f"rev_{marker}_reject_a", version_a.id),
        )
        rebuild_search_documents(conn)
    expected = {version_a.id: "flagged", version_b.id: "reviewed"}
    listed, fetched, searched = _version_states(
        service,
        source.id,
        marker,
        list(expected),
        access=access,
    )
    assert listed == fetched == searched == expected
    expected_conflicts = {version_a.id: "none", version_b.id: "none"}
    listed_conflicts, fetched_conflicts, searched_conflicts = (
        _version_conflicts(
            service,
            source.id,
            marker,
            list(expected),
            access=access,
        )
    )
    assert (
        listed_conflicts
        == fetched_conflicts
        == searched_conflicts
        == expected_conflicts
    )
    version_detail = V2WebViewService(  # type: ignore[arg-type]
        service.database, None
    ).source_version_detail(version_a.id, access=access, can_review=True)
    assert version_detail.version.conflict_state == "none"

    version_c = versions.create_or_reuse(
        _version_spec(source.id, marker, "c", hour=2)
    ).record
    expected[version_c.id] = "unreviewed"
    listed, fetched, searched = _version_states(
        service,
        source.id,
        marker,
        list(expected),
        access=access,
    )
    assert listed == fetched == searched == expected
    assert version_c.id in _inbox_ids(service, access=access)

    retained = ReviewRequest(kind="source", record_id=source.id)
    service.review(retained)
    service.review(retained)
    expected[version_c.id] = "reviewed"
    listed, fetched, searched = _version_states(
        service,
        source.id,
        marker,
        list(expected),
        access=access,
    )
    assert listed == fetched == searched == expected
    with service.connect() as conn:
        events = conn.execute(
            """
            SELECT action
            FROM review_events
            WHERE entity_kind = 'source_version' AND entity_id = ?
            ORDER BY created_at, id
            """,
            (version_c.id,),
        ).fetchall()
        mirror = conn.execute(
            "SELECT review_state, conflict_state FROM sources WHERE id = ?",
            (source.id,),
        ).fetchone()
    assert [row["action"] for row in events] == ["approve"]
    assert (mirror["review_state"], mirror["conflict_state"]) == (
        "reviewed",
        "none",
    )

    # A new native observation invalidates the stable v1 mirror until that
    # exact newest version receives a decision.
    version_d = versions.create_or_reuse(
        _version_spec(source.id, marker, "d", hour=3)
    ).record
    expected[version_d.id] = "unreviewed"
    listed, fetched, searched = _version_states(
        service,
        source.id,
        marker,
        list(expected),
        access=access,
    )
    assert listed == fetched == searched == expected
    with service.connect() as conn:
        mirror = conn.execute(
            "SELECT review_state, conflict_state FROM sources WHERE id = ?",
            (source.id,),
        ).fetchone()
    assert (mirror["review_state"], mirror["conflict_state"]) == (
        "unreviewed",
        "none",
    )
    assert version_d.id in _inbox_ids(service, access=access)
    # Simulate a pre-fix alpha compatibility mirror with no exact event or
    # projection identity. Adoption must repair it without inventing review
    # provenance for the native version.
    with service.connect() as conn:
        conn.execute(
            """
            UPDATE sources
            SET review_state = 'reviewed', conflict_state = 'conflicted'
            WHERE id = ?
            """,
            (source.id,),
        )
        conn.execute(
            """
            DELETE FROM legacy_projection_identity
            WHERE legacy_kind = 'source' AND legacy_id = ?
            """,
            (source.id,),
        )

    legacy = service.create_source(
        SourceCreate(
            locator=f"note:{marker}:legacy-reviewed",
            title=f"Legacy reviewed source {marker}",
            source_type="note",
            review_state="reviewed",
        ),
        _project_v2=False,
    )
    first_backfill = run_v2_backfill(
        service.database_url,
        batch_size=20,
        resume=True,
    )
    assert first_backfill.error_count == 0
    with service.connect() as conn:
        projection = conn.execute(
            """
            SELECT v2_id
            FROM legacy_projection_identity
            WHERE legacy_kind = 'source'
              AND legacy_id = ?
              AND v2_kind = 'source_version'
            """,
            (legacy.id,),
        ).fetchone()
        assert projection is not None
        legacy_version_id = projection["v2_id"]
        repaired_projection = conn.execute(
            """
            SELECT v2_id FROM legacy_projection_identity
            WHERE legacy_kind = 'source' AND legacy_id = ?
            """,
            (source.id,),
        ).fetchone()
        repaired_mirror = conn.execute(
            """
            SELECT review_state, conflict_state
            FROM sources WHERE id = ?
            """,
            (source.id,),
        ).fetchone()
        native_migration_events = conn.execute(
            """
            SELECT COUNT(*) AS count FROM review_events
            WHERE entity_kind = 'source_version' AND entity_id = ?
              AND actor_type = 'migration'
            """,
            (version_d.id,),
        ).fetchone()["count"]
        legacy_events = conn.execute(
            """
            SELECT action, from_state, to_state, actor_type
            FROM review_events
            WHERE entity_kind = 'source_version' AND entity_id = ?
            ORDER BY created_at, id
            """,
            (legacy_version_id,),
        ).fetchall()
        rebuild_search_documents(conn)
        tracked_ids = [*expected, legacy_version_id]
        before_projection = [
            (row["id"], row["review_state"])
            for row in conn.execute(
                """
                SELECT id, review_state
                FROM search_documents
                WHERE id IN ("""
                + ",".join("?" for _ in tracked_ids)
                + ") ORDER BY id",
                tuple(tracked_ids),
            ).fetchall()
        ]
        before_events = [
            (
                row["entity_id"],
                row["action"],
                row["from_state"],
                row["to_state"],
                row["actor_type"],
            )
            for row in conn.execute(
                """
                SELECT entity_id, action, from_state, to_state, actor_type
                FROM review_events
                WHERE entity_id IN ("""
                + ",".join("?" for _ in tracked_ids)
                + ") ORDER BY entity_id, created_at, id",
                tuple(tracked_ids),
            ).fetchall()
        ]
    assert [
        (
            row["action"],
            row["from_state"],
            row["to_state"],
            row["actor_type"],
        )
        for row in legacy_events
    ] == [("approve", "unreviewed", "reviewed", "migration")]
    assert repaired_projection["v2_id"] == version_d.id
    assert (
        repaired_mirror["review_state"],
        repaired_mirror["conflict_state"],
    ) == ("unreviewed", "none")
    assert native_migration_events == 0
    legacy_record = CurrentRetrievalAdapter(service.database).get_record(
        legacy_version_id,
        access=access,
    )
    assert legacy_record is not None
    assert legacy_record.review_state == "reviewed"
    assert legacy_version_id not in _inbox_ids(service, access=access)
    assert dict(before_projection) == {
        **expected,
        legacy_version_id: "reviewed",
    }

    repeated = run_v2_backfill(
        service.database_url,
        batch_size=20,
        resume=True,
    )
    assert repeated.error_count == 0
    with service.connect() as conn:
        rebuild_search_documents(conn)
        after_projection = [
            (row["id"], row["review_state"])
            for row in conn.execute(
                """
                SELECT id, review_state
                FROM search_documents
                WHERE id IN ("""
                + ",".join("?" for _ in tracked_ids)
                + ") ORDER BY id",
                tuple(tracked_ids),
            ).fetchall()
        ]
        after_events = [
            (
                row["entity_id"],
                row["action"],
                row["from_state"],
                row["to_state"],
                row["actor_type"],
            )
            for row in conn.execute(
                """
                SELECT entity_id, action, from_state, to_state, actor_type
                FROM review_events
                WHERE entity_id IN ("""
                + ",".join("?" for _ in tracked_ids)
                + ") ORDER BY entity_id, created_at, id",
                tuple(tracked_ids),
            ).fetchall()
        ]
    assert after_projection == before_projection
    assert after_events == before_events


def test_sqlite_source_version_review_state_isolation(tmp_path: Path) -> None:
    exercise_source_version_review_state_isolation(
        RegistryService(tmp_path / "source-version-review.sqlite3"),
        tmp_path,
        suffix=uuid4().hex[:10],
    )
