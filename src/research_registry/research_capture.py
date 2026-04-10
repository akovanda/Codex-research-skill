from __future__ import annotations

from datetime import datetime
import re

from pydantic import BaseModel, Field


RESEARCH_PATTERNS = (
    r"\bresearch\b",
    r"\binvestigate\b",
    r"\blook into\b",
    r"\bcompare\b",
    r"\bgather sources\b",
    r"\bfind sources\b",
    r"\bliterature review\b",
    r"\bsurvey\b",
    r"\bwhat does the literature say\b",
    r"\bevaluate\b.+\bsource",
)

MEMORY_KEYWORDS = (
    "memory",
    "rag",
    "retrieval",
    "rerank",
    "reranking",
    "context window",
    "context management",
    "provenance",
    "freshness",
    "index",
    "cross-session",
    "long-term",
    "episodic",
    "semantic memory",
)


def normalize_research_prompt(prompt: str) -> str:
    return " ".join(prompt.strip().lower().split())


def is_research_request(prompt: str) -> bool:
    normalized = normalize_research_prompt(prompt)
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in RESEARCH_PATTERNS)


def specialized_skill_for_prompt(prompt: str) -> str | None:
    normalized = normalize_research_prompt(prompt)
    if any(keyword in normalized for keyword in MEMORY_KEYWORDS):
        return "research-memory-retrieval"
    return None


class CaptureSummary(BaseModel):
    prompt: str
    reused_record_ids: list[str] = Field(default_factory=list)
    stored_run_id: str | None = None
    stored_annotation_ids: list[str] = Field(default_factory=list)
    stored_finding_ids: list[str] = Field(default_factory=list)
    stored_report_id: str | None = None
    queued_bundle_id: str | None = None
    pending_queue_count: int = 0
    created_at: datetime
    backend_name: str | None = None
    backend_url: str | None = None
    namespace_id: str | None = None


def format_capture_summary(summary: CaptureSummary) -> str:
    parts = [f"Research capture summary for: {summary.prompt}"]
    if summary.backend_name or summary.backend_url:
        backend_bits = [bit for bit in [summary.backend_name, summary.backend_url] if bit]
        line = f"Backend: {' | '.join(backend_bits)}"
        if summary.namespace_id:
            line += f" | namespace={summary.namespace_id}"
        parts.append(line)
    if summary.reused_record_ids:
        parts.append(f"Reused: {', '.join(summary.reused_record_ids)}")
    if summary.stored_run_id:
        parts.append(f"Stored run: {summary.stored_run_id}")
    if summary.stored_annotation_ids:
        parts.append(f"Stored annotations: {', '.join(summary.stored_annotation_ids)}")
    if summary.stored_finding_ids:
        parts.append(f"Stored findings: {', '.join(summary.stored_finding_ids)}")
    if summary.stored_report_id:
        parts.append(f"Stored report: {summary.stored_report_id}")
    if summary.queued_bundle_id:
        parts.append(f"Queued for retry: {summary.queued_bundle_id}")
    parts.append(f"Pending queue count: {summary.pending_queue_count}")
    return "\n".join(parts)
