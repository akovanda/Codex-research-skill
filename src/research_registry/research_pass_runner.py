from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from .research_capture import run_implicit_research_capture
from .research_pass_suite import ResearchPassSpec, load_research_pass_suite
from .service import RegistryService


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
    stored_source_count: int
    stored_excerpt_count: int
    stored_claim_count: int
    stored_report: bool
    stored_question_id: str | None
    stored_session_id: str | None
    stored_report_id: str | None
    narrative_preview: str | None = None


class ResearchPassRoundSummary(BaseModel):
    round_index: int
    total_passes: int
    domain_counts: dict[str, int]
    mode_counts: dict[str, int]
    contract_pass_rate: float
    reused_records: int
    stored_reports: int
    insufficient_evidence_passes: int


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
    source_contexts: list[str]
    executions: list[ResearchPassExecution]
    round_summaries: list[ResearchPassRoundSummary]
    transitions: list[ResearchPassTransition] = Field(default_factory=list)


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
    return service


def execute_passes(
    service: RegistryService,
    specs: list[ResearchPassSpec],
    *,
    rounds: int = 2,
    source_roots: list[Path] | None = None,
) -> ResearchPassRunReport:
    executions: list[ResearchPassExecution] = []
    round_summaries: list[ResearchPassRoundSummary] = []

    for round_index in range(1, rounds + 1):
        round_rows: list[ResearchPassExecution] = []
        for spec in specs:
            outcome = run_implicit_research_capture(
                spec.prompt,
                backend=service,
                source_signals=spec.source_signals,
                source_roots=source_roots,
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
                stored_source_count=len(outcome.capture_summary.stored_source_ids),
                stored_excerpt_count=len(outcome.capture_summary.stored_excerpt_ids),
                stored_claim_count=len(outcome.capture_summary.stored_claim_ids),
                stored_report=outcome.capture_summary.stored_report_id is not None,
                stored_question_id=outcome.capture_summary.stored_question_id,
                stored_session_id=outcome.capture_summary.stored_session_id,
                stored_report_id=outcome.capture_summary.stored_report_id,
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
                insufficient_evidence_passes=sum(1 for row in round_rows if row.specialist_mode == "insufficient_evidence"),
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
        db_path=service.database_label,
        rounds=rounds,
        source_contexts=sorted({signal.split(":", 1)[0] for spec in specs for signal in spec.source_signals if ":" in signal}),
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
        f"- Source contexts: {', '.join(report.source_contexts)}",
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
        lines.append(f"- Insufficient-evidence passes: {summary.insufficient_evidence_passes}")
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
        lines.append(f"- Stored sources: {row.stored_source_count}")
        lines.append(f"- Stored excerpts: {row.stored_excerpt_count}")
        lines.append(f"- Stored claims: {row.stored_claim_count}")
        lines.append(f"- Stored report: {row.stored_report}")
        lines.append(f"- Stored question id: {row.stored_question_id or 'none'}")
        lines.append(f"- Stored session id: {row.stored_session_id or 'none'}")
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
        help="Delete the existing runner database before execution.",
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
        help="How to print the report to stdout.",
    )
    args = parser.parse_args()

    service = build_seeded_service(Path(args.db_path), reset=args.reset)
    specs = load_research_pass_suite()
    report = execute_passes(service, specs, rounds=args.rounds)

    if args.json_out:
        json_path = Path(args.json_out)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    if args.markdown_out:
        markdown_path = Path(args.markdown_out)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_report_markdown(report), encoding="utf-8")

    if args.format == "json":
        print(report.model_dump_json(indent=2))
    elif args.format == "markdown":
        print(render_report_markdown(report), end="")
    else:
        latest = report.round_summaries[-1]
        print(f"rounds={report.rounds} passes={latest.total_passes} mode_counts={json.dumps(latest.mode_counts, sort_keys=True)}")
        print(f"contract_pass_rate={latest.contract_pass_rate:.2f} reused={latest.reused_records} stored_reports={latest.stored_reports}")


if __name__ == "__main__":
    main()
