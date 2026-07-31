from __future__ import annotations

from pathlib import Path
import socket

import pytest

from research_registry.application.refresh import (
    CapturePolicy,
    ResearchRefreshService,
    SourceCaptureCoordinator,
)
from research_registry.application.source_versions import SourceVersionService
from research_registry.domain.evidence import EvidenceAmbiguous, EvidenceUnresolved
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.ingestion.fetch_policy import FetchPolicy
from research_registry.ingestion.reanchor import reanchor_text
from research_registry.ingestion.web import HardenedWebFetcher, WebSourceIngestor
from tests.fixtures.v2_review import SUPPORTING_QUOTE, seed_review_registry


def test_unique_exact_prefix_suffix_match_is_deterministic() -> None:
    result = reanchor_text(
        "new header\nbefore exact quote after\nnew footer\n",
        exact="exact quote",
        prefix="before ",
        suffix=" after",
    )

    assert result.start == len("new header\nbefore ")
    assert result.end == result.start + len("exact quote")
    assert result.start_line == 2
    assert result.end_line == 2


def test_ambiguous_or_missing_text_never_asserts_survival() -> None:
    with pytest.raises(EvidenceAmbiguous):
        reanchor_text("exact quote\nexact quote", exact="exact quote")
    with pytest.raises(EvidenceUnresolved):
        reanchor_text("different text", exact="exact quote")


class _Response:
    status = 200

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.done = False

    def getheaders(self):
        return [("content-type", "text/plain")]

    def read(self, amount: int) -> bytes:
        if self.done:
            return b""
        self.done = True
        return self.body


class _Connection:
    def __init__(self, body: bytes) -> None:
        self.response = _Response(body)

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
        pass

    def getresponse(self):
        return self.response

    def close(self) -> None:
        pass


def test_capture_refresh_creates_new_evidence_and_queues_dependents_without_rewrite(
    tmp_path: Path,
) -> None:
    key = "capture-reanchor"
    registry, ids = seed_review_registry(tmp_path, key=key)
    with registry.connect() as conn:
        conn.execute(
            "UPDATE sources SET locator = ? WHERE id = ?",
            ("https://public.example/source", ids["source"]),
        )
        before_claim = dict(
            conn.execute(
                "SELECT * FROM claims WHERE id = ?",
                (ids["claim"],),
            ).fetchone()
        )
    new_text = f"new context before {SUPPORTING_QUOTE} after new context"
    blob_store = FilesystemBlobStore(tmp_path / f"{key}-blobs")
    web = WebSourceIngestor(
        HardenedWebFetcher(
            FetchPolicy(),
            resolver=lambda host, port: [(socket.AF_INET, "93.184.216.34")],
            connection_factory=lambda target, policy: _Connection(
                new_text.encode()
            ),
        ),
        SourceVersionService(registry.database, blob_store),
    )
    refresh = ResearchRefreshService(
        registry.database,
        capture_coordinator=SourceCaptureCoordinator(
            registry.database,
            CapturePolicy(
                enabled_modes=frozenset({"capture"}),
                max_snapshot_policy="extracted_text",
            ),
            web=web,
        ),
    )

    request = {
        "protocol": "research-refresh/v2",
        "mode": "capture",
        "idempotency_key": "capture-source-v2",
        "entities": [{"kind": "source", "id": ids["source"]}],
        "snapshot_policy": "extracted_text",
    }
    result = refresh.refresh(request)
    replay = refresh.refresh(request)

    assert result.status == "captured"
    assert replay.idempotent_replay is True
    assert replay.model_copy(update={"idempotent_replay": False}) == result
    reanchored = next(item for item in result.items if item.evidence_span_id)
    assert reanchored.anchor_state == "resolved"
    assert reanchored.previous_evidence_span_id == ids["supporting"]
    with registry.connect() as conn:
        old_evidence = conn.execute(
            "SELECT * FROM evidence_spans WHERE id = ?",
            (ids["supporting"],),
        ).fetchone()
        new_evidence = conn.execute(
            "SELECT * FROM evidence_spans WHERE id = ?",
            (reanchored.evidence_span_id,),
        ).fetchone()
        after_claim = dict(
            conn.execute(
                "SELECT * FROM claims WHERE id = ?",
                (ids["claim"],),
            ).fetchone()
        )
        queued = conn.execute(
            """
            SELECT entity_kind FROM refresh_queue
            WHERE status = 'pending' ORDER BY entity_kind
            """
        ).fetchall()
    assert old_evidence["anchor_state"] == "unverified"
    assert new_evidence["anchor_state"] == "resolved"
    assert before_claim == after_claim
    assert [row["entity_kind"] for row in queued] == ["claim", "report"]
