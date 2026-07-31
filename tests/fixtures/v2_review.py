from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from research_registry.application.deposit import ResearchDepositService
from research_registry.ingestion.blobs import FilesystemBlobStore
from research_registry.service import RegistryService


CONTENT = (
    "Review transitions preserve immutable claim text. "
    "A later observation can contradict the earlier conclusion."
)
SUPPORTING_QUOTE = "Review transitions preserve immutable claim text."
REFUTING_QUOTE = "A later observation can contradict the earlier conclusion."
STATEMENT = "Review transitions preserve earlier claim revisions."


def seed_review_registry(
    tmp_path: Path,
    *,
    key: str = "review-seed",
    status: str = "supported",
    include_refuting_evidence: bool = False,
    database: str | Path | None = None,
) -> tuple[RegistryService, dict[str, str]]:
    registry = RegistryService(database or (tmp_path / f"{key}.sqlite3"))
    registry.initialize()
    content = f"{CONTENT}\nSeed: {key}"
    evidence: list[dict[str, Any]] = [
        {
            "client_ref": "supporting",
            "source_version": {"ref": "source"},
            "quote_text": SUPPORTING_QUOTE,
            "selector": {
                "type": "text_quote",
                "exact": SUPPORTING_QUOTE,
            },
        }
    ]
    links: list[dict[str, Any]] = [
        {
            "evidence": {"ref": "supporting"},
            "relationship": "supports",
        }
    ]
    if include_refuting_evidence:
        evidence.append(
            {
                "client_ref": "refuting",
                "source_version": {"ref": "source"},
                "quote_text": REFUTING_QUOTE,
                "selector": {
                    "type": "text_quote",
                    "exact": REFUTING_QUOTE,
                },
            }
        )
        links.append(
            {
                "evidence": {"ref": "refuting"},
                "relationship": "refutes",
            }
        )

    receipt = ResearchDepositService(
        registry.database,
        FilesystemBlobStore(tmp_path / f"{key}-blobs"),
    ).deposit(
        {
            "protocol": "research-deposit/v2",
            "idempotency_key": key,
            "inquiry": {
                "client_ref": "question",
                "prompt": "How are claim reviews recorded?",
                "topic_label": "Claim review history",
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
                        "locator": f"note:{key}",
                        "title": "Review transition evidence",
                        "source_type": "note",
                    },
                    "version": {
                        "version_key": f"note:{key}:v1",
                        "version_kind": "note",
                        "retrieved_at": "2026-07-30T00:00:00Z",
                        "content_sha256": sha256(content.encode()).hexdigest(),
                        "canonical_locator": f"note:{key}",
                        "snapshot": {
                            "policy": "extracted_text",
                            "text": content,
                            "media_type": "text/plain",
                            "byte_count": len(content.encode()),
                        },
                    },
                }
            ],
            "evidence": evidence,
            "claims": [
                {
                    "client_ref": "claim",
                    "title": "Claim reviews preserve history",
                    "statement": STATEMENT,
                    "status": status,
                    "confidence": 0.9,
                    "evidence": links,
                }
            ],
            "report": {
                "client_ref": "report",
                "title": "Review workflow report",
                "summary_md": "Claim reviews are append-only.",
                "claims": [{"ref": "claim"}],
            },
        }
    )
    ids = {
        "source": receipt.records.source_ids["source"],
        "source_version": receipt.records.source_version_ids["source"],
        "supporting": receipt.records.evidence_ids["supporting"],
        "claim": receipt.records.claim_ids["claim"],
        "revision": receipt.records.claim_revision_ids["claim"],
        "report": receipt.records.report_id or "",
    }
    if include_refuting_evidence:
        ids["refuting"] = receipt.records.evidence_ids["refuting"]
    with registry.connect() as conn:
        ids["excerpt"] = conn.execute(
            """
            SELECT legacy_id
            FROM legacy_projection_identity
            WHERE legacy_kind = 'excerpt'
              AND v2_kind = 'evidence'
              AND v2_id = ?
            """,
            (ids["supporting"],),
        ).fetchone()["legacy_id"]
    return registry, ids
