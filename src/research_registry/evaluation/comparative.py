from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


COMPARATIVE_MODES = (
    "memory_only",
    "registry_only",
    "both",
    "research_again",
)
_MAX_CORPUS_BYTES = 5 * 1024 * 1024
_MAX_CASES = 5_000
_TOP_LEVEL_FIELDS = {"protocol", "cases"}
_CASE_FIELDS = {"id", "modes"}
_MODE_FIELDS = {
    "correct_prior_finding",
    "expected_citations",
    "resolved_citations",
    "tool_calls",
    "context_bytes",
    "latency_ms",
    "user_corrections",
    "repeated_research_avoided",
}


@dataclass(frozen=True)
class ComparativeModeResult:
    correct_prior_finding_rate: float
    evidence_resolution_rate: float
    average_tool_calls: float
    average_context_bytes: float
    average_latency_ms: float
    average_user_corrections: float
    repeated_research_avoided_rate: float

    def to_dict(self) -> dict[str, float]:
        return {
            "correct_prior_finding_rate": self.correct_prior_finding_rate,
            "evidence_resolution_rate": self.evidence_resolution_rate,
            "average_tool_calls": self.average_tool_calls,
            "average_context_bytes": self.average_context_bytes,
            "average_latency_ms": self.average_latency_ms,
            "average_user_corrections": self.average_user_corrections,
            "repeated_research_avoided_rate": (
                self.repeated_research_avoided_rate
            ),
        }


@dataclass(frozen=True)
class ComparativeEvaluationResult:
    protocol: str
    case_count: int
    modes: dict[str, ComparativeModeResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "case_count": self.case_count,
            "modes": {
                mode: result.to_dict()
                for mode, result in self.modes.items()
            },
        }


def run_comparative_evaluation(
    corpus_path: str | Path,
) -> ComparativeEvaluationResult:
    """Score caller-recorded outcomes; this function never runs an agent."""
    payload = _load_comparative_corpus(Path(corpus_path))
    mode_results: dict[str, ComparativeModeResult] = {}
    for mode in COMPARATIVE_MODES:
        records = [case["modes"][mode] for case in payload["cases"]]
        expected_citations = sum(
            record["expected_citations"] for record in records
        )
        resolved_citations = sum(
            record["resolved_citations"] for record in records
        )
        mode_results[mode] = ComparativeModeResult(
            correct_prior_finding_rate=_rate(
                [record["correct_prior_finding"] for record in records]
            ),
            evidence_resolution_rate=round(
                resolved_citations / max(1, expected_citations),
                6,
            ),
            average_tool_calls=_average(
                [record["tool_calls"] for record in records]
            ),
            average_context_bytes=_average(
                [record["context_bytes"] for record in records]
            ),
            average_latency_ms=_average(
                [record["latency_ms"] for record in records]
            ),
            average_user_corrections=_average(
                [record["user_corrections"] for record in records]
            ),
            repeated_research_avoided_rate=_rate(
                [
                    record["repeated_research_avoided"]
                    for record in records
                ]
            ),
        )
    return ComparativeEvaluationResult(
        protocol="research-comparative-evaluation/v1",
        case_count=len(payload["cases"]),
        modes=mode_results,
    )


def _load_comparative_corpus(path: Path) -> dict[str, Any]:
    if path.stat().st_size > _MAX_CORPUS_BYTES:
        raise ValueError("comparative corpus exceeds the 5 MiB limit")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("comparative corpus must be an object")
    _reject_unknown(payload, _TOP_LEVEL_FIELDS, "corpus")
    if payload.get("protocol") != "research-comparative-corpus/v1":
        raise ValueError("unsupported comparative corpus protocol")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("comparative corpus requires cases")
    if len(cases) > _MAX_CASES:
        raise ValueError("comparative corpus has too many cases")
    ids: list[str] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"comparative case[{index}] must be an object")
        _reject_unknown(case, _CASE_FIELDS, f"case[{index}]")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"comparative case[{index}] requires an id")
        ids.append(case_id)
        modes = case.get("modes")
        if not isinstance(modes, dict) or set(modes) != set(
            COMPARATIVE_MODES
        ):
            raise ValueError(
                "comparative case modes must be exactly: "
                + ", ".join(COMPARATIVE_MODES)
            )
        for mode, record in modes.items():
            _validate_mode(record, label=f"case[{index}].{mode}")
    if len(ids) != len(set(ids)):
        raise ValueError("comparative case ids must be unique")
    return payload


def _validate_mode(record: Any, *, label: str) -> None:
    if not isinstance(record, dict):
        raise ValueError(f"comparative {label} must be an object")
    _reject_unknown(record, _MODE_FIELDS, label)
    missing = _MODE_FIELDS - set(record)
    if missing:
        raise ValueError(
            f"comparative {label} is missing: " + ", ".join(sorted(missing))
        )
    for field in ("correct_prior_finding", "repeated_research_avoided"):
        if not isinstance(record[field], bool):
            raise ValueError(f"comparative {label}.{field} must be boolean")
    for field in (
        "expected_citations",
        "resolved_citations",
        "tool_calls",
        "context_bytes",
        "user_corrections",
    ):
        if not isinstance(record[field], int) or record[field] < 0:
            raise ValueError(
                f"comparative {label}.{field} must be a non-negative integer"
            )
    latency = record["latency_ms"]
    if not isinstance(latency, int | float) or latency < 0:
        raise ValueError(
            f"comparative {label}.latency_ms must be non-negative"
        )
    if record["resolved_citations"] > record["expected_citations"]:
        raise ValueError(
            f"comparative {label} resolves more citations than expected"
        )


def _average(values: list[int | float]) -> float:
    return round(sum(values) / max(1, len(values)), 6)


def _rate(values: list[bool]) -> float:
    return round(sum(values) / max(1, len(values)), 6)


def _reject_unknown(
    value: dict[str, Any],
    allowed: set[str],
    label: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            f"comparative {label} has unknown field(s): "
            + ", ".join(unknown)
        )
