from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Iterable


_MAX_LOG_BYTES = 5 * 1024 * 1024
_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+\S+"),
    re.compile(r"(?i)\bx-api-key\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(?:password|token|secret)\s*[:=]\s*\S+"),
)


@dataclass(frozen=True)
class SensitiveLogFinding:
    kind: str
    line_number: int
    fingerprint: str


def scan_log_text(
    value: str | Path,
    *,
    forbidden_values: Iterable[str] = (),
) -> tuple[SensitiveLogFinding, ...]:
    """Scan bounded log text without returning the sensitive matching value."""
    text = _read_bounded(value)
    forbidden = tuple(
        marker for marker in forbidden_values if isinstance(marker, str) and marker
    )
    findings: list[SensitiveLogFinding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if any(pattern.search(line) for pattern in _CREDENTIAL_PATTERNS):
            findings.append(
                _finding("credential_pattern", line_number, line)
            )
        if any(marker in line for marker in forbidden):
            findings.append(_finding("forbidden_value", line_number, line))
    return tuple(findings)


def _read_bounded(value: str | Path) -> str:
    if isinstance(value, Path):
        if value.stat().st_size > _MAX_LOG_BYTES:
            raise ValueError("log input exceeds the 5 MiB limit")
        return value.read_text(encoding="utf-8", errors="replace")
    encoded = value.encode("utf-8")
    if len(encoded) > _MAX_LOG_BYTES:
        raise ValueError("log input exceeds the 5 MiB limit")
    return value


def _finding(kind: str, line_number: int, line: str) -> SensitiveLogFinding:
    return SensitiveLogFinding(
        kind=kind,
        line_number=line_number,
        fingerprint=sha256(line.encode("utf-8")).hexdigest()[:16],
    )
