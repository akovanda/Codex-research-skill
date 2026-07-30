from __future__ import annotations

from typing import Literal


ReviewableEntityKind = Literal[
    "claim_revision",
    "evidence",
    "source_version",
    "report",
    "refresh_item",
]
ReviewAction = Literal[
    "approve",
    "contest",
    "reject",
    "supersede",
    "request_refresh",
    "dismiss_refresh",
]

_ENTITY_ACTIONS: dict[ReviewableEntityKind, frozenset[ReviewAction]] = {
    "claim_revision": frozenset(
        {
            "approve",
            "contest",
            "reject",
            "supersede",
            "request_refresh",
        }
    ),
    "evidence": frozenset({"approve", "contest", "reject", "request_refresh"}),
    "source_version": frozenset(
        {"approve", "contest", "reject", "request_refresh"}
    ),
    "report": frozenset({"approve", "contest", "reject", "request_refresh"}),
    "refresh_item": frozenset({"dismiss_refresh"}),
}


def validate_review_action(
    entity_kind: ReviewableEntityKind,
    action: ReviewAction,
) -> None:
    if action not in _ENTITY_ACTIONS[entity_kind]:
        raise ValueError(f"{action} is not valid for {entity_kind}")


def review_state_after(
    current_state: str,
    action: ReviewAction,
) -> str:
    if action == "approve":
        if current_state == "reviewed":
            raise ValueError("the entity is already reviewed")
        return "reviewed"
    if action in {"contest", "reject"}:
        if current_state == "flagged":
            raise ValueError("the entity is already flagged")
        return "flagged"
    return current_state
