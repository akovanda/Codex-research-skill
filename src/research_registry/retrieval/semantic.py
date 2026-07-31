from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class SemanticRecord:
    id: str
    text: str


@dataclass(frozen=True)
class SemanticCandidate:
    id: str
    score: float
    provider: str
    provider_version: str


class SemanticIndex(Protocol):
    """Optional provider seam; deterministic lexical retrieval remains complete."""

    def index(self, records: Sequence[SemanticRecord]) -> None: ...

    def search(
        self,
        query: str,
        limit: int,
    ) -> Sequence[SemanticCandidate]: ...
