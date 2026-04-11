from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from .memory_retrieval_skill import (
    GapFillBundle,
    MemoryRetrievalSkillResult,
    SkillEvidenceAnnotation,
    SkillEvidenceFinding,
    derive_subject,
    evaluate_summary_contract,
    extract_focus_tokens,
    normalize_question,
    summarize_answer,
    token_overlap,
)
from .models import (
    AnnotationCreate,
    FindingCreate,
    PublishRequest,
    ReportCompileCreate,
    ReviewRequest,
    RunCreate,
    SourceCreate,
    SourceSelector,
)


class DomainContextRule(BaseModel):
    keywords: list[str]
    message: str


class DomainSpecialistConfig(BaseModel):
    domain_id: str
    label: str
    keywords: list[str]
    metric_terms: list[str] = Field(default_factory=list)
    method_terms: list[str] = Field(default_factory=list)
    failure_terms: list[str] = Field(default_factory=list)
    synthesis_cues: list[str] = Field(default_factory=list)
    context_rules: list[DomainContextRule] = Field(default_factory=list)


INFERENCE_OPTIMIZATION_CONFIG = DomainSpecialistConfig(
    domain_id="inference-optimization",
    label="Inference Optimization",
    keywords=[
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
    ],
    metric_terms=["latency throughput", "cost efficiency", "tail latency"],
    method_terms=["speculative decoding", "prefix caching", "continuous batching"],
    failure_terms=["memory pressure", "cache thrash", "acceptance rate"],
    synthesis_cues=["tradeoff", "latency", "throughput", "cost", "failure", "mitigation", "context", "serving"],
    context_rules=[
        DomainContextRule(
            keywords=["latency", "throughput"],
            message="Separate throughput gains from tail-latency regressions so serving improvements do not hide bad interactive behavior.",
        ),
        DomainContextRule(
            keywords=["speculative", "acceptance"],
            message="Track draft-model acceptance rate because speculative decoding only helps when acceptance stays high enough to amortize extra work.",
        ),
        DomainContextRule(
            keywords=["cache", "memory pressure", "prefix", "kv"],
            message="Treat cache policy as part of inference optimization, because caching can improve repeated prompts while destabilizing memory footprint and utilization.",
        ),
        DomainContextRule(
            keywords=["batching"],
            message="Measure batching policy against tail latency and saturation together, because higher utilization can still worsen user-facing responsiveness.",
        ),
    ],
)

LLM_EVALS_CONFIG = DomainSpecialistConfig(
    domain_id="llm-evals",
    label="LLM Evals",
    keywords=[
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
    ],
    metric_terms=["task success", "failure coverage", "human agreement"],
    method_terms=["judge model calibration", "benchmark drift", "offline online eval"],
    failure_terms=["distribution shift", "rubric drift", "label disagreement"],
    synthesis_cues=["tradeoff", "benchmark", "calibration", "drift", "failure", "mitigation", "context", "evaluation"],
    context_rules=[
        DomainContextRule(
            keywords=["judge", "human", "calibration"],
            message="Calibrate judge-model outputs against human labels so evaluation automation does not drift away from what people actually consider correct.",
        ),
        DomainContextRule(
            keywords=["capability", "failure", "safety"],
            message="Track capability and failure metrics side by side so evaluation improvements do not come from ignoring risky failure modes.",
        ),
        DomainContextRule(
            keywords=["offline", "online", "shift", "drift"],
            message="Treat offline benchmarks as a filter, not the whole answer, because distribution shift can invalidate an apparently strong offline score.",
        ),
    ],
)


DOMAIN_SPECIALIST_CONFIGS = {
    INFERENCE_OPTIMIZATION_CONFIG.domain_id: INFERENCE_OPTIMIZATION_CONFIG,
    LLM_EVALS_CONFIG.domain_id: LLM_EVALS_CONFIG,
}


class DomainSpecialistHarness:
    def __init__(
        self,
        tools,
        config: DomainSpecialistConfig,
        *,
        model_name: str = "gpt-5.4",
        model_version: str = "2026-04-10",
    ):
        self.tools = tools
        self.config = config
        self.model_name = model_name
        self.model_version = model_version
        self.domain_common_tokens = self._build_domain_common_tokens()

    def research(
        self,
        prompt: str,
        *,
        gap_fill: GapFillBundle | None = None,
        prefer_report: bool = True,
    ) -> MemoryRetrievalSkillResult:
        question = normalize_question(prompt)
        query_variants = self._expand_query_variants(prompt)
        relevant_reports, relevant_findings = self._search_relevant_artifacts(query_variants)

        if gap_fill is not None:
            if self._has_specific_coverage(prompt, relevant_reports, relevant_findings):
                return self._create_reuse_result(prompt, question, query_variants, relevant_reports, relevant_findings)
            return self._create_gap_fill_result(prompt, question, query_variants, gap_fill)

        if prefer_report and self._should_create_synthesis_report(prompt, relevant_reports, relevant_findings):
            return self._create_synthesis_result(prompt, question, query_variants, relevant_reports, relevant_findings)

        return self._create_reuse_result(prompt, question, query_variants, relevant_reports, relevant_findings)

    def _expand_query_variants(self, prompt: str) -> list[str]:
        normalized = derive_subject(prompt)
        variants = [normalized]
        has_failure = any(term in normalized for term in self.config.failure_terms) or any(
            token in normalized for token in ("failure", "drift", "shift", "pressure", "regression", "mitigation")
        )
        has_metrics = any(
            token in normalized
            for token in (
                "metric",
                "metrics",
                "latency",
                "throughput",
                "cost",
                "evaluate",
                "evaluation",
                "score",
                "benchmark",
                "agreement",
                "quality",
            )
        )
        variants.extend(f"{normalized} {term}" for term in self.config.method_terms[:2] if term not in normalized)
        if has_metrics:
            variants.extend(f"{normalized} {term}" for term in self.config.metric_terms[:2])
        if has_failure:
            variants.extend(f"{normalized} {term}" for term in self.config.failure_terms[:2])
        deduped: list[str] = []
        for variant in variants:
            candidate = " ".join(variant.split())
            if candidate and candidate not in deduped:
                deduped.append(candidate)
        return deduped[:8]

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
        specific_tokens = {token for token in focus_tokens if token not in self.domain_common_tokens}
        if specific_tokens:
            specific_overlap = max(self._exact_token_overlap(specific_tokens, hit) for hit in relevant_hits)
            if specific_overlap == 0:
                return False
        best_overlap = max(token_overlap(focus_tokens, hit) for hit in relevant_hits)
        target_overlap = 2 if len(focus_tokens) >= 3 else 1
        return best_overlap >= target_overlap

    def _build_domain_common_tokens(self) -> set[str]:
        tokens = {
            "research",
            "llm",
            "model",
            "models",
            "system",
            "systems",
            "context",
            "tradeoff",
            "tradeoffs",
            "metric",
            "metrics",
            "optimization",
            "reliability",
        }
        fields = [
            self.config.domain_id,
            self.config.label,
            *self.config.keywords,
            *self.config.metric_terms,
            *self.config.method_terms,
            *self.config.failure_terms,
            *self.config.synthesis_cues,
        ]
        for field in fields:
            tokens.update(re.findall(r"[a-z0-9-]+", field.lower()))
        return tokens

    def _exact_token_overlap(self, focus_tokens: set[str], hit: dict[str, Any]) -> int:
        haystack_tokens = set(
            re.findall(
                r"[a-z0-9-]+",
                " ".join([hit.get("title", ""), hit.get("summary", ""), hit.get("subject", "")]).lower(),
            )
        )
        return sum(1 for token in focus_tokens if token in haystack_tokens)

    def _should_create_synthesis_report(
        self,
        prompt: str,
        reports: list[dict[str, Any]],
        findings: list[dict[str, Any]],
    ) -> bool:
        normalized = prompt.lower()
        if not any(cue in normalized for cue in self.config.synthesis_cues):
            return False
        if len(findings) < 2:
            return False
        if not reports:
            return True
        subjects = {hit["subject"] for hit in findings[:2]}
        report_subjects = {hit["subject"] for hit in reports[:1]}
        return not subjects.issubset(report_subjects)

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
        return MemoryRetrievalSkillResult(
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
                "dedupe_key": f"{self.config.domain_id}:synthesis:{derive_subject(prompt)}:{':'.join(selected_finding_ids)}",
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
                "notes": f"{self.config.domain_id} harness gap-fill",
                "dedupe_key": f"{self.config.domain_id}:gapfill:{derive_subject(prompt)}:run",
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
        context_points = self._derive_context_points(findings, annotations)
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

    def _derive_context_points(self, findings: list[dict[str, Any]], annotations: list[dict[str, Any]]) -> list[str]:
        haystack = " ".join(
            [
                *(finding["claim"] for finding in findings),
                *(finding["subject"] for finding in findings),
                *(annotation["note"] for annotation in annotations),
                *(" ".join(annotation.get("tags", [])) for annotation in annotations),
            ]
        ).lower()
        points: list[str] = []
        for rule in self.config.context_rules:
            if any(keyword in haystack for keyword in rule.keywords):
                points.append(rule.message)
        if not points:
            points.append(
                "Carry forward the linked findings and sources rather than only the chat phrasing, because the durable context lives in the anchored evidence."
            )
        return points[:4]


def inference_optimization_gap_fill_bundle() -> GapFillBundle:
    return GapFillBundle(
        subject="inference optimization",
        annotations=[
            SkillEvidenceAnnotation(
                source=SourceCreate(
                    canonical_url="https://example.org/quantization-tail-latency",
                    title="Quantization and tail latency in LLM serving",
                    source_type="paper",
                    snapshot_required=True,
                    snapshot_present=True,
                ),
                subject="inference optimization",
                note="This source supports evaluating quantization against tail latency rather than average latency alone.",
                selector=SourceSelector(
                    exact="Quantization should be evaluated against tail latency rather than average latency alone.",
                    deep_link="https://example.org/quantization-tail-latency#results",
                ),
                tags=["inference", "quantization", "latency", "tail latency"],
            ),
            SkillEvidenceAnnotation(
                source=SourceCreate(
                    canonical_url="https://example.org/batching-interactive-cost",
                    title="Batching policies for interactive LLM serving",
                    source_type="paper",
                    snapshot_required=True,
                    snapshot_present=True,
                ),
                subject="inference optimization",
                note="This source supports measuring batching policy against both saturation and user-facing latency.",
                selector=SourceSelector(
                    exact="Continuous batching should be measured against both saturation and user-facing latency.",
                    deep_link="https://example.org/batching-interactive-cost#discussion",
                ),
                tags=["inference", "batching", "throughput", "latency"],
            ),
        ],
        findings=[
            SkillEvidenceFinding(
                title="Inference optimization needs tail-latency and batching-aware metrics",
                subject="inference optimization",
                claim="Inference optimization should track tail latency and batching behavior together, because average gains can hide regressions in interactive responsiveness.",
                annotation_indexes=[0, 1],
            )
        ],
    )


def llm_evals_gap_fill_bundle() -> GapFillBundle:
    return GapFillBundle(
        subject="llm evaluation reliability",
        annotations=[
            SkillEvidenceAnnotation(
                source=SourceCreate(
                    canonical_url="https://example.org/online-eval-regression-detection",
                    title="Online regression detection for LLM evaluation pipelines",
                    source_type="paper",
                    snapshot_required=True,
                    snapshot_present=True,
                ),
                subject="llm evaluation reliability",
                note="This source supports adding online regression checks because offline benchmarks miss product-specific drift.",
                selector=SourceSelector(
                    exact="Offline benchmarks miss product-specific drift, so online regression checks are required.",
                    deep_link="https://example.org/online-eval-regression-detection#results",
                ),
                tags=["evals", "online eval", "drift"],
            ),
            SkillEvidenceAnnotation(
                source=SourceCreate(
                    canonical_url="https://example.org/rubric-audit-sampling",
                    title="Audit sampling for rubric-driven LLM evals",
                    source_type="paper",
                    snapshot_required=True,
                    snapshot_present=True,
                ),
                subject="llm evaluation reliability",
                note="This source supports periodic audit sampling to keep rubric-driven evaluation pipelines aligned with human judgment.",
                selector=SourceSelector(
                    exact="Periodic audit sampling keeps rubric-driven evaluation pipelines aligned with human judgment.",
                    deep_link="https://example.org/rubric-audit-sampling#discussion",
                ),
                tags=["evals", "rubric", "human agreement"],
            ),
        ],
        findings=[
            SkillEvidenceFinding(
                title="Reliable eval pipelines need online checks and audit sampling",
                subject="llm evaluation reliability",
                claim="Reliable LLM evaluation pipelines need online regression checks plus periodic audit sampling so offline benchmark wins do not mask rubric drift or distribution shift.",
                annotation_indexes=[0, 1],
            )
        ],
    )


def seed_inference_optimization(service) -> dict[str, str]:
    existing = service.search("speculative decoding", kind="finding", include_private=True).hits
    if any(hit.title == "Speculative decoding depends on acceptance rate" for hit in existing):
        return {}
    run = service.create_run(
        RunCreate(
            question="What patterns matter most in LLM inference optimization?",
            model_name="gpt-5.4",
            model_version="2026-04-10",
            notes="Seed corpus for inference optimization specialist coverage.",
        )
    )
    speculative_source = service.create_source(
        SourceCreate(
            canonical_url="https://example.org/speculative-decoding-acceptance",
            title="Acceptance rate governs speculative decoding gains",
            source_type="paper",
            site_name="Example Systems Lab",
            author="T. Engineer",
            snippet="Speculative decoding improves latency only when draft-token acceptance remains high enough to avoid wasted validation work.",
            content_sha256=service._hash_text("speculative decoding acceptance remains high"),
            snapshot_url="https://archive.example.org/speculative-decoding-acceptance",
            snapshot_required=True,
            snapshot_present=True,
            visibility="private",
        )
    )
    caching_source = service.create_source(
        SourceCreate(
            canonical_url="https://example.org/prefix-caching-memory-pressure",
            title="Prefix caching trades throughput for memory pressure",
            source_type="paper",
            site_name="Example Infra Journal",
            author="C. Operator",
            snippet="Prefix caching helps repeated prompts but can create memory pressure and eviction churn without explicit cache policy.",
            content_sha256=service._hash_text("prefix caching memory pressure eviction churn"),
            snapshot_url="https://archive.example.org/prefix-caching-memory-pressure",
            snapshot_required=True,
            snapshot_present=True,
            visibility="private",
        )
    )
    speculative_annotation = service.create_annotation(
        AnnotationCreate(
            source_id=speculative_source.id,
            run_id=run.id,
            subject="speculative decoding",
            note="This supports using acceptance rate as a primary control metric for speculative decoding.",
            selector=SourceSelector(
                exact="Speculative decoding improves latency only when draft-token acceptance remains high enough to avoid wasted validation work.",
                deep_link="https://example.org/speculative-decoding-acceptance#results",
            ),
            confidence=0.88,
            model_name="gpt-5.4",
            model_version="2026-04-10",
            tags=["inference", "speculative decoding", "latency", "acceptance rate"],
        )
    )
    caching_annotation = service.create_annotation(
        AnnotationCreate(
            source_id=caching_source.id,
            run_id=run.id,
            subject="inference caching",
            note="This supports treating cache policy as a throughput and memory tradeoff rather than a free win.",
            selector=SourceSelector(
                exact="Prefix caching helps repeated prompts but can create memory pressure and eviction churn without explicit cache policy.",
                deep_link="https://example.org/prefix-caching-memory-pressure#discussion",
            ),
            confidence=0.86,
            model_name="gpt-5.4",
            model_version="2026-04-10",
            tags=["inference", "prefix caching", "throughput", "memory pressure"],
        )
    )
    speculative_finding = service.create_finding(
        FindingCreate(
            title="Speculative decoding depends on acceptance rate",
            subject="speculative decoding",
            claim="Speculative decoding only improves inference latency when draft-token acceptance remains high enough to offset extra validation work.",
            annotation_ids=[speculative_annotation.id],
            model_name="gpt-5.4",
            model_version="2026-04-10",
            run_id=run.id,
        )
    )
    caching_finding = service.create_finding(
        FindingCreate(
            title="Cache policy shapes throughput and memory pressure",
            subject="inference caching",
            claim="Prefix and KV caching should be optimized with explicit eviction policy because throughput gains can be offset by memory pressure and eviction churn.",
            annotation_ids=[caching_annotation.id],
            model_name="gpt-5.4",
            model_version="2026-04-10",
            run_id=run.id,
        )
    )
    report = service.compile_report(
        ReportCompileCreate(
            question="What usually limits inference optimization gains in LLM serving?",
            subject="inference optimization",
            finding_ids=[speculative_finding.id, caching_finding.id],
            model_name="gpt-5.4",
            model_version="2026-04-10",
            run_id=run.id,
        )
    )
    for kind, record_id in [
        ("annotation", speculative_annotation.id),
        ("annotation", caching_annotation.id),
        ("finding", speculative_finding.id),
        ("finding", caching_finding.id),
        ("report", report.id),
    ]:
        service.review(ReviewRequest(kind=kind, record_id=record_id))
    service.publish(PublishRequest(kind="report", record_id=report.id, include_in_global_index=True))
    return {
        "run_id": run.id,
        "speculative_finding_id": speculative_finding.id,
        "caching_finding_id": caching_finding.id,
        "report_id": report.id,
    }


def seed_llm_evals(service) -> dict[str, str]:
    existing = service.search("judge model calibration", kind="finding", include_private=True).hits
    if any(hit.title == "Judge-model evals require human calibration" for hit in existing):
        return {}
    run = service.create_run(
        RunCreate(
            question="What patterns matter most in reliable LLM evaluation pipelines?",
            model_name="gpt-5.4",
            model_version="2026-04-10",
            notes="Seed corpus for LLM eval specialist coverage.",
        )
    )
    calibration_source = service.create_source(
        SourceCreate(
            canonical_url="https://example.org/judge-model-human-calibration",
            title="Judge models need periodic human calibration",
            source_type="paper",
            site_name="Example Eval Lab",
            author="R. Evaluator",
            snippet="Judge-model pipelines drift without periodic calibration against human labels and disagreement audits.",
            content_sha256=service._hash_text("judge model periodic human calibration disagreement audits"),
            snapshot_url="https://archive.example.org/judge-model-human-calibration",
            snapshot_required=True,
            snapshot_present=True,
            visibility="private",
        )
    )
    drift_source = service.create_source(
        SourceCreate(
            canonical_url="https://example.org/benchmark-drift-coverage",
            title="Benchmark drift hides evaluation blind spots",
            source_type="paper",
            site_name="Example Reliability Review",
            author="L. Analyst",
            snippet="Evaluation suites should track capability and failure coverage together because benchmark wins can still miss drifted failure modes.",
            content_sha256=service._hash_text("benchmark drift capability failure coverage"),
            snapshot_url="https://archive.example.org/benchmark-drift-coverage",
            snapshot_required=True,
            snapshot_present=True,
            visibility="private",
        )
    )
    calibration_annotation = service.create_annotation(
        AnnotationCreate(
            source_id=calibration_source.id,
            run_id=run.id,
            subject="judge model calibration",
            note="This supports calibrating automated eval judges against human labels on a recurring schedule.",
            selector=SourceSelector(
                exact="Judge-model pipelines drift without periodic calibration against human labels and disagreement audits.",
                deep_link="https://example.org/judge-model-human-calibration#results",
            ),
            confidence=0.87,
            model_name="gpt-5.4",
            model_version="2026-04-10",
            tags=["evals", "judge model", "human labels", "calibration"],
        )
    )
    drift_annotation = service.create_annotation(
        AnnotationCreate(
            source_id=drift_source.id,
            run_id=run.id,
            subject="benchmark drift",
            note="This supports pairing capability metrics with failure coverage so evaluation suites do not hide blind spots.",
            selector=SourceSelector(
                exact="Evaluation suites should track capability and failure coverage together because benchmark wins can still miss drifted failure modes.",
                deep_link="https://example.org/benchmark-drift-coverage#discussion",
            ),
            confidence=0.86,
            model_name="gpt-5.4",
            model_version="2026-04-10",
            tags=["evals", "benchmark drift", "failure coverage"],
        )
    )
    calibration_finding = service.create_finding(
        FindingCreate(
            title="Judge-model evals require human calibration",
            subject="judge model calibration",
            claim="Judge-model evaluation pipelines require periodic calibration and disagreement audits against human labels, or automated judging drifts over time.",
            annotation_ids=[calibration_annotation.id],
            model_name="gpt-5.4",
            model_version="2026-04-10",
            run_id=run.id,
        )
    )
    drift_finding = service.create_finding(
        FindingCreate(
            title="Evaluation suites need capability and failure metrics",
            subject="evaluation reliability",
            claim="Reliable evaluation suites should track capability and failure coverage together, because benchmark improvements can still hide drifted failure modes.",
            annotation_ids=[drift_annotation.id],
            model_name="gpt-5.4",
            model_version="2026-04-10",
            run_id=run.id,
        )
    )
    report = service.compile_report(
        ReportCompileCreate(
            question="What makes LLM evaluation pipelines reliable over time?",
            subject="llm evaluation reliability",
            finding_ids=[calibration_finding.id, drift_finding.id],
            model_name="gpt-5.4",
            model_version="2026-04-10",
            run_id=run.id,
        )
    )
    for kind, record_id in [
        ("annotation", calibration_annotation.id),
        ("annotation", drift_annotation.id),
        ("finding", calibration_finding.id),
        ("finding", drift_finding.id),
        ("report", report.id),
    ]:
        service.review(ReviewRequest(kind=kind, record_id=record_id))
    service.publish(PublishRequest(kind="report", record_id=report.id, include_in_global_index=True))
    return {
        "run_id": run.id,
        "calibration_finding_id": calibration_finding.id,
        "drift_finding_id": drift_finding.id,
        "report_id": report.id,
    }


def seed_specialist_domains(service) -> dict[str, dict[str, str]]:
    return {
        "inference-optimization": seed_inference_optimization(service),
        "llm-evals": seed_llm_evals(service),
    }


def build_domain_harness(domain_id: str, tools, *, model_name: str = "gpt-5.4", model_version: str = "2026-04-10") -> DomainSpecialistHarness:
    return DomainSpecialistHarness(
        tools,
        DOMAIN_SPECIALIST_CONFIGS[domain_id],
        model_name=model_name,
        model_version=model_version,
    )
