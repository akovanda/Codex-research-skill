from __future__ import annotations

from datetime import datetime, timezone
import re

from pydantic import BaseModel, Field

from .backend_client import RegistryBackend
from .capture_queue import CaptureQueue, QueuedAnnotation, QueuedCaptureBundle, QueuedFinding, QueuedReport
from .memory_retrieval_skill import GapFillBundle, MemoryRetrievalSkillHarness
from .specialist_domains import build_domain_harness
from .models import AnnotationCreate, FindingCreate, ReportCreate, RunCreate


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

INFERENCE_OPTIMIZATION_KEYWORDS = (
    "inference",
    "latency",
    "throughput",
    "serving",
    "batching",
    "quantization",
    "speculative decoding",
    "prefix caching",
    "kv cache",
    "ttft",
)

LLM_EVALS_KEYWORDS = (
    "eval",
    "evaluation",
    "benchmark",
    "judge model",
    "rubric",
    "human label",
    "calibration",
    "drift",
    "offline eval",
    "online eval",
    "ground truth",
)


def normalize_research_prompt(prompt: str) -> str:
    return " ".join(prompt.strip().lower().split())


def keyword_matches_prompt(normalized_prompt: str, keyword: str) -> bool:
    if " " in keyword or "-" in keyword or len(keyword) <= 4:
        pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
        return re.search(pattern, normalized_prompt) is not None
    pattern = rf"\b{re.escape(keyword)}[a-z0-9]*\b"
    return re.search(pattern, normalized_prompt) is not None


def prompt_matches_any_keyword(normalized_prompt: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword_matches_prompt(normalized_prompt, keyword) for keyword in keywords)


def is_research_request(prompt: str) -> bool:
    normalized = normalize_research_prompt(prompt)
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in RESEARCH_PATTERNS)


def specialized_skill_for_prompt(prompt: str) -> str | None:
    domain = specialized_domain_for_prompt(prompt)
    if domain == "memory-retrieval":
        return "research-memory-retrieval"
    return None


def specialized_domain_for_prompt(prompt: str) -> str | None:
    normalized = normalize_research_prompt(prompt)
    if prompt_matches_any_keyword(normalized, MEMORY_KEYWORDS):
        return "memory-retrieval"
    if prompt_matches_any_keyword(normalized, INFERENCE_OPTIMIZATION_KEYWORDS):
        return "inference-optimization"
    if prompt_matches_any_keyword(normalized, LLM_EVALS_KEYWORDS):
        return "llm-evals"
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
    flushed_queue_ids: list[str] = Field(default_factory=list)


class ImplicitCaptureOutcome(BaseModel):
    specialized_domain: str | None = None
    specialized_skill: str | None = None
    specialist_mode: str | None = None
    capture_summary: CaptureSummary
    narrative_summary_md: str | None = None
    summary_contract_passed: bool | None = None


class RegistryBackendToolAdapter:
    def __init__(self, backend: RegistryBackend):
        self.backend = backend

    def search(self, query: str, kind: str | None = None, include_private: bool = True, limit: int = 10) -> dict:
        return self.backend.search(query, kind=kind, include_private=include_private, limit=limit).model_dump(mode="json")

    def create_run(self, payload: dict) -> dict:
        return self.backend.create_run(RunCreate.model_validate(payload)).model_dump(mode="json")

    def get_source(self, source_id: str, include_private: bool = True) -> dict:
        return self.backend.get_source(source_id, include_private=include_private).model_dump(mode="json")

    def get_annotation(self, annotation_id: str, include_private: bool = True) -> dict:
        return self.backend.get_annotation(annotation_id, include_private=include_private).model_dump(mode="json")

    def get_finding(self, finding_id: str, include_private: bool = True) -> dict:
        return self.backend.get_finding(finding_id, include_private=include_private).model_dump(mode="json")

    def get_report(self, report_id: str, include_private: bool = True) -> dict:
        return self.backend.get_report(report_id, include_private=include_private).model_dump(mode="json")

    def add_annotation(self, payload: dict) -> dict:
        return self.backend.create_annotation(AnnotationCreate.model_validate(payload)).model_dump(mode="json")

    def create_finding(self, payload: dict) -> dict:
        return self.backend.create_finding(FindingCreate.model_validate(payload)).model_dump(mode="json")

    def create_report(self, payload: dict) -> dict:
        return self.backend.create_report(ReportCreate.model_validate(payload)).model_dump(mode="json")


def run_implicit_research_capture(
    prompt: str,
    *,
    backend: RegistryBackend,
    queue: CaptureQueue | None = None,
    gap_fill: GapFillBundle | None = None,
    prefer_report: bool = True,
    model_name: str = "gpt-5.4",
    model_version: str = "2026-04-10",
) -> ImplicitCaptureOutcome:
    backend_status = backend.backend_status()
    flushed_queue_ids: list[str] = []
    if queue is not None:
        flush_result = queue.flush(backend)
        flushed_queue_ids = flush_result.flushed_queue_ids
        pending_queue_count = len(queue.list_pending())
    else:
        pending_queue_count = 0

    specialized_domain = specialized_domain_for_prompt(prompt)
    specialized_skill = specialized_skill_for_prompt(prompt)
    if specialized_domain is not None:
        if specialized_domain == "memory-retrieval":
            harness = MemoryRetrievalSkillHarness(
                RegistryBackendToolAdapter(backend),
                model_name=model_name,
                model_version=model_version,
            )
        else:
            harness = build_domain_harness(
                specialized_domain,
                RegistryBackendToolAdapter(backend),
                model_name=model_name,
                model_version=model_version,
            )
        try:
            specialist_result = harness.research(prompt, gap_fill=gap_fill, prefer_report=prefer_report)
        except Exception:
            if queue is None or gap_fill is None:
                raise
            queued_bundle = queue_bundle_from_gap_fill(
                prompt,
                gap_fill=gap_fill,
                backend=backend_status,
                model_name=model_name,
                model_version=model_version,
            )
            queue.enqueue(queued_bundle)
            pending_queue_count = len(queue.list_pending())
            return ImplicitCaptureOutcome(
                specialized_domain=specialized_domain,
                specialized_skill=specialized_skill,
                specialist_mode="gap_fill",
                capture_summary=CaptureSummary(
                    prompt=prompt,
                    queued_bundle_id=queued_bundle.queue_id,
                    pending_queue_count=pending_queue_count,
                    created_at=datetime.now(timezone.utc),
                    backend_name=backend_status.name,
                    backend_url=backend_status.url,
                    namespace_id=backend_status.namespace_id,
                    flushed_queue_ids=flushed_queue_ids,
                ),
                narrative_summary_md=None,
                summary_contract_passed=None,
            )

        capture_summary = CaptureSummary(
            prompt=prompt,
            reused_record_ids=[
                *specialist_result.reused_report_ids,
                *specialist_result.reused_finding_ids,
            ],
            stored_run_id=specialist_result.created_run_id,
            stored_annotation_ids=specialist_result.created_annotation_ids,
            stored_finding_ids=specialist_result.created_finding_ids,
            stored_report_id=specialist_result.created_report_id,
            pending_queue_count=pending_queue_count,
            created_at=datetime.now(timezone.utc),
            backend_name=backend_status.name,
            backend_url=backend_status.url,
            namespace_id=backend_status.namespace_id,
            flushed_queue_ids=flushed_queue_ids,
        )
        return ImplicitCaptureOutcome(
            specialized_domain=specialized_domain,
            specialized_skill=specialized_skill,
            specialist_mode=specialist_result.mode,
            capture_summary=capture_summary,
            narrative_summary_md=specialist_result.summary_md,
            summary_contract_passed=specialist_result.summary_check.passed,
        )

    search_response = backend.search(prompt, include_private=True, limit=5)
    capture_summary = CaptureSummary(
        prompt=prompt,
        reused_record_ids=[hit.id for hit in search_response.hits],
        pending_queue_count=pending_queue_count,
        created_at=datetime.now(timezone.utc),
        backend_name=backend_status.name,
        backend_url=backend_status.url,
        namespace_id=backend_status.namespace_id,
        flushed_queue_ids=flushed_queue_ids,
    )
    return ImplicitCaptureOutcome(capture_summary=capture_summary)


def queue_bundle_from_gap_fill(
    prompt: str,
    *,
    gap_fill: GapFillBundle,
    backend,
    model_name: str,
    model_version: str,
) -> QueuedCaptureBundle:
    question = prompt if prompt.endswith("?") else f"{prompt}?"
    annotations: list[QueuedAnnotation] = []
    for index, annotation in enumerate(gap_fill.annotations):
        annotations.append(
            QueuedAnnotation(
                temp_id=f"ann_{index + 1}",
                source=annotation.source,
                subject=annotation.subject,
                note=annotation.note,
                selector=annotation.selector,
                quote_text=annotation.quote_text,
                confidence=annotation.confidence,
                model_name=model_name,
                model_version=model_version,
                tags=annotation.tags,
            )
        )
    findings: list[QueuedFinding] = []
    for index, finding in enumerate(gap_fill.findings):
        findings.append(
            QueuedFinding(
                temp_id=f"fdg_{index + 1}",
                title=finding.title,
                subject=finding.subject,
                claim=finding.claim,
                annotation_temp_ids=[f"ann_{item + 1}" for item in finding.annotation_indexes],
                confidence=finding.confidence,
                model_name=model_name,
                model_version=model_version,
            )
        )
    report = QueuedReport(
        question=question,
        subject=gap_fill.subject,
        summary_md=f"# {question}\n\nQueued specialist gap-fill capture for replay.",
        finding_temp_ids=[finding.temp_id for finding in findings],
        model_name=model_name,
        model_version=model_version,
    )
    return QueuedCaptureBundle.create(
        prompt=prompt,
        normalized_topic=normalize_research_prompt(prompt),
        model_name=model_name,
        model_version=model_version,
        run=RunCreate(
            question=question,
            model_name=model_name,
            model_version=model_version,
            notes="queued implicit specialist gap-fill",
            namespace_kind=backend.namespace_kind,
            namespace_id=backend.namespace_id,
        ),
        annotations=annotations,
        findings=findings,
        report=report,
        backend_status=backend,
    )


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
    if summary.flushed_queue_ids:
        parts.append(f"Flushed queue items: {', '.join(summary.flushed_queue_ids)}")
    parts.append(f"Pending queue count: {summary.pending_queue_count}")
    return "\n".join(parts)
