from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from research_registry.application.migrate_v2 import run_v2_backfill
from research_registry.application.source_versions import SourceVersionService
from research_registry.external_ingest import (
    CapturedVersionCandidate,
    ImportedSourceCandidate,
)
from research_registry.models import (
    FocusTuple,
    ImportDoiRequest,
    ImportUrlRequest,
    QuestionCreate,
    SourceCreate,
)
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.persistence.repositories import V2ReadRepository
from research_registry.service import RegistryService


def _candidate(locator: str, version_kind: str) -> ImportedSourceCandidate:
    content = f"captured {version_kind} evidence at {locator}".encode()
    digest = sha256(content).hexdigest()
    now = datetime.now(UTC).replace(microsecond=0)
    return ImportedSourceCandidate(
        source=SourceCreate(
            locator=locator,
            title=f"Captured {version_kind} source",
            source_type="paper" if version_kind == "doi" else "documentation",
            accessed_at=now,
            content_sha256=digest,
            snapshot_required=True,
            snapshot_present=True,
            last_verified_at=now,
        ),
        excerpt_text=f"captured {version_kind} evidence",
        version=CapturedVersionCandidate(
            version_kind=version_kind,
            version_key=f"{version_kind}:{digest}",
            content_sha256=digest,
            canonical_locator=locator,
            snapshot_policy="extracted_text",
            snapshot_bytes=content,
            media_type="text/plain",
            byte_count=len(content),
            parser_name=f"test-{version_kind}",
            parser_version="1",
        ),
    )


def _assert_captured_linkage(
    service: RegistryService,
    *,
    source_id: str,
    excerpt_id: str,
    version_kind: str,
) -> tuple[str, str]:
    with service.connect() as conn:
        versions = conn.execute(
            """
            SELECT id, version_kind
            FROM source_versions
            WHERE source_id = ?
            ORDER BY id
            """,
            (source_id,),
        ).fetchall()
        mapping = conn.execute(
            """
            SELECT legacy_kind, v2_kind, v2_id
            FROM legacy_projection_identity
            WHERE (legacy_kind = 'source' AND legacy_id = ?)
               OR (legacy_kind = 'excerpt' AND legacy_id = ?)
            ORDER BY legacy_kind
            """,
            (source_id, excerpt_id),
        ).fetchall()
        evidence = conn.execute(
            """
            SELECT e.id, e.source_version_id, e.selector_json, e.quote_text
            FROM evidence_spans e
            JOIN legacy_projection_identity lpi
              ON lpi.v2_id = e.id
            WHERE lpi.legacy_kind = 'excerpt'
              AND lpi.legacy_id = ?
              AND lpi.v2_kind = 'evidence'
            """,
            (excerpt_id,),
        ).fetchone()
        projected = V2ReadRepository(conn).get_evidence_for_legacy_excerpt(
            excerpt_id
        )

    assert len(versions) == 1
    assert versions[0]["version_kind"] == version_kind
    assert {row["legacy_kind"] for row in mapping} == {"source", "excerpt"}
    assert evidence is not None
    assert evidence["source_version_id"] == versions[0]["id"]
    if service.db_path is not None:
        resolution = SourceVersionService(
            service.database,
            FilesystemBlobStore(service.db_path.parent / "blobs"),
        ).resolve_evidence(
            evidence["source_version_id"],
            json.loads(evidence["selector_json"]),
            evidence["quote_text"],
        )
        assert resolution.selector_type == "text_quote"
    assert projected.id == evidence["id"]
    assert projected.source_version_id == versions[0]["id"]
    return versions[0]["id"], evidence["id"]


@pytest.mark.parametrize("version_kind", ["web", "doi"])
def test_url_and_doi_import_evidence_anchors_to_captured_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version_kind: str,
) -> None:
    service = RegistryService(tmp_path / f"{version_kind}.sqlite3")
    service.initialize()
    run_v2_backfill(service.database_url)
    locator = (
        "https://doi.org/10.1234/import-projection"
        if version_kind == "doi"
        else "https://example.com/import-projection"
    )
    candidate = _candidate(locator, version_kind)
    question = service.create_question(
        QuestionCreate(
            prompt=f"Does the {version_kind} import retain capture identity?",
            focus=FocusTuple(domain="imports", object=version_kind),
        )
    )

    if version_kind == "doi":
        monkeypatch.setattr(
            "research_registry.service.fetch_doi_candidate",
            lambda _: candidate,
        )
        result = service.import_doi(
            ImportDoiRequest(
                doi="10.1234/import-projection",
                question_id=question.id,
            )
        )
    else:
        monkeypatch.setattr(
            "research_registry.service.fetch_url_candidate",
            lambda _: candidate,
        )
        result = service.import_url(
            ImportUrlRequest(url=locator, question_id=question.id)
        )

    source_id = result.source_ids[0]
    excerpt_id = result.excerpt_ids[0]
    identity = _assert_captured_linkage(
        service,
        source_id=source_id,
        excerpt_id=excerpt_id,
        version_kind=version_kind,
    )

    for _ in range(2):
        run_v2_backfill(service.database_url)
        assert (
            _assert_captured_linkage(
                service,
                source_id=source_id,
                excerpt_id=excerpt_id,
                version_kind=version_kind,
            )
            == identity
        )


@pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="postgres projection identity parity requires TEST_DATABASE_URL",
)
def test_postgres_import_projection_uses_captured_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RegistryService(os.environ["TEST_DATABASE_URL"])
    service.initialize()
    run_v2_backfill(service.database_url, resume=True)
    suffix = uuid4().hex
    locator = f"https://example.com/postgres-import-{suffix}"
    candidate = _candidate(locator, "web")
    monkeypatch.setattr(
        "research_registry.service.fetch_url_candidate",
        lambda _: candidate,
    )
    question = service.create_question(
        QuestionCreate(
            prompt=f"Postgres import projection {suffix}",
            focus=FocusTuple(domain="imports", object=suffix),
        )
    )

    result = service.import_url(
        ImportUrlRequest(url=locator, question_id=question.id)
    )
    identity = _assert_captured_linkage(
        service,
        source_id=result.source_ids[0],
        excerpt_id=result.excerpt_ids[0],
        version_kind="web",
    )

    run_v2_backfill(service.database_url, resume=True)
    assert (
        _assert_captured_linkage(
            service,
            source_id=result.source_ids[0],
            excerpt_id=result.excerpt_ids[0],
            version_kind="web",
        )
        == identity
    )
