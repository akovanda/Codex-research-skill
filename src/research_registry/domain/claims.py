from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..contracts.common import ClaimRevisionStatus


ClaimReviewAction = Literal["approve", "contest", "reject", "supersede"]

_STATUS_TRANSITIONS: dict[ClaimRevisionStatus, frozenset[ClaimRevisionStatus]] = {
    "draft": frozenset({"partial", "supported", "rejected", "superseded"}),
    "partial": frozenset({"supported", "contested", "rejected", "superseded"}),
    "supported": frozenset({"contested", "rejected", "superseded"}),
    "contested": frozenset({"supported", "rejected", "superseded"}),
    "rejected": frozenset({"superseded"}),
    "superseded": frozenset(),
}

V1_CLAIM_STATUS: dict[ClaimRevisionStatus, str] = {
    "supported": "supported",
    "partial": "partial",
    "contested": "conflicted",
    "draft": "insufficient_evidence",
    "rejected": "insufficient_evidence",
    "superseded": "insufficient_evidence",
}


@dataclass(frozen=True)
class ClaimRevisionChange:
    status: ClaimRevisionStatus
    review_state: Literal["unreviewed", "reviewed", "flagged"]
    creates_revision: bool


def plan_claim_review(
    current_status: ClaimRevisionStatus,
    action: ClaimReviewAction,
    *,
    replacement_status: ClaimRevisionStatus | None,
) -> ClaimRevisionChange:
    """Return the only permitted immutable-revision effect for a review action."""
    if current_status == "superseded":
        raise ValueError("superseded claim revisions are terminal")

    if action == "approve":
        if current_status == "rejected" and replacement_status is None:
            raise ValueError("rejected claims require an explicit replacement")
        if replacement_status is None:
            return ClaimRevisionChange(
                status=current_status,
                review_state="reviewed",
                creates_revision=False,
            )
        if replacement_status != current_status:
            _require_status_transition(current_status, replacement_status)
        return ClaimRevisionChange(
            status=replacement_status,
            review_state="reviewed",
            creates_revision=True,
        )

    if action == "contest":
        if replacement_status not in {None, "contested"}:
            raise ValueError("contest revisions must have contested status")
        _require_status_transition(current_status, "contested")
        return ClaimRevisionChange(
            status="contested",
            review_state="flagged",
            creates_revision=True,
        )

    if action == "reject":
        if replacement_status not in {None, "rejected"}:
            raise ValueError("reject revisions must have rejected status")
        _require_status_transition(current_status, "rejected")
        return ClaimRevisionChange(
            status="rejected",
            review_state="flagged",
            creates_revision=True,
        )

    if replacement_status is None:
        raise ValueError("supersede requires a replacement revision")
    if replacement_status == "superseded":
        raise ValueError("a replacement revision cannot already be superseded")
    if "superseded" not in _STATUS_TRANSITIONS[current_status]:
        raise ValueError(f"{current_status} cannot be superseded")
    return ClaimRevisionChange(
        status=replacement_status,
        review_state="unreviewed",
        creates_revision=True,
    )


def _require_status_transition(
    current_status: ClaimRevisionStatus,
    target_status: ClaimRevisionStatus,
) -> None:
    if target_status not in _STATUS_TRANSITIONS[current_status]:
        raise ValueError(
            f"claim status cannot transition from {current_status} to {target_status}"
        )
