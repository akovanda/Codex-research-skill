from __future__ import annotations

from dataclasses import dataclass

from ..domain.evidence import EvidenceAmbiguous, EvidenceUnresolved


@dataclass(frozen=True)
class ReanchorResult:
    start: int
    end: int
    start_line: int
    end_line: int


def reanchor_text(
    text: str,
    *,
    exact: str,
    prefix: str | None = None,
    suffix: str | None = None,
) -> ReanchorResult:
    """Resolve exact evidence with optional literal context, never fuzzily."""
    if not isinstance(text, str) or not isinstance(exact, str) or not exact:
        raise EvidenceUnresolved("exact evidence does not resolve")
    matches: list[tuple[int, int]] = []
    offset = 0
    while offset <= len(text) - len(exact):
        start = text.find(exact, offset)
        if start < 0:
            break
        end = start + len(exact)
        if (
            (prefix is None or text[:start].endswith(prefix))
            and (suffix is None or text[end:].startswith(suffix))
        ):
            matches.append((start, end))
            if len(matches) > 1:
                raise EvidenceAmbiguous("exact evidence resolves more than once")
        offset = start + 1
    if not matches:
        raise EvidenceUnresolved("exact evidence does not resolve")
    start, end = matches[0]
    start_line = text.count("\n", 0, start) + 1
    end_line = text.count("\n", 0, max(start, end - 1)) + 1
    return ReanchorResult(
        start=start,
        end=end,
        start_line=start_line,
        end_line=end_line,
    )
