from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from .memory_retrieval_skill import optimization_gap_fill_bundle
from .research_capture import run_implicit_research_capture
from .research_pass_suite import ResearchPassSpec, load_research_pass_suite
from .seed_memory_retrieval import seed_memory_retrieval
from .service import RegistryService
from .specialist_domains import (
    inference_optimization_gap_fill_bundle,
    llm_evals_gap_fill_bundle,
    seed_specialist_domains,
)


class ResearchPassExecution(BaseModel):
    round_index: int
    pass_id: str
    theme: str
    prompt: str
    expected_domain: str | None
    expected_initial_outcome: str
    actual_domain: str | None
    specialized_skill: str | None
    specialist_mode: str | None
    summary_contract_passed: bool | None
    reused_record_count: int
    stored_annotation_count: int
    stored_finding_count: int
    stored_report: bool
    queued: bool
    stored_run_id: str | None
    stored_report_id: str | None
    queued_bundle_id: str | None
    narrative_preview: str | None = None


class ResearchPassRoundSummary(BaseModel):
    round_index: int
    total_passes: int
    domain_counts: dict[str, int]
    mode_counts: dict[str, int]
    contract_pass_rate: float
    reused_records: int
    stored_reports: int
    queued_passes: int


class ResearchPassTransition(BaseModel):
    pass_id: str
    round_1_mode: str | None
    round_2_mode: str | None
    reused_on_round_1: bool
    reused_on_round_2: bool
    mode_changed: bool


class ResearchPassRunReport(BaseModel):
    generated_at: datetime
    db_path: str
    rounds: int
    seeded_domains: list[str]
    executions: list[ResearchPassExecution]
    round_summaries: list[ResearchPassRoundSummary]
    transitions: list[ResearchPassTransition] = Field(default_factory=list)


def _gap_fill_for_domain(domain: str | None):
    if domain == "memory-retrieval":
        return optimization_gap_fill_bundle()
    if domain == "inference-optimization":
        return inference_optimization_gap_fill_bundle()
    if domain == "llm-evals":
        return llm_evals_gap_fill_bundle()
    return None


def _preview(summary_md: str | None) -> str | None:
    if not summary_md:
        return None
    lines = [line.strip() for line in summary_md.splitlines() if line.strip()]
    for line in lines:
        if not line.startswith("#") and not line.startswith("##"):
            return line
    return lines[0] if lines else None


def build_seeded_service(db_path: Path, *, reset: bool = False) -> RegistryService:
    if reset and db_path.exists():
        db_path.unlink()
    service = RegistryService(db_path)
    service.initialize()
    seed_memory_retrieval(service)
    seed_specialist_domains(service)
    return service


def execute_passes(
    service: RegistryService,
    specs: list[ResearchPassSpec],
    *,
    rounds: int = 2,
) -> ResearchPassRunReport:
    executions: list[ResearchPassExecution] = []
    round_summaries: list[ResearchPassRoundSummary] = []

    for round_index in range(1, rounds + 1):
        round_rows: list[ResearchPassExecution] = []
        for spec in specs:
            gap_fill = None
            if round_index == 1 and spec.expected_initial_outcome == "gap_fill":
                gap_fill = _gap_fill_for_domain(spec.expected_domain)
            outcome = run_implicit_research_capture(
                spec.prompt,
                backend=service,
                gap_fill=gap_fill,
            )
            row = ResearchPassExecution(
                round_index=round_index,
                pass_id=spec.pass_id,
                theme=spec.theme,
                prompt=spec.prompt,
                expected_domain=spec.expected_domain,
                expected_initial_outcome=spec.expected_initial_outcome,
                actual_domain=outcome.specialized_domain,
                specialized_skill=outcome.specialized_skill,
                specialist_mode=outcome.specialist_mode,
                summary_contract_passed=outcome.summary_contract_passed,
                reused_record_count=len(outcome.capture_summary.reused_record_ids),
                stored_annotation_count=len(outcome.capture_summary.stored_annotation_ids),
                stored_finding_count=len(outcome.capture_summary.stored_finding_ids),
                stored_report=outcome.capture_summary.stored_report_id is not None,
                queued=outcome.capture_summary.queued_bundle_id is not None,
                stored_run_id=outcome.capture_summary.stored_run_id,
                stored_report_id=outcome.capture_summary.stored_report_id,
                queued_bundle_id=outcome.capture_summary.queued_bundle_id,
                narrative_preview=_preview(outcome.narrative_summary_md),
            )
            executions.append(row)
            round_rows.append(row)

        mode_counts = Counter((row.specialist_mode or "none") for row in round_rows)
        domain_counts = Counter((row.actual_domain or "generic") for row in round_rows)
        contract_passes = sum(1 for row in round_rows if row.summary_contract_passed)
        round_summaries.append(
            ResearchPassRoundSummary(
                round_index=round_index,
                total_passes=len(round_rows),
                domain_counts=dict(sorted(domain_counts.items())),
                mode_counts=dict(sorted(mode_counts.items())),
                contract_pass_rate=contract_passes / len(round_rows) if round_rows else 0.0,
                reused_records=sum(row.reused_record_count for row in round_rows),
                stored_reports=sum(1 for row in round_rows if row.stored_report),
                queued_passes=sum(1 for row in round_rows if row.queued),
            )
        )

    transitions: list[ResearchPassTransition] = []
    if rounds >= 2:
        by_pass: dict[str, list[ResearchPassExecution]] = {}
        for row in executions:
            by_pass.setdefault(row.pass_id, []).append(row)
        for pass_id, rows in sorted(by_pass.items()):
            rows.sort(key=lambda item: item.round_index)
            first = rows[0]
            second = rows[1] if len(rows) > 1 else rows[0]
            transitions.append(
                ResearchPassTransition(
                    pass_id=pass_id,
                    round_1_mode=first.specialist_mode,
                    round_2_mode=second.specialist_mode,
                    reused_on_round_1=first.reused_record_count > 0,
                    reused_on_round_2=second.reused_record_count > 0,
                    mode_changed=first.specialist_mode != second.specialist_mode,
                )
            )

    return ResearchPassRunReport(
        generated_at=datetime.now(timezone.utc),
        db_path=str(service.db_path),
        rounds=rounds,
        seeded_domains=["memory-retrieval", "inference-optimization", "llm-evals"],
        executions=executions,
        round_summaries=round_summaries,
        transitions=transitions,
    )


def render_report_markdown(report: ResearchPassRunReport) -> str:
    lines = [
        "# Research Pass Runner Report",
        "",
        f"- Generated at: {report.generated_at.isoformat()}",
        f"- Database: `{report.db_path}`",
        f"- Rounds: {report.rounds}",
        f"- Seeded domains: {', '.join(report.seeded_domains)}",
        "",
        "## Round Summaries",
        "",
    ]
    for summary in report.round_summaries:
        lines.append(f"### Round {summary.round_index}")
        lines.append("")
        lines.append(f"- Total passes: {summary.total_passes}")
        lines.append(f"- Domain counts: {json.dumps(summary.domain_counts, sort_keys=True)}")
        lines.append(f"- Mode counts: {json.dumps(summary.mode_counts, sort_keys=True)}")
        lines.append(f"- Summary contract pass rate: {summary.contract_pass_rate:.2f}")
        lines.append(f"- Reused record count: {summary.reused_records}")
        lines.append(f"- Stored reports: {summary.stored_reports}")
        lines.append(f"- Queued passes: {summary.queued_passes}")
        lines.append("")
    if report.transitions:
        lines.append("## Round 1 -> Round 2 Transitions")
        lines.append("")
        changed = [item for item in report.transitions if item.mode_changed]
        reused_later = [item for item in report.transitions if not item.reused_on_round_1 and item.reused_on_round_2]
        lines.append(f"- Mode changes: {len(changed)}")
        lines.append(f"- New reuse on round 2: {len(reused_later)}")
        lines.append("")
        for item in report.transitions:
            lines.append(
                f"- `{item.pass_id}`: {item.round_1_mode or 'none'} -> {item.round_2_mode or 'none'}"
                f" | reuse {item.reused_on_round_1} -> {item.reused_on_round_2}"
            )
        lines.append("")
    lines.append("## Pass Results")
    lines.append("")
    for row in report.executions:
        lines.append(f"### Round {row.round_index} / {row.pass_id}")
        lines.append("")
        lines.append(f"- Expected domain: {row.expected_domain or 'generic'}")
        lines.append(f"- Actual domain: {row.actual_domain or 'generic'}")
        lines.append(f"- Mode: {row.specialist_mode or 'none'}")
        lines.append(f"- Reused records: {row.reused_record_count}")
        lines.append(f"- Stored report: {row.stored_report}")
        lines.append(f"- Stored run id: {row.stored_run_id or 'none'}")
        lines.append(f"- Stored report id: {row.stored_report_id or 'none'}")
        lines.append(f"- Summary contract passed: {row.summary_contract_passed}")
        if row.narrative_preview:
            lines.append(f"- Preview: {row.narrative_preview}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the grounded research pass suite against the local Research Registry.")
    parser.add_argument(
        "--db-path",
        default=".data/research-pass-runner.sqlite3",
        help="SQLite database path for the pass runner.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=2,
        help="How many sequential rounds to execute against the same registry.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the existing runner database before seeding and execution.",
    )
    parser.add_argument(
        "--json-out",
        help="Optional path for a JSON report.",
    )
    parser.add_argument(
        "--markdown-out",
        help="Optional path for a markdown report.",
    )
    parser.add_argument(
        "--format",
        choices=["summary", "json", "markdown"],
        default="summary",
        help="Console output format.",
    )
    args = parser.parse_args()

    specs = load_research_pass_suite()
    service = build_seeded_service(Path(args.db_path), reset=args.reset)
    report = execute_passes(service, specs, rounds=args.rounds)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report.model_dump(mode="json"), indent=2) + "\n")
    if args.markdown_out:
        Path(args.markdown_out).write_text(render_report_markdown(report))

    if args.format == "json":
        print(json.dumps(report.model_dump(mode="json"), indent=2))
        return
    if args.format == "markdown":
        print(render_report_markdown(report), end="")
        return

    for summary in report.round_summaries:
        print(f"round={summary.round_index}")
        print(f"total_passes={summary.total_passes}")
        print(f"domain_counts={json.dumps(summary.domain_counts, sort_keys=True)}")
        print(f"mode_counts={json.dumps(summary.mode_counts, sort_keys=True)}")
        print(f"contract_pass_rate={summary.contract_pass_rate:.2f}")
        print(f"reused_records={summary.reused_records}")
        print(f"stored_reports={summary.stored_reports}")
        print(f"queued_passes={summary.queued_passes}")
    if report.transitions:
        changed = sum(1 for item in report.transitions if item.mode_changed)
        reused_later = sum(1 for item in report.transitions if not item.reused_on_round_1 and item.reused_on_round_2)
        print(f"mode_changes={changed}")
        print(f"new_reuse_on_round_2={reused_later}")


if __name__ == "__main__":
    main()
