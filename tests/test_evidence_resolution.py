from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from research_registry.application.source_versions import SourceVersionService
from research_registry.domain.evidence import (
    EvidenceAmbiguous,
    EvidenceDocument,
    EvidenceHashMismatch,
    EvidenceUnresolved,
    InvalidSelector,
    SourceVersionProvenance,
    resolve_exact_evidence,
)
from research_registry.domain.sources import SourceVersionSpec
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.service import RegistryService


def test_text_quote_requires_one_exact_contextual_match() -> None:
    document = "before exact quote after; before exact quote other"
    selector = {
        "type": "text_quote",
        "exact": "exact quote",
        "prefix": "before ",
        "suffix": " after",
    }

    resolution = resolve_exact_evidence(document, selector, "exact quote")

    assert document[resolution.start : resolution.end] == "exact quote"
    assert resolution.selector_type == "text_quote"
    with pytest.raises(EvidenceAmbiguous):
        resolve_exact_evidence(
            "exact quote then exact quote",
            {"type": "text_quote", "exact": "exact quote"},
            "exact quote",
        )
    with pytest.raises(EvidenceUnresolved):
        resolve_exact_evidence(
            document,
            {"type": "text_quote", "exact": "missing"},
            "missing",
        )


def test_closed_selector_validation_and_quote_hash_fail_closed() -> None:
    with pytest.raises(InvalidSelector, match="selector"):
        resolve_exact_evidence(
            "exact",
            {"type": "text_quote", "exact": "exact", "unknown": "rejected"},
            "exact",
        )
    with pytest.raises(InvalidSelector):
        resolve_exact_evidence(
            "exact",
            {"type": "char_range", "start": 0, "end": 5, "exact": "exact", "path": "../escape"},
            "exact",
        )
    with pytest.raises(EvidenceHashMismatch):
        resolve_exact_evidence(
            "exact",
            {"type": "text_quote", "exact": "exact"},
            "exact",
            quote_sha256="0" * 64,
        )
    with pytest.raises(InvalidSelector):
        resolve_exact_evidence(
            EvidenceDocument(json_value={"~2": "exact"}),
            {"type": "json_pointer", "pointer": "/~2", "exact": "exact"},
            "exact",
        )


def test_character_line_and_git_ranges_resolve_exactly() -> None:
    text = "zero\none exact\nthree\n"
    character = resolve_exact_evidence(
        text,
        {"type": "char_range", "start": 9, "end": 14, "exact": "exact"},
        "exact",
    )
    lines = resolve_exact_evidence(
        text,
        {
            "type": "line_range",
            "start_line": 2,
            "end_line": 2,
            "exact": "one exact",
        },
        "one exact",
    )
    provenance = SourceVersionProvenance(
        path="src/example.py",
        commit_sha="a" * 40,
        blob_sha="b" * 40,
    )
    git = resolve_exact_evidence(
        text,
        {
            "type": "git_line_range",
            "path": provenance.path,
            "commit_sha": provenance.commit_sha,
            "blob_sha": provenance.blob_sha,
            "start_line": 2,
            "end_line": 2,
            "exact": "one exact",
        },
        "one exact",
        provenance=provenance,
    )

    assert text[character.start : character.end] == "exact"
    assert text[lines.start : lines.end] == "one exact"
    assert text[git.start : git.end] == "one exact"
    with pytest.raises(EvidenceUnresolved, match="provenance"):
        resolve_exact_evidence(
            text,
            {
                "type": "git_line_range",
                "path": "src/other.py",
                "commit_sha": provenance.commit_sha,
                "blob_sha": provenance.blob_sha,
                "start_line": 2,
                "end_line": 2,
                "exact": "one exact",
            },
            "one exact",
            provenance=provenance,
        )


def test_json_pointer_page_and_dom_selectors_are_exact() -> None:
    pointer = resolve_exact_evidence(
        EvidenceDocument(json_value={"results": [{"value": 42}]}),
        {"type": "json_pointer", "pointer": "/results/0/value", "exact": "42"},
        "42",
    )
    page = resolve_exact_evidence(
        EvidenceDocument(pages=("first page", "second exact page")),
        {
            "type": "page_range",
            "start_page": 2,
            "end_page": 2,
            "exact": "exact",
        },
        "exact",
    )
    dom = resolve_exact_evidence(
        EvidenceDocument(dom_text={"main > p": "unique DOM exact"}),
        {
            "type": "dom_text",
            "css_selector": "main > p",
            "exact": "DOM exact",
        },
        "DOM exact",
    )

    assert pointer.pointer == "/results/0/value"
    assert page.start_page == 2
    assert dom.selector_type == "dom_text"


def test_resolution_reads_and_verifies_the_exact_source_version_blob(
    tmp_path: Path,
) -> None:
    registry = RegistryService(tmp_path / "registry.sqlite3")
    registry.initialize()
    with registry.connect() as conn:
        conn.execute(
            """
            INSERT INTO sources (
                id, locator, title, source_type, visibility, created_at
            ) VALUES ('src_resolution', 'note:resolution', 'Resolution', 'note',
                      'private', '2026-07-30T00:00:00+00:00')
            """
        )
    store = FilesystemBlobStore(tmp_path / "blobs")
    service = SourceVersionService(registry.database, store)
    content = b"before exact persisted evidence after"
    created = service.create_or_reuse(
        SourceVersionSpec(
            source_id="src_resolution",
            version_key=None,
            version_kind="note",
            retrieved_at="2026-07-30T00:00:00+00:00",
            content_sha256=sha256(content).hexdigest(),
            canonical_locator="note:resolution",
            snapshot_policy="extracted_text",
            snapshot_bytes=content,
            media_type="text/plain",
            byte_count=len(content),
        )
    )

    resolution = service.resolve_evidence(
        created.record.id,
        {
            "type": "text_quote",
            "exact": "exact persisted evidence",
            "prefix": "before ",
            "suffix": " after",
        },
        "exact persisted evidence",
    )

    assert resolution.start == len("before ")
    assert resolution.end == len("before exact persisted evidence")

    quote = "exact persisted evidence"
    with registry.connect() as conn:
        conn.execute(
            """
            INSERT INTO evidence_spans (
                id, source_version_id, quote_text, quote_sha256,
                selector_type, selector_json, confidence, anchor_state,
                review_state, trust_tier, created_at
            ) VALUES (?, ?, ?, ?, 'text_quote', ?, 1.0, 'resolved',
                      'unreviewed', 'medium', ?)
            """,
            (
                "evd_resolution",
                created.record.id,
                quote,
                sha256(quote.encode("utf-8")).hexdigest(),
                json.dumps(
                    {"type": "text_quote", "exact": quote},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                "2026-07-30T00:00:00+00:00",
            ),
        )
    with registry.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE evidence_spans SET note = 'mutation' WHERE id = ?",
                ("evd_resolution",),
            )
