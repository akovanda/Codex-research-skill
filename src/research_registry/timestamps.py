from __future__ import annotations

from datetime import datetime, timezone


def as_utc(value: datetime) -> datetime:
    """Return an instant in UTC; compatibility-naive values mean UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_text(value: datetime) -> str:
    return as_utc(value).isoformat()


def parse_utc(value: str) -> datetime:
    return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def is_due(value: str | None, *, now: str) -> bool:
    return value is not None and parse_utc(value) <= parse_utc(now)


def freshness_case(column: str, *, dialect: str) -> str:
    if dialect == "sqlite":
        parsed = f"julianday({column})"
        now = "julianday(?)"
    elif dialect == "postgres":
        parsed = (
            f"(CASE WHEN {column} ~ '(Z|[+-][0-9]{{2}}:[0-9]{{2}})$' "
            f"THEN {column}::timestamptz "
            f"ELSE {column}::timestamp AT TIME ZONE 'UTC' END)"
        )
        now = "?::timestamptz"
    else:  # pragma: no cover
        raise ValueError(f"unsupported database dialect: {dialect}")
    return (
        f"CASE WHEN {column} IS NULL THEN 'unknown' "
        f"WHEN {parsed} IS NULL THEN 'unknown' "
        f"WHEN {parsed} <= {now} THEN 'needs_refresh' "
        "ELSE 'fresh' END"
    )
