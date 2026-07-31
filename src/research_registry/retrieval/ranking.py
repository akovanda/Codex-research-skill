from __future__ import annotations

from dataclasses import dataclass


RANKING_PROFILE_VERSION = "lexical-v1"
_WEIGHTS = {
    "exact": 0.30,
    "lexical": 0.30,
    "scope": 0.12,
    "relationship": 0.10,
    "review": 0.06,
    "trust": 0.05,
    "freshness": 0.04,
    "conflict": 0.02,
    "recency": 0.01,
}


@dataclass(frozen=True)
class RankingResult:
    score: float
    components: dict[str, float]
    matched_by: tuple[str, ...]


def rank_result(
    *,
    exact: float,
    lexical: float,
    scope: float,
    relationship: float,
    review_state: str | None,
    trust_tier: str | None,
    freshness: str | None,
    conflict_state: str | None,
    status: str | None,
    matched_by: tuple[str, ...],
) -> RankingResult:
    review = (
        -1.0
        if status == "rejected"
        else {"reviewed": 1.0, "flagged": -0.5}.get(review_state, 0.0)
    )
    trust = {"high": 1.0, "medium": 0.4, "low": -0.5}.get(
        trust_tier, 0.0
    )
    freshness_score = {
        "fresh": 1.0,
        "needs_refresh": -0.5,
        "stale": -1.0,
    }.get(freshness, 0.0)
    conflict = {
        "conflicted": -1.0,
        "resolved": 0.25,
    }.get(conflict_state, 0.0)
    components = {
        "exact": _unit(exact),
        "lexical": _unit(lexical),
        "scope": _unit(scope),
        "relationship": _unit(relationship),
        "review": _signed(review),
        "trust": _signed(trust),
        "freshness": _signed(freshness_score),
        "conflict": _signed(conflict),
        "recency": 0.0,
    }
    score = sum(
        _WEIGHTS[name] * value for name, value in components.items()
    )
    explanations = list(matched_by)
    if review_state == "reviewed":
        explanations.append("review state: reviewed")
    elif status == "rejected":
        explanations.append("state penalty: rejected")
    if freshness in {"stale", "needs_refresh"}:
        explanations.append(f"freshness penalty: {freshness}")
    if conflict_state == "conflicted":
        explanations.append("conflict penalty: conflicted")
    return RankingResult(
        score=round(max(0.0, min(1.0, score)), 6),
        components={name: round(value, 6) for name, value in components.items()},
        matched_by=tuple(dict.fromkeys(explanations)),
    )


def _unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _signed(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))
