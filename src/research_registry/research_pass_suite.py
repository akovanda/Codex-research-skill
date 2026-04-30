from __future__ import annotations

import argparse
import json
from collections import Counter

from pydantic import BaseModel, Field

from .research_capture import specialized_domain_for_prompt


class ResearchPassSpec(BaseModel):
    pass_id: str
    wave: int
    theme: str
    prompt: str
    why_it_matters: str
    expected_domain: str | None
    expected_initial_outcome: str
    source_signals: list[str] = Field(default_factory=list)


RESEARCH_PASS_SPECS: tuple[ResearchPassSpec, ...] = (
    ResearchPassSpec(
        pass_id="eval-long-vs-mab-coverage",
        wave=1,
        theme="Benchmark Fit",
        prompt="Research evaluation coverage gaps between the current public long-horizon benchmark suites for multi-session agents.",
        why_it_matters="This is the top-level benchmark framing question behind the example overnight compare workflow.",
        expected_domain="llm-evals",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "benchmark-lab: overnight public batch compares LongMemEval and MemoryAgentBench slices",
            "benchmark-lab: public benchmark orchestration and suite docs",
        ],
    ),
    ResearchPassSpec(
        pass_id="eval-window-sampling-cpu-budget",
        wave=1,
        theme="Benchmark Fit",
        prompt="Research benchmark window sampling strategy for candidate comparisons under CPU budget constraints.",
        why_it_matters="The example corpus introduces start-index slicing specifically to make overnight candidate comparisons feasible.",
        expected_domain="llm-evals",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "benchmark-lab: added --start-index controls for LongMemEval and MemoryAgentBench subset runs",
            "benchmark-lab: overnight batch windows 0:50 and 0:25 style slices",
        ],
    ),
    ResearchPassSpec(
        pass_id="eval-judge-model-calibration-local",
        wave=1,
        theme="Benchmark Fit",
        prompt="Research judge model calibration risks when the answer model and judge model are the same local model in benchmark runs.",
        why_it_matters="Example public runs use the same small local model for answer and judge, which creates evaluation credibility risk.",
        expected_domain="llm-evals",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "benchmark-lab: Qwen/Qwen2.5-0.5B-Instruct used for answer and judge lanes",
            "benchmark-lab: official LongMemEval runner accepts answer-model and judge-model flags",
        ],
    ),
    ResearchPassSpec(
        pass_id="eval-eventqa-vs-fact-subsets",
        wave=1,
        theme="Benchmark Fit",
        prompt="Research evaluation differences between eventqa_full and factconsolidation_sh_6k subsets for long-horizon benchmark runs.",
        why_it_matters="The example corpus cuts eventqa from the CPU resume path, so this question determines whether that lane is worth its cost.",
        expected_domain="llm-evals",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "benchmark-lab: eventqa_full disabled in CPU-only resume",
            "benchmark-lab: separate fact and eventqa subset runners",
        ],
    ),
    ResearchPassSpec(
        pass_id="eval-release-gate-variant-promotion",
        wave=1,
        theme="Benchmark Fit",
        prompt="Research evaluation gate design for promoting long-horizon agent variants from candidate screen to release.",
        why_it_matters="The project already runs candidate screens and release-style gates, but the promotion criteria need to be defensible.",
        expected_domain="llm-evals",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "benchmark-lab: release_gate and candidate screen suites",
            "benchmark-lab: baseline_current, cand80, and cand119 are compared as promotion candidates",
        ],
    ),
    ResearchPassSpec(
        pass_id="eval-history-schema-regression-review",
        wave=1,
        theme="Benchmark Fit",
        prompt="Research evaluation artifact schemas that make benchmark history useful for post-run diagnosis and regression review.",
        why_it_matters="The example overnight run uses --record-history everywhere, so the value depends on whether those artifacts support meaningful diagnosis later.",
        expected_domain="llm-evals",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "benchmark-lab: overnight batch adds --record-history to official and subset runs",
            "benchmark-lab: artifacts and reporting pipeline",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-rollup-query-gating",
        wave=2,
        theme="Retrieval Mechanics",
        prompt="Research retrieval failure modes of rollup query gating in long-term memory systems.",
        why_it_matters="Rollup gating is directly exposed in the example memory runtime diagnostics and is central to the cand80 variant naming.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "memory-runtime: rollup_query_gated_enabled diagnostics",
            "benchmark-lab: cand80_rollup_duration_commute_guard_v1 variant under evaluation",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-multi-session-anchor-guard",
        wave=2,
        theme="Retrieval Mechanics",
        prompt="Research multi-session anchor guard strategies for long-term memory retrieval.",
        why_it_matters="The cand119 line explicitly tests multi-session anchor guards, so this is a real algorithm question rather than a generic memory topic.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "benchmark-lab: cand119_cand114_ms_anchor_guard_strict_v1 variant under evaluation",
            "memory-runtime: ms_anchor_guard and ms_anchor_guard_strict bonuses",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-strict-fact-guard",
        wave=2,
        theme="Retrieval Mechanics",
        prompt="Research strict fact guard designs that keep retrieval from polluting exact factual memory answers.",
        why_it_matters="Strict fact guards are already part of the retrieval engine and interact with rollups and source-card promotion.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "memory-runtime: fact_guard_strict_enabled diagnostics",
            "memory-runtime: retrieval promotions are disabled under strict fact guard",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-episodic-vs-semantic-split",
        wave=2,
        theme="Retrieval Mechanics",
        prompt="Research episodic versus semantic memory split policies for long-memory systems.",
        why_it_matters="The example product spans an episodic memory runtime plus story-context style semantic memory, so the boundary between them is fundamental.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "branch-sandbox: semantic precision runner separate from retrieval-backed fallback",
            "memory-runtime: episodic timeline and summary selection logic",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-branch-private-isolation",
        wave=2,
        theme="Retrieval Mechanics",
        prompt="Research branch-private memory isolation strategies for divergent narrative and coding branches.",
        why_it_matters="Branch isolation already shows up in the example verification corpus and will matter even more in a public coding-oriented memory tool.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "branch-sandbox: full stack verification checks coding branch isolation",
            "branch-sandbox: freeform and typed-thread branch-private memory fixtures",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-write-policy-durable-events",
        wave=2,
        theme="Retrieval Mechanics",
        prompt="Research memory write policies for deciding which events become durable long-term memory in coding and narrative sessions.",
        why_it_matters="A public tool needs a disciplined write policy or the registry and memory engine both become noisy.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "branch-sandbox: session persistence and summary generation over long sessions",
            "memory-runtime: low-salience episodic forgetting and compaction logic",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-compaction-and-archival",
        wave=2,
        theme="Retrieval Mechanics",
        prompt="Research low-salience episodic compaction and archival strategies for long-term memory stores.",
        why_it_matters="Compaction and forgetting are already implemented in the example memory runtime and need stronger external grounding before publicizing the system.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "memory-runtime: _compact_summarized_timeline_entries and forgetting profile logic",
            "branch-sandbox: long-horizon and freeform soak runners",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-provenance-and-freshness",
        wave=2,
        theme="Retrieval Mechanics",
        prompt="Research provenance and freshness requirements for public memory retrieval systems.",
        why_it_matters="The move from a private workflow helper to a public tool means the engine now needs auditable memory answers, not just plausible recall.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="synthesis",
        source_signals=[
            "registry-example: research-memory-retrieval specialist already emphasizes provenance and freshness",
            "memory-runtime: public-facing memory product direction from current project context",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-context-budget-allocation",
        wave=3,
        theme="Context Assembly",
        prompt="Research context management policies for allocating budget across instructions, commitments, semantic memory, and episodic memory.",
        why_it_matters="The example stack already tests memory dropping under pressure, which is the same decision surface a public memory tool will need.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "branch-sandbox: budget pressure runner drops memory and instruction candidates",
            "memory-runtime: selected hit sets across commitment, fact, summary, and episodic channels",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-semantic-precision-separation",
        wave=3,
        theme="Context Assembly",
        prompt="Research semantic memory retrieval precision tests that stay separate from secondary memory fallback.",
        why_it_matters="You already have a semantic precision runner, so the open question is how to keep that signal clean as the generic product grows.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "branch-sandbox: semantic precision regression and analyzer",
            "branch-sandbox: explicit separation between semantic retrieval and secondary memory fallback",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-duration-commute-retrieval",
        wave=3,
        theme="Context Assembly",
        prompt="Research retrieval strategies for duration and commute questions in long-memory systems.",
        why_it_matters="The cand80 variant directly targets duration and commute behavior, so this deserves focused research rather than intuition.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "benchmark-lab: cand80_rollup_duration_commute_guard_v1",
            "memory-runtime: duration_time_allocation and commute-route bonuses",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-retrieval-objects",
        wave=3,
        theme="Context Assembly",
        prompt="Research retrieval object designs such as source cards, segment heads, and answer windows for long-context memory.",
        why_it_matters="The example memory runtime already mixes episodic candidates with retrieval objects, so this is a concrete architecture decision.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "memory-runtime: source cards, segment heads, and answer windows routing",
            "benchmark-lab: public shadow diagnostics over retrieval object counts",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-typed-anchor-model",
        wave=3,
        theme="Context Assembly",
        prompt="Research typed-anchor memory models for rivalry, polity, obligation, and branch-private long-memory threads.",
        why_it_matters="The example branch sandbox already uses typed anchors and open-form threads, which is likely to generalize into a public memory product.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "branch-sandbox: freeform fixtures with romance, rivalry, polity, and obligation anchor types",
            "branch-sandbox: deep recall and freeform soak validation",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-public-api-shape",
        wave=4,
        theme="Productization",
        prompt="Research API and data-model patterns for a public long-term memory service that serves chat, coding, and learning workloads.",
        why_it_matters="The public-tool transition depends on keeping the memory runtime generic while still supporting workload-specific behavior.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "memory-runtime: generic memory library positioning",
            "branch-sandbox: live verification across chat, coding, and learning scopes",
        ],
    ),
    ResearchPassSpec(
        pass_id="memory-release-validation",
        wave=4,
        theme="Productization",
        prompt="Research memory regression validation patterns for shipping a public memory tool without silent recall failures.",
        why_it_matters="The example corpus already has multiple smoke and soak runners, but a public product needs a clearer release-validation theory.",
        expected_domain="memory-retrieval",
        expected_initial_outcome="synthesis",
        source_signals=[
            "branch-sandbox: smoke, deep recall, freeform, and semantic precision regressions",
            "benchmark-lab: release gate and reporting workflows",
        ],
    ),
    ResearchPassSpec(
        pass_id="inference-cpu-vs-gpu-benchmark-latency",
        wave=4,
        theme="Performance",
        prompt="Research inference latency tradeoffs between CPU and GPU paths for long benchmark queues.",
        why_it_matters="The example overnight run repeatedly hit CPU feasibility constraints, so serving strategy affects the whole research loop.",
        expected_domain="inference-optimization",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "benchmark-lab: CPU-only resume path and eventqa lane removal",
            "benchmark-lab: long overnight batch orchestration",
        ],
    ),
    ResearchPassSpec(
        pass_id="inference-record-history-cost",
        wave=4,
        theme="Performance",
        prompt="Research inference and throughput cost of record-history logging during benchmark execution.",
        why_it_matters="Record-history is useful only if its logging overhead does not distort benchmark throughput or queue time.",
        expected_domain="inference-optimization",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "benchmark-lab: --record-history enabled across overnight jobs",
            "benchmark-lab: artifact-heavy run plans",
        ],
    ),
    ResearchPassSpec(
        pass_id="inference-topk-context-expansion",
        wave=4,
        theme="Performance",
        prompt="Research inference latency impact of top-k context expansion and response batching in serving pipelines.",
        why_it_matters="The benchmark subset runner fixes top-k context today, but the public tool will need explicit serving tradeoffs.",
        expected_domain="inference-optimization",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "benchmark-lab: MemoryAgentBench subset runs use --topk-context 8",
            "memory-runtime: richer retrieval object and rerank paths increase serving cost",
        ],
    ),
    ResearchPassSpec(
        pass_id="inference-overnight-batch-layout",
        wave=4,
        theme="Performance",
        prompt="Research batching and throughput strategies for overnight benchmark execution on CPU-only hosts.",
        why_it_matters="The overnight batch script became a real operational bottleneck, which means scheduling itself is now a research topic.",
        expected_domain="inference-optimization",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "benchmark-lab: overnight public batch, CPU lane controls, and resume path",
            "benchmark-lab: run_overnight_public_batch.sh",
        ],
    ),
    ResearchPassSpec(
        pass_id="eval-benchmark-drift-candidate-heuristics",
        wave=4,
        theme="Benchmark Fit",
        prompt="Research benchmark drift detection when candidate variants change answer-selection heuristics and compaction rules.",
        why_it_matters="As candidate variants evolve, you need a way to distinguish real progress from benchmark-fit drift.",
        expected_domain="llm-evals",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "benchmark-lab: repeated candidate comparisons across baseline_current, cand80, and cand119",
            "memory-runtime: many interacting retrieval and compaction heuristics",
        ],
    ),
    ResearchPassSpec(
        pass_id="eval-human-audit-sampling",
        wave=4,
        theme="Benchmark Fit",
        prompt="Research human label audit sampling for benchmark runs where judge-model decisions are uncertain.",
        why_it_matters="This is the practical follow-up to judge-model calibration when you want a trustworthy public benchmark story.",
        expected_domain="llm-evals",
        expected_initial_outcome="gap_fill",
        source_signals=[
            "benchmark-lab: local judge usage on overnight runs",
            "registry-example: llm-evals specialist domain already carries audit-sampling patterns",
        ],
    ),
)


def load_research_pass_suite() -> list[ResearchPassSpec]:
    return [spec.model_copy(deep=True) for spec in RESEARCH_PASS_SPECS]


def routing_check(specs: list[ResearchPassSpec]) -> list[dict[str, str | None]]:
    rows: list[dict[str, str | None]] = []
    for spec in specs:
        actual_domain = specialized_domain_for_prompt(spec.prompt)
        rows.append(
            {
                "pass_id": spec.pass_id,
                "expected_domain": spec.expected_domain,
                "actual_domain": actual_domain,
                "status": "ok" if actual_domain == spec.expected_domain else "mismatch",
            }
        )
    return rows


def render_summary(specs: list[ResearchPassSpec]) -> str:
    wave_counts = Counter(spec.wave for spec in specs)
    domain_counts = Counter(spec.expected_domain or "generic" for spec in specs)
    lines = [
        f"passes={len(specs)}",
        "waves=" + ", ".join(f"{wave}:{count}" for wave, count in sorted(wave_counts.items())),
        "domains=" + ", ".join(f"{domain}:{count}" for domain, count in sorted(domain_counts.items())),
    ]
    return "\n".join(lines)


def render_markdown(specs: list[ResearchPassSpec]) -> str:
    lines = [
        "# Example Research Pass Suite",
        "",
        "This suite is a generic example corpus for long-memory benchmark, retrieval, and productization research.",
        "",
    ]
    for wave in sorted({spec.wave for spec in specs}):
        lines.append(f"## Wave {wave}")
        lines.append("")
        for spec in [item for item in specs if item.wave == wave]:
            lines.append(f"### {spec.pass_id}")
            lines.append("")
            lines.append(f"- Theme: {spec.theme}")
            lines.append(f"- Expected domain: {spec.expected_domain or 'generic'}")
            lines.append(f"- Expected first outcome: {spec.expected_initial_outcome}")
            lines.append(f"- Prompt: {spec.prompt}")
            lines.append(f"- Why it matters: {spec.why_it_matters}")
            lines.append(f"- Source signals: {'; '.join(spec.source_signals)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect the example research pass suite for long-memory project workflows.")
    parser.add_argument(
        "--format",
        choices=["summary", "markdown", "json"],
        default="summary",
        help="Output format for the suite.",
    )
    parser.add_argument(
        "--wave",
        type=int,
        action="append",
        default=None,
        help="Restrict output to one or more waves.",
    )
    parser.add_argument(
        "--domain",
        action="append",
        default=None,
        help="Restrict output to one or more expected domains.",
    )
    parser.add_argument(
        "--check-routing",
        action="store_true",
        help="Verify that the current implicit routing classifies each prompt into its expected domain.",
    )
    args = parser.parse_args()

    specs = load_research_pass_suite()
    if args.wave:
        allowed_waves = set(args.wave)
        specs = [spec for spec in specs if spec.wave in allowed_waves]
    if args.domain:
        allowed_domains = set(args.domain)
        specs = [spec for spec in specs if (spec.expected_domain or "generic") in allowed_domains]

    if args.check_routing:
        rows = routing_check(specs)
        mismatches = [row for row in rows if row["status"] != "ok"]
        payload = {
            "passes": len(specs),
            "mismatches": mismatches,
            "ok": len(mismatches) == 0,
        }
        print(json.dumps(payload, indent=2))
        if mismatches:
            raise SystemExit(1)
        return

    if args.format == "summary":
        print(render_summary(specs))
        return
    if args.format == "markdown":
        print(render_markdown(specs), end="")
        return
    print(json.dumps([spec.model_dump(mode="json") for spec in specs], indent=2))


if __name__ == "__main__":
    main()
