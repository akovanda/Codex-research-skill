from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from .models import SourceCreate, SourceSelector


class MemoryRetrievalToolSurface(Protocol):
    def search(self, query: str, kind: str | None = None, include_private: bool = True, limit: int = 10) -> dict: ...
    def create_run(self, payload: dict) -> dict: ...
    def get_source(self, source_id: str, include_private: bool = True) -> dict: ...
    def get_annotation(self, annotation_id: str, include_private: bool = True) -> dict: ...
    def get_finding(self, finding_id: str, include_private: bool = True) -> dict: ...
    def get_report(self, report_id: str, include_private: bool = True) -> dict: ...
    def add_annotation(self, payload: dict) -> dict: ...
    def create_finding(self, payload: dict) -> dict: ...
    def create_report(self, payload: dict) -> dict: ...


class SkillEvidenceAnnotation(BaseModel):
    source: SourceCreate
    subject: str
    note: str
    selector: SourceSelector
    quote_text: str | None = None
    confidence: float = 0.75
    tags: list[str] = Field(default_factory=list)


class SkillEvidenceFinding(BaseModel):
    title: str
    subject: str
    claim: str
    annotation_indexes: list[int] = Field(min_length=1)
    confidence: float | None = None


class GapFillBundle(BaseModel):
    subject: str
    annotations: list[SkillEvidenceAnnotation] = Field(default_factory=list)
    findings: list[SkillEvidenceFinding] = Field(default_factory=list)
    create_report: bool = True


class SummaryContractCheck(BaseModel):
    required_sections_present: bool
    mentions_claims: bool
    mentions_context: bool
    mentions_sources: bool
    mentions_registry_state: bool
    passed: bool


class MemoryRetrievalSkillResult(BaseModel):
    mode: Literal["reuse", "synthesis", "gap_fill"]
    prompt: str
    question: str
    query_variants: list[str]
    reused_report_ids: list[str] = Field(default_factory=list)
    reused_finding_ids: list[str] = Field(default_factory=list)
    created_run_id: str | None = None
    created_annotation_ids: list[str] = Field(default_factory=list)
    created_finding_ids: list[str] = Field(default_factory=list)
    created_report_id: str | None = None
    summary_md: str
    knowledge_points: list[str] = Field(default_factory=list)
    context_points: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    summary_check: SummaryContractCheck


class MemoryRetrievalSkillHarness:
    def __init__(
        self,
        tools: MemoryRetrievalToolSurface,
        *,
        model_name: str = "gpt-5.4",
        model_version: str = "2026-04-10",
    ):
        self.tools = tools
        self.model_name = model_name
        self.model_version = model_version
        self.taxonomy = load_topic_taxonomy()

    def research(
        self,
        prompt: str,
        *,
        gap_fill: GapFillBundle | None = None,
        prefer_report: bool = True,
    ) -> MemoryRetrievalSkillResult:
        question = normalize_question(prompt)
        query_variants = expand_query_variants(prompt, self.taxonomy)
        relevant_reports, relevant_findings = self._search_relevant_artifacts(query_variants)

        if gap_fill is not None:
            if self._has_specific_coverage(prompt, relevant_reports, relevant_findings):
                return self._create_reuse_result(prompt, question, query_variants, relevant_reports, relevant_findings)
            return self._create_gap_fill_result(prompt, question, query_variants, gap_fill)

        if prefer_report and should_create_synthesis_report(prompt, relevant_reports, relevant_findings):
            return self._create_synthesis_result(prompt, question, query_variants, relevant_reports, relevant_findings)

        return self._create_reuse_result(prompt, question, query_variants, relevant_reports, relevant_findings)

    def _search_relevant_artifacts(self, query_variants: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        reports = self._collect_hits(query_variants, kind="report")
        findings = self._collect_hits(query_variants, kind="finding")
        return reports, findings

    def _collect_hits(self, query_variants: list[str], *, kind: str) -> list[dict[str, Any]]:
        deduped: dict[str, dict[str, Any]] = {}
        for variant in query_variants:
            response = self.tools.search(variant, kind=kind, include_private=True, limit=5)
            for hit in response["hits"]:
                existing = deduped.get(hit["id"])
                if existing is None or hit["score"] > existing["score"]:
                    deduped[hit["id"]] = hit
        return sorted(deduped.values(), key=lambda hit: (hit["score"], hit["created_at"]), reverse=True)

    def _has_specific_coverage(
        self,
        prompt: str,
        reports: list[dict[str, Any]],
        findings: list[dict[str, Any]],
    ) -> bool:
        focus_tokens = extract_focus_tokens(prompt)
        if not focus_tokens:
            return bool(reports or findings)
        relevant_hits = [*reports, *findings]
        if not relevant_hits:
            return False
        best_overlap = max(token_overlap(focus_tokens, hit) for hit in relevant_hits)
        target_overlap = 2 if len(focus_tokens) >= 3 else 1
        return best_overlap >= target_overlap

    def _create_reuse_result(
        self,
        prompt: str,
        question: str,
        query_variants: list[str],
        reports: list[dict[str, Any]],
        findings: list[dict[str, Any]],
    ) -> MemoryRetrievalSkillResult:
        focus_tokens = extract_focus_tokens(prompt)
        top_report_overlap = token_overlap(focus_tokens, reports[0]) if focus_tokens and reports else 0
        top_finding_overlap = token_overlap(focus_tokens, findings[0]) if focus_tokens and findings else 0

        selected_report_ids = [hit["id"] for hit in reports[:1]] if reports and top_report_overlap >= top_finding_overlap and top_report_overlap > 0 else []
        if selected_report_ids:
            selected_finding_ids = self.tools.get_report(selected_report_ids[0], include_private=True)["finding_ids"]
        else:
            specific_findings = [hit["id"] for hit in findings if token_overlap(focus_tokens, hit) > 0] if focus_tokens else []
            selected_finding_ids = specific_findings[:2] or [hit["id"] for hit in findings[:2]]
        summary_md, knowledge_points, context_points, source_urls = self._build_summary(
            question=question,
            finding_ids=selected_finding_ids,
            report_ids=selected_report_ids,
            created_report_id=None,
            created_run_id=None,
            created_annotation_ids=[],
            created_finding_ids=[],
        )
        result = MemoryRetrievalSkillResult(
            mode="reuse",
            prompt=prompt,
            question=question,
            query_variants=query_variants,
            reused_report_ids=selected_report_ids,
            reused_finding_ids=selected_finding_ids,
            summary_md=summary_md,
            knowledge_points=knowledge_points,
            context_points=context_points,
            source_urls=source_urls,
            summary_check=evaluate_summary_contract(
                summary_md,
                claims=knowledge_points,
                source_urls=source_urls,
                registry_ids=[*selected_report_ids, *selected_finding_ids],
            ),
        )
        return result

    def _create_synthesis_result(
        self,
        prompt: str,
        question: str,
        query_variants: list[str],
        reports: list[dict[str, Any]],
        findings: list[dict[str, Any]],
    ) -> MemoryRetrievalSkillResult:
        selected_finding_ids = [hit["id"] for hit in findings[:2]]
        reused_report_ids = [hit["id"] for hit in reports[:1]]
        summary_md, knowledge_points, context_points, source_urls = self._build_summary(
            question=question,
            finding_ids=selected_finding_ids,
            report_ids=reused_report_ids,
            created_report_id=None,
            created_run_id=None,
            created_annotation_ids=[],
            created_finding_ids=[],
        )
        report = self.tools.create_report(
            {
                "question": question,
                "subject": derive_subject(prompt),
                "summary_md": summary_md,
                "finding_ids": selected_finding_ids,
                "model_name": self.model_name,
                "model_version": self.model_version,
                "dedupe_key": f"synthesis:{derive_subject(prompt)}:{':'.join(selected_finding_ids)}",
            }
        )
        created_report_id = None if report["id"] in reused_report_ids else report["id"]
        final_summary_md, knowledge_points, context_points, source_urls = self._build_summary(
            question=question,
            finding_ids=selected_finding_ids,
            report_ids=[report["id"]],
            created_report_id=created_report_id,
            created_run_id=None,
            created_annotation_ids=[],
            created_finding_ids=[],
        )
        return MemoryRetrievalSkillResult(
            mode="synthesis",
            prompt=prompt,
            question=question,
            query_variants=query_variants,
            reused_report_ids=reused_report_ids,
            reused_finding_ids=selected_finding_ids,
            created_report_id=created_report_id,
            summary_md=final_summary_md,
            knowledge_points=knowledge_points,
            context_points=context_points,
            source_urls=source_urls,
            summary_check=evaluate_summary_contract(
                final_summary_md,
                claims=knowledge_points,
                source_urls=source_urls,
                registry_ids=[report["id"], *reused_report_ids, *selected_finding_ids],
            ),
        )

    def _create_gap_fill_result(
        self,
        prompt: str,
        question: str,
        query_variants: list[str],
        gap_fill: GapFillBundle,
    ) -> MemoryRetrievalSkillResult:
        run = self.tools.create_run(
            {
                "question": question,
                "model_name": self.model_name,
                "model_version": self.model_version,
                "notes": "memory retrieval harness gap-fill",
                "dedupe_key": f"gapfill:{derive_subject(prompt)}:run",
            }
        )
        created_annotations: list[dict[str, Any]] = []
        for index, annotation in enumerate(gap_fill.annotations):
            created_annotations.append(
                self.tools.add_annotation(
                    {
                        "run_id": run["id"],
                        "source": annotation.source.model_dump(mode="json"),
                        "subject": annotation.subject,
                        "note": annotation.note,
                        "selector": annotation.selector.model_dump(mode="json"),
                        "quote_text": annotation.quote_text,
                        "confidence": annotation.confidence,
                        "tags": annotation.tags,
                        "model_name": self.model_name,
                        "model_version": self.model_version,
                        "dedupe_key": f"{run['id']}:ann:{index}",
                    }
                )
            )

        created_findings: list[dict[str, Any]] = []
        for index, finding in enumerate(gap_fill.findings):
            annotation_ids = [created_annotations[item]["id"] for item in finding.annotation_indexes]
            created_findings.append(
                self.tools.create_finding(
                    {
                        "title": finding.title,
                        "subject": finding.subject,
                        "claim": finding.claim,
                        "annotation_ids": annotation_ids,
                        "run_id": run["id"],
                        "confidence": finding.confidence,
                        "model_name": self.model_name,
                        "model_version": self.model_version,
                        "dedupe_key": f"{run['id']}:finding:{index}",
                    }
                )
            )

        created_report_id: str | None = None
        summary_md, knowledge_points, context_points, source_urls = self._build_summary(
            question=question,
            finding_ids=[finding["id"] for finding in created_findings],
            report_ids=[],
            created_report_id=None,
            created_run_id=run["id"],
            created_annotation_ids=[annotation["id"] for annotation in created_annotations],
            created_finding_ids=[finding["id"] for finding in created_findings],
        )
        if gap_fill.create_report and created_findings:
            report = self.tools.create_report(
                {
                    "question": question,
                    "subject": gap_fill.subject,
                    "summary_md": summary_md,
                    "finding_ids": [finding["id"] for finding in created_findings],
                    "run_id": run["id"],
                    "model_name": self.model_name,
                    "model_version": self.model_version,
                    "dedupe_key": f"{run['id']}:report",
                }
            )
            created_report_id = report["id"]
            summary_md, knowledge_points, context_points, source_urls = self._build_summary(
                question=question,
                finding_ids=[finding["id"] for finding in created_findings],
                report_ids=[created_report_id],
                created_report_id=created_report_id,
                created_run_id=run["id"],
                created_annotation_ids=[annotation["id"] for annotation in created_annotations],
                created_finding_ids=[finding["id"] for finding in created_findings],
            )

        return MemoryRetrievalSkillResult(
            mode="gap_fill",
            prompt=prompt,
            question=question,
            query_variants=query_variants,
            created_run_id=run["id"],
            created_annotation_ids=[annotation["id"] for annotation in created_annotations],
            created_finding_ids=[finding["id"] for finding in created_findings],
            created_report_id=created_report_id,
            summary_md=summary_md,
            knowledge_points=knowledge_points,
            context_points=context_points,
            source_urls=source_urls,
            summary_check=evaluate_summary_contract(
                summary_md,
                claims=knowledge_points,
                source_urls=source_urls,
                registry_ids=[
                    run["id"],
                    *[annotation["id"] for annotation in created_annotations],
                    *[finding["id"] for finding in created_findings],
                    *([created_report_id] if created_report_id else []),
                ],
            ),
        )

    def _build_summary(
        self,
        *,
        question: str,
        finding_ids: list[str],
        report_ids: list[str],
        created_report_id: str | None,
        created_run_id: str | None,
        created_annotation_ids: list[str],
        created_finding_ids: list[str],
    ) -> tuple[str, list[str], list[str], list[str]]:
        findings = [self.tools.get_finding(finding_id, include_private=True) for finding_id in finding_ids]
        annotation_ids = sorted({annotation_id for finding in findings for annotation_id in finding["annotation_ids"]})
        annotations = [self.tools.get_annotation(annotation_id, include_private=True) for annotation_id in annotation_ids]
        source_ids = sorted({annotation["source_id"] for annotation in annotations})
        sources = [self.tools.get_source(source_id, include_private=True) for source_id in source_ids]

        knowledge_points = [finding["claim"] for finding in findings]
        context_points = derive_context_points(findings, annotations)
        source_urls = [source["canonical_url"] for source in sources]

        lines = [
            f"# {question}",
            "",
            "## Answer",
            summarize_answer(knowledge_points),
            "",
            "## Knowledge To Reuse",
        ]
        for point in knowledge_points:
            lines.append(f"- {point}")
        lines.extend(["", "## Context To Carry Forward"])
        for point in context_points:
            lines.append(f"- {point}")
        lines.extend(["", "## Evidence"])
        for source in sources:
            lines.append(f"- {source['title']}: {source['canonical_url']}")
        lines.extend(["", "## Registry State"])
        lines.append(f"- Reused reports: {', '.join(report_ids) if report_ids else 'none'}")
        lines.append(f"- Reused findings: {', '.join(finding_ids) if finding_ids else 'none'}")
        lines.append(f"- Created run: {created_run_id or 'none'}")
        lines.append(f"- Created annotations: {', '.join(created_annotation_ids) if created_annotation_ids else 'none'}")
        lines.append(f"- Created findings: {', '.join(created_finding_ids) if created_finding_ids else 'none'}")
        lines.append(f"- Created report: {created_report_id or 'none'}")
        return "\n".join(lines), knowledge_points, context_points, source_urls


def load_topic_taxonomy() -> dict[str, list[str]]:
    taxonomy_path = Path(__file__).resolve().parents[2] / "skills" / "research-memory-retrieval" / "references" / "topic-taxonomy.md"
    if not taxonomy_path.exists():
        return {}
    categories: dict[str, list[str]] = {}
    current_category: str | None = None
    for line in taxonomy_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current_category = line.removeprefix("## ").strip()
            categories[current_category] = []
            continue
        if line.startswith("- ") and current_category:
            categories[current_category].append(line.removeprefix("- ").strip())
    return categories


def normalize_question(prompt: str) -> str:
    normalized = " ".join(prompt.strip().split())
    if not normalized:
        return "Memory and retrieval research"
    if normalized.endswith("?"):
        return normalized
    return f"{normalized}?"


def derive_subject(prompt: str) -> str:
    normalized = prompt.lower()
    normalized = re.sub(r"\b(research|investigate|look into|find|compare|please|about)\b", " ", normalized)
    normalized = re.sub(r"[^a-z0-9\s-]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" ?")
    return normalized or "memory retrieval"


def expand_query_variants(prompt: str, taxonomy: dict[str, list[str]]) -> list[str]:
    normalized = derive_subject(prompt)
    variants: list[str] = [normalized]

    metric_terms = ["recall precision", "temporal relevance", "retrieval latency"]
    failure_terms = ["stale indexes", "retrieval drift", "missing provenance"]
    retrieval_terms = taxonomy.get("Retrieval Systems", [])
    memory_terms = taxonomy.get("Agent Memory", [])
    has_failure = any(term in normalized for term in ("failure", "stale", "drift", "freshness", "provenance", "outdated"))
    has_rerank = any(term in normalized for term in ("rerank", "precision"))
    has_metrics = any(term in normalized for term in ("metric", "metrics", "evaluate", "evaluation", "score", "scoring", "temporal relevance"))
    has_optimization = "optimiz" in normalized
    has_memory = "memory" in normalized

    if has_rerank or has_optimization:
        variants.extend(
            [
                f"{normalized} reranking precision",
                f"{normalized} dense retrieval recall",
            ]
        )
    if has_memory and (has_metrics or has_failure or "long-term" in normalized):
        variants.extend(
            [
                f"{normalized} memory freshness",
                f"{normalized} episodic memory provenance",
                f"{normalized} temporal relevance",
            ]
        )
    if has_metrics or has_optimization:
        variants.extend([f"{normalized} {term}" for term in metric_terms])
    if has_failure:
        variants.extend([f"{normalized} {term}" for term in failure_terms])

    matched_terms = [term for term in [*retrieval_terms, *memory_terms] if term in normalized]
    if matched_terms:
        anchor = " ".join(matched_terms[:2])
        variants.append(f"{anchor} {metric_terms[0]}")
        if has_failure:
            variants.append(f"{anchor} {failure_terms[0]}")

    deduped: list[str] = []
    for variant in variants:
        candidate = " ".join(variant.split())
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped[:8]


def summarize_answer(knowledge_points: list[str]) -> str:
    if not knowledge_points:
        return "The registry does not yet contain enough source-backed evidence to answer this question."
    if len(knowledge_points) == 1:
        return knowledge_points[0]
    return f"{knowledge_points[0]} The rest of the evidence adds operational context rather than contradicting that core conclusion."


def extract_focus_tokens(prompt: str) -> set[str]:
    generic = {
        "research",
        "llm",
        "memory",
        "retrieval",
        "agent",
        "agents",
        "system",
        "systems",
        "long",
        "term",
        "into",
        "about",
        "with",
        "that",
        "this",
        "from",
        "their",
        "what",
        "does",
    }
    tokens = re.findall(r"[a-z0-9-]+", prompt.lower())
    return {token for token in tokens if len(token) >= 4 and token not in generic}


def token_overlap(focus_tokens: set[str], hit: dict[str, Any]) -> int:
    haystack = " ".join([hit.get("title", ""), hit.get("summary", ""), hit.get("subject", "")]).lower()
    return sum(1 for token in focus_tokens if token in haystack)


def should_create_synthesis_report(
    prompt: str,
    reports: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> bool:
    normalized = prompt.lower()
    synthesis_cues = ("compare", "tradeoff", "mitigation", "failure", "context", "summary")
    if not any(cue in normalized for cue in synthesis_cues):
        return False
    if len(findings) < 2:
        return False
    if not reports:
        return True
    subjects = {hit["subject"] for hit in findings[:2]}
    report_subjects = {hit["subject"] for hit in reports[:1]}
    return not subjects.issubset(report_subjects)


def derive_context_points(findings: list[dict[str, Any]], annotations: list[dict[str, Any]]) -> list[str]:
    haystack = " ".join(
        [
            *(finding["claim"] for finding in findings),
            *(finding["subject"] for finding in findings),
            *(annotation["note"] for annotation in annotations),
            *(" ".join(annotation.get("tags", [])) for annotation in annotations),
        ]
    ).lower()
    points: list[str] = []
    if "recall" in haystack and "precision" in haystack:
        points.append("Tune recall and precision as separate levers so optimization work does not hide failure tradeoffs behind one retrieval score.")
    if "rerank" in haystack:
        points.append("Keep broad retrieval and reranking as separate stages so you can optimize coverage before context pruning.")
    if "stale" in haystack or "index" in haystack or "freshness" in haystack:
        points.append("Treat freshness and reindexing as part of retrieval optimization, because stale memory stores degrade both correctness and trust.")
    if "provenance" in haystack or "anchor" in haystack:
        points.append("Carry source anchors and freshness metadata into retrieved memories so later synthesis stays auditable.")
    if "temporal relevance" in haystack or "latency" in haystack:
        points.append("Measure temporal relevance alongside latency, because a fast retrieval path can still return harmful or outdated memory.")
    if not points:
        points.append("Carry forward the linked findings and sources rather than the chat phrasing, because the durable context lives in the anchored evidence.")
    return points[:4]


def evaluate_summary_contract(
    summary_md: str,
    *,
    claims: list[str],
    source_urls: list[str],
    registry_ids: list[str],
) -> SummaryContractCheck:
    required_sections_present = all(
        section in summary_md
        for section in [
            "## Answer",
            "## Knowledge To Reuse",
            "## Context To Carry Forward",
            "## Evidence",
            "## Registry State",
        ]
    )
    mentions_claims = any(claim[:32] in summary_md for claim in claims if len(claim) >= 32) or any(claim in summary_md for claim in claims)
    mentions_context = "Context To Carry Forward" in summary_md and "- " in summary_md.split("## Context To Carry Forward", 1)[1]
    mentions_sources = any(url in summary_md for url in source_urls)
    mentions_registry_state = any(record_id in summary_md for record_id in registry_ids)
    passed = all(
        [
            required_sections_present,
            mentions_claims,
            mentions_context,
            mentions_sources,
            mentions_registry_state,
        ]
    )
    return SummaryContractCheck(
        required_sections_present=required_sections_present,
        mentions_claims=mentions_claims,
        mentions_context=mentions_context,
        mentions_sources=mentions_sources,
        mentions_registry_state=mentions_registry_state,
        passed=passed,
    )


def optimization_gap_fill_bundle() -> GapFillBundle:
    return GapFillBundle(
        subject="long-term memory retrieval optimization",
        annotations=[
            SkillEvidenceAnnotation(
                source=SourceCreate(
                    canonical_url="https://example.org/optimization-recall-precision",
                    title="Optimizing recall and precision in memory retrieval",
                    source_type="paper",
                    snapshot_required=True,
                    snapshot_present=True,
                ),
                subject="long-term memory retrieval optimization",
                note="The source argues that recall and precision must be tracked separately when optimizing retrieval quality.",
                selector=SourceSelector(
                    exact="Recall and precision must be tracked separately when optimizing retrieval quality.",
                    deep_link="https://example.org/optimization-recall-precision#results",
                ),
                tags=["memory", "retrieval", "optimization", "recall", "precision"],
            ),
            SkillEvidenceAnnotation(
                source=SourceCreate(
                    canonical_url="https://example.org/optimization-temporal-relevance",
                    title="Temporal relevance in persistent memory retrieval",
                    source_type="paper",
                    snapshot_required=True,
                    snapshot_present=True,
                ),
                subject="long-term memory retrieval optimization",
                note="The source argues that stale but correctly retrieved memories still harm downstream reasoning, so temporal relevance should be optimized directly.",
                selector=SourceSelector(
                    exact="Stale but correctly retrieved memories still harm downstream reasoning, so temporal relevance should be optimized directly.",
                    deep_link="https://example.org/optimization-temporal-relevance#discussion",
                ),
                tags=["memory", "retrieval", "optimization", "temporal relevance", "freshness"],
            ),
        ],
        findings=[
            SkillEvidenceFinding(
                title="Retrieval optimization needs recall, precision, and temporal relevance",
                subject="long-term memory retrieval optimization",
                claim="Optimizing long-term memory retrieval requires recall, precision, and temporal relevance, because stale but correctly retrieved memories can still degrade reasoning.",
                annotation_indexes=[0, 1],
            )
        ],
        create_report=True,
    )
