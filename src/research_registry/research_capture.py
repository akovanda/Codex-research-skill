from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Protocol

from pydantic import BaseModel, Field

from .local_research import build_focus, run_local_research
from .models import (
    BackendStatus,
    ClaimCreate,
    ClaimRecord,
    ExcerptCreate,
    FocusTuple,
    QuestionCreate,
    QuestionRecord,
    ReportCreate,
    ReportRecord,
    ResearchSessionCreate,
    SearchResponse,
    SourceCreate,
)

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


class ResearchRegistryBackend(Protocol):
    def search(self, query: str, *, kind: str | None = None, include_private: bool = False, limit: int = 20) -> SearchResponse: ...
    def backend_status(self) -> BackendStatus: ...
    def get_question(self, question_id: str, include_private: bool = False) -> QuestionRecord: ...
    def get_claim(self, claim_id: str, include_private: bool = False) -> ClaimRecord: ...
    def get_report(self, report_id: str, include_private: bool = False) -> ReportRecord: ...
    def get_source(self, source_id: str, include_private: bool = False): ...
    def create_question(self, payload: QuestionCreate) -> QuestionRecord: ...
    def create_session(self, payload: ResearchSessionCreate): ...
    def create_source(self, payload: SourceCreate): ...
    def create_excerpt(self, payload: ExcerptCreate): ...
    def create_claim(self, payload: ClaimCreate): ...
    def create_report(self, payload: ReportCreate): ...
    def set_question_status(self, question_id: str, status: str) -> None: ...


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


class SummaryContractCheck(BaseModel):
    required_sections_present: bool
    mentions_claims: bool
    mentions_context: bool
    mentions_sources: bool
    mentions_registry_state: bool
    passed: bool


class CaptureSummary(BaseModel):
    prompt: str
    reused_record_ids: list[str] = Field(default_factory=list)
    stored_topic_id: str | None = None
    stored_question_id: str | None = None
    stored_session_id: str | None = None
    stored_source_ids: list[str] = Field(default_factory=list)
    stored_excerpt_ids: list[str] = Field(default_factory=list)
    stored_claim_ids: list[str] = Field(default_factory=list)
    stored_report_id: str | None = None
    created_at: datetime
    backend_name: str | None = None
    backend_url: str | None = None
    namespace_id: str | None = None


class ImplicitCaptureOutcome(BaseModel):
    specialized_domain: str | None = None
    specialized_skill: str | None = None
    specialist_mode: str | None = None
    capture_summary: CaptureSummary
    narrative_summary_md: str | None = None
    summary_contract_passed: bool | None = None


def run_implicit_research_capture(
    prompt: str,
    *,
    backend: ResearchRegistryBackend,
    source_signals: list[str] | None = None,
    source_roots: list | None = None,
    prefer_report: bool = True,
    model_name: str = "gpt-5.4",
    model_version: str = "2026-04-10",
) -> ImplicitCaptureOutcome:
    del prefer_report
    source_signals = source_signals or []
    backend_status = backend.backend_status()
    specialized_domain = specialized_domain_for_prompt(prompt)
    specialized_skill = specialized_skill_for_prompt(prompt)
    focus = build_focus(prompt, domain=specialized_domain, source_signals=source_signals)
    question = backend.create_question(
        QuestionCreate(
            prompt=prompt,
            focus=focus,
            dedupe_key=f"question:{normalize_research_prompt(prompt)}",
        )
    )

    reusable_reports, reusable_claims = search_existing_artifacts(backend, prompt=prompt, focus=focus)
    if has_specific_coverage(focus, reusable_reports, reusable_claims):
        session = backend.create_session(
            ResearchSessionCreate(
                question_id=question.id,
                prompt=prompt,
                model_name=model_name,
                model_version=model_version,
                mode="reuse",
                source_signals=source_signals,
                notes="implicit research capture reuse",
            )
        )
        summary_md, claim_points, context_points, source_refs = build_reuse_summary(
            prompt=prompt,
            focus=focus,
            backend=backend,
            reports=reusable_reports,
            claims=reusable_claims,
            question_id=question.id,
            session_id=session.id,
        )
        check = evaluate_summary_contract(summary_md, claims=claim_points, source_refs=source_refs, registry_ids=[question.id, session.id, *[report.id for report in reusable_reports], *[claim.id for claim in reusable_claims]])
        return ImplicitCaptureOutcome(
            specialized_domain=specialized_domain,
            specialized_skill=specialized_skill,
            specialist_mode="reuse",
            capture_summary=CaptureSummary(
                prompt=prompt,
                reused_record_ids=[report.id for report in reusable_reports] + [claim.id for claim in reusable_claims],
                stored_topic_id=question.topic_id,
                stored_question_id=question.id,
                stored_session_id=session.id,
                created_at=datetime.now(timezone.utc),
                backend_name=backend_status.name,
                backend_url=backend_status.url,
                namespace_id=backend_status.namespace_id,
            ),
            narrative_summary_md=summary_md,
            summary_contract_passed=check.passed,
        )

    live_result = run_local_research(prompt, domain=specialized_domain, source_signals=source_signals, source_roots=source_roots)
    if not live_result.hits:
        backend.set_question_status(question.id, "insufficient_evidence")
        session = backend.create_session(
            ResearchSessionCreate(
                question_id=question.id,
                prompt=prompt,
                model_name=model_name,
                model_version=model_version,
                mode="insufficient_evidence",
                source_signals=source_signals,
                notes="implicit research capture found no live local evidence",
            )
        )
        summary_md = build_insufficient_evidence_summary(
            prompt=prompt,
            focus=focus,
            source_roots=live_result.source_roots,
            question_id=question.id,
            session_id=session.id,
        )
        check = evaluate_summary_contract(summary_md, claims=[], source_refs=[], registry_ids=[question.id, session.id])
        return ImplicitCaptureOutcome(
            specialized_domain=specialized_domain,
            specialized_skill=specialized_skill,
            specialist_mode="insufficient_evidence",
            capture_summary=CaptureSummary(
                prompt=prompt,
                stored_topic_id=question.topic_id,
                stored_question_id=question.id,
                stored_session_id=session.id,
                created_at=datetime.now(timezone.utc),
                backend_name=backend_status.name,
                backend_url=backend_status.url,
                namespace_id=backend_status.namespace_id,
            ),
            narrative_summary_md=summary_md,
            summary_contract_passed=check.passed,
        )

    session = backend.create_session(
        ResearchSessionCreate(
            question_id=question.id,
            prompt=prompt,
            model_name=model_name,
            model_version=model_version,
            mode="live_research",
            source_signals=source_signals,
            notes="implicit research capture with live local evidence",
        )
    )

    created_sources: dict[str, str] = {}
    created_excerpt_ids: list[str] = []
    created_claim_ids: list[str] = []
    source_refs: list[str] = []
    for hit in live_result.hits:
        locator = hit.source.locator
        source_id = created_sources.get(locator)
        if source_id is None:
            created_source = backend.create_source(hit.source)
            created_sources[locator] = created_source.id
            source_id = created_source.id
            source_refs.append(created_source.locator)
        created_excerpt = backend.create_excerpt(
            ExcerptCreate(
                source_id=source_id,
                question_id=question.id,
                session_id=session.id,
                topic_id=question.topic_id,
                focal_label=live_result.focus.label or question.focus.label or "research",
                note=hit.note,
                selector=hit.selector,
                quote_text=hit.quote_text,
                confidence=min(0.95, max(0.55, hit.score / 10.0)),
                tags=hit.matched_terms[:4],
                model_name=model_name,
                model_version=model_version,
            )
        )
        created_excerpt_ids.append(created_excerpt.id)

    for draft in live_result.claim_drafts:
        excerpt_ids = [created_excerpt_ids[index] for index in draft.excerpt_indexes if index < len(created_excerpt_ids)]
        if not excerpt_ids:
            continue
        created_claim = backend.create_claim(
            ClaimCreate(
                question_id=question.id,
                session_id=session.id,
                topic_id=question.topic_id,
                title=draft.title,
                focal_label=live_result.focus.label or question.focus.label or "research",
                statement=draft.statement,
                excerpt_ids=excerpt_ids,
                status=draft.status,
                confidence=draft.confidence,
                model_name=model_name,
                model_version=model_version,
            )
        )
        created_claim_ids.append(created_claim.id)

    report = backend.create_report(
        ReportCreate(
            question_id=question.id,
            session_id=session.id,
            title=prompt,
            focal_label=live_result.focus.label or question.focus.label or "research",
            summary_md=live_result.report_md or f"# {prompt}\n",
            claim_ids=created_claim_ids,
            model_name=model_name,
            model_version=model_version,
        )
    )
    backend.set_question_status(question.id, "answered")
    claim_points = [draft.statement for draft in live_result.claim_drafts]
    context_points = build_context_points(live_result.focus, live_result.source_roots)
    summary_md = build_live_summary(
        prompt=prompt,
        focus=live_result.focus,
        report=backend.get_report(report.id, include_private=True),
        claim_points=claim_points,
        context_points=context_points,
        source_refs=source_refs,
        question_id=question.id,
        session_id=session.id,
    )
    check = evaluate_summary_contract(summary_md, claims=claim_points, source_refs=source_refs, registry_ids=[question.id, session.id, report.id, *created_claim_ids, *created_excerpt_ids, *created_sources.values()])
    return ImplicitCaptureOutcome(
        specialized_domain=specialized_domain,
        specialized_skill=specialized_skill,
        specialist_mode="live_research",
        capture_summary=CaptureSummary(
            prompt=prompt,
            stored_topic_id=question.topic_id,
            stored_question_id=question.id,
            stored_session_id=session.id,
            stored_source_ids=list(created_sources.values()),
            stored_excerpt_ids=created_excerpt_ids,
            stored_claim_ids=created_claim_ids,
            stored_report_id=report.id,
            created_at=datetime.now(timezone.utc),
            backend_name=backend_status.name,
            backend_url=backend_status.url,
            namespace_id=backend_status.namespace_id,
        ),
        narrative_summary_md=summary_md,
        summary_contract_passed=check.passed,
    )


def search_existing_artifacts(
    backend: ResearchRegistryBackend,
    *,
    prompt: str,
    focus: FocusTuple,
) -> tuple[list[ReportRecord], list[ClaimRecord]]:
    query_variants = [prompt, focus.label or "", focus.object or "", " ".join(part for part in [focus.object, focus.concern] if part)]
    report_ids: list[str] = []
    claim_ids: list[str] = []
    for query in query_variants:
        if not query.strip():
            continue
        report_hits = backend.search(query, kind="report", include_private=True, limit=3)
        claim_hits = backend.search(query, kind="claim", include_private=True, limit=4)
        for hit in report_hits.hits:
            if hit.id not in report_ids:
                report_ids.append(hit.id)
        for hit in claim_hits.hits:
            if hit.id not in claim_ids:
                claim_ids.append(hit.id)
    reports = [backend.get_report(report_id, include_private=True) for report_id in report_ids[:2]]
    claims = [backend.get_claim(claim_id, include_private=True) for claim_id in claim_ids[:4]]
    reports.sort(key=lambda report: relevance_score(focus, f"{report.title} {report.focal_label} {report.summary_md}"), reverse=True)
    claims.sort(key=lambda claim: relevance_score(focus, f"{claim.title} {claim.focal_label} {claim.statement}"), reverse=True)
    return reports, claims


def has_specific_coverage(focus: FocusTuple, reports: list[ReportRecord], claims: list[ClaimRecord]) -> bool:
    report_text = f"{reports[0].title} {reports[0].focal_label} {reports[0].summary_md}" if reports else ""
    claim_text = f"{claims[0].title} {claims[0].focal_label} {claims[0].statement}" if claims else ""
    report_score = relevance_score(focus, report_text) if reports and domain_matches(focus, reports[0].focal_label) else 0
    claim_score = relevance_score(focus, claim_text) if claims and domain_matches(focus, claims[0].focal_label) else 0
    report_object_score = object_match_score(focus, report_text) if report_text else 0
    claim_object_score = object_match_score(focus, claim_text) if claim_text else 0
    if reports and reports[0].focal_label == focus.label:
        return True
    if claims and claims[0].focal_label == focus.label:
        return True
    return max((report_score if report_object_score >= 2 else 0), (claim_score if claim_object_score >= 2 else 0)) >= 3


def relevance_score(focus: FocusTuple, text: str) -> int:
    normalized = normalize_research_prompt(text)
    tokens = focus_tokens(focus)
    return sum(1 for token in tokens if token in normalized)


def object_match_score(focus: FocusTuple, text: str) -> int:
    if not focus.object:
        return 0
    normalized = normalize_research_prompt(text)
    object_tokens = {token for token in re.findall(r"[a-z0-9_-]+", focus.object.lower()) if len(token) > 3}
    score = sum(1 for token in object_tokens if token in normalized)
    if focus.object.lower() in normalized:
        score += 1
    return score


def focus_tokens(focus: FocusTuple) -> set[str]:
    values = [focus.domain, focus.object, focus.concern, focus.context, focus.constraint, focus.label]
    tokens: set[str] = set()
    for value in values:
        if not value:
            continue
        tokens.update(token for token in re.findall(r"[a-z0-9_-]+", value.lower()) if len(token) > 3)
    return tokens


def domain_matches(focus: FocusTuple, focal_label: str) -> bool:
    if not focus.domain:
        return True
    return focus.domain.lower() in normalize_research_prompt(focal_label)


def build_reuse_summary(
    *,
    prompt: str,
    focus: FocusTuple,
    backend: ResearchRegistryBackend,
    reports: list[ReportRecord],
    claims: list[ClaimRecord],
    question_id: str,
    session_id: str,
) -> tuple[str, list[str], list[str], list[str]]:
    selected_report = reports[0] if reports else None
    selected_claims = claims[:3]
    if selected_report and not selected_claims:
        selected_claims = [backend.get_claim(claim_id, include_private=True) for claim_id in selected_report.claim_ids[:3]]
    claim_points = [claim.statement for claim in selected_claims]
    source_refs: list[str] = []
    if selected_report:
        for source_id in selected_report.source_ids:
            source_refs.append(backend.get_source(source_id, include_private=True).locator)
    context_points = build_context_points(focus, [])
    direct_answer = first_nonempty([first_non_heading_line(selected_report.summary_md) if selected_report else None, *claim_points, "Existing stored claims already cover this question closely enough to reuse."])
    lines = [
        f"# {prompt}",
        "",
        "## Direct Answer",
        direct_answer,
        "",
        "## Knowledge To Reuse",
    ]
    if claim_points:
        lines.extend(f"- {point}" for point in claim_points)
    else:
        lines.append("- No claim bullets were attached to the selected report.")
    lines.extend(["", "## Context To Carry Forward"])
    lines.extend(f"- {point}" for point in context_points)
    lines.extend(["", "## Evidence"])
    if source_refs:
        lines.extend(f"- {source_ref}" for source_ref in source_refs)
    else:
        lines.append("- Existing report sources were not expanded in this reuse pass.")
    lines.extend(
        [
            "",
            "## Registry State",
            f"- Question: {question_id}",
            f"- Session: {session_id}",
            f"- Reused reports: {', '.join(report.id for report in reports) or 'none'}",
            f"- Reused claims: {', '.join(claim.id for claim in selected_claims) or 'none'}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n", claim_points, context_points, source_refs


def build_live_summary(
    *,
    prompt: str,
    focus: FocusTuple,
    report: ReportRecord,
    claim_points: list[str],
    context_points: list[str],
    source_refs: list[str],
    question_id: str,
    session_id: str,
) -> str:
    direct_answer = first_non_heading_line(report.summary_md) or first_nonempty(claim_points)
    lines = [
        f"# {prompt}",
        "",
        "## Direct Answer",
        direct_answer,
        "",
        "## Knowledge To Reuse",
    ]
    lines.extend(f"- {point}" for point in claim_points)
    lines.extend(["", "## Context To Carry Forward"])
    lines.extend(f"- {point}" for point in context_points)
    lines.extend(["", "## Evidence"])
    lines.extend(f"- {source_ref}" for source_ref in source_refs)
    lines.extend(
        [
            "",
            "## Registry State",
            f"- Question: {question_id}",
            f"- Session: {session_id}",
            f"- Report: {report.id}",
            f"- Claims: {', '.join(report.claim_ids)}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def build_insufficient_evidence_summary(
    *,
    prompt: str,
    focus: FocusTuple,
    source_roots: list[str],
    question_id: str,
    session_id: str,
) -> str:
    context_points = build_context_points(focus, source_roots)
    lines = [
        f"# {prompt}",
        "",
        "## Direct Answer",
        "No live local evidence matched this question closely enough to justify storing claims or a report.",
        "",
        "## Knowledge To Reuse",
        "- No source-backed claims were created in this pass.",
        "",
        "## Context To Carry Forward",
    ]
    lines.extend(f"- {point}" for point in context_points)
    lines.extend(
        [
            "",
            "## Evidence",
            "- No qualifying evidence excerpts were found in the selected source roots.",
            "",
            "## Registry State",
            f"- Question: {question_id}",
            f"- Session: {session_id}",
            "- Stored report: none",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def build_context_points(focus: FocusTuple, source_roots: list[str]) -> list[str]:
    points = [f"Focus label: {focus.label}"]
    if focus.context:
        points.append(f"Context: {focus.context}")
    if focus.constraint:
        points.append(f"Constraint: {focus.constraint}")
    if source_roots:
        points.append(f"Live source roots: {', '.join(source_roots[:4])}")
    return points


def evaluate_summary_contract(summary_md: str, *, claims: list[str], source_refs: list[str], registry_ids: list[str]) -> SummaryContractCheck:
    required_sections_present = all(section in summary_md for section in ("## Knowledge To Reuse", "## Context To Carry Forward", "## Evidence", "## Registry State"))
    mentions_claims = bool(claims) and all(claim[:20].lower() in summary_md.lower() for claim in claims[:2]) if claims else "no source-backed claims" in summary_md.lower()
    mentions_context = "Focus label:" in summary_md or "Context:" in summary_md
    mentions_sources = bool(source_refs) and any(source_ref in summary_md for source_ref in source_refs[:2]) if source_refs else "No qualifying evidence excerpts" in summary_md or "Existing report sources" in summary_md
    mentions_registry_state = any(registry_id in summary_md for registry_id in registry_ids[:4])
    return SummaryContractCheck(
        required_sections_present=required_sections_present,
        mentions_claims=mentions_claims,
        mentions_context=mentions_context,
        mentions_sources=mentions_sources,
        mentions_registry_state=mentions_registry_state,
        passed=all((required_sections_present, mentions_claims, mentions_context, mentions_sources, mentions_registry_state)),
    )


def format_capture_summary(summary: CaptureSummary) -> str:
    parts = [f"Backend: {summary.backend_name or 'unknown'}"]
    if summary.backend_url:
        parts.append(f"URL: {summary.backend_url}")
    if summary.namespace_id:
        parts.append(f"Namespace: {summary.namespace_id}")
    if summary.reused_record_ids:
        parts.append(f"Reused: {', '.join(summary.reused_record_ids)}")
    if summary.stored_question_id:
        parts.append(f"Stored question: {summary.stored_question_id}")
    if summary.stored_session_id:
        parts.append(f"Stored session: {summary.stored_session_id}")
    if summary.stored_claim_ids:
        parts.append(f"Stored claims: {', '.join(summary.stored_claim_ids)}")
    if summary.stored_report_id:
        parts.append(f"Stored report: {summary.stored_report_id}")
    return " | ".join(parts)


def first_non_heading_line(markdown: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return markdown.strip().splitlines()[0] if markdown.strip() else ""


def first_nonempty(values: list[str | None]) -> str:
    for value in values:
        if value and value.strip():
            return value
    return ""
