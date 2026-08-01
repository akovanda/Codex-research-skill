from __future__ import annotations

from ..db import psycopg


_CONCURRENT_SQLSTATES = frozenset({"40001", "40P01", "55P03"})
_UNAVAILABLE_SQLSTATES = frozenset({"57P01", "57P02", "57P03"})

_CONCURRENT_MESSAGE = (
    "CONCURRENT_WRITE_CONFLICT: The deposit transaction could not be "
    "completed because of concurrent database activity; retry with the same "
    "idempotency key."
)
_UNIQUENESS_MESSAGE = (
    "DATABASE_UNIQUENESS_CONFLICT: The deposit conflicts with an existing "
    "database identity."
)
_INTEGRITY_MESSAGE = (
    "DATABASE_INTEGRITY_ERROR: The deposit violates a database integrity "
    "constraint."
)
_SCHEMA_MESSAGE = (
    "DATABASE_SCHEMA_ERROR: The deposit database schema or statement is "
    "incompatible."
)
_UNAVAILABLE_MESSAGE = (
    "DATABASE_UNAVAILABLE: The deposit database is unavailable."
)
_CANCELLED_MESSAGE = (
    "DATABASE_OPERATION_CANCELLED: The deposit database operation was "
    "cancelled."
)
_RESOURCE_MESSAGE = (
    "DATABASE_RESOURCE_EXHAUSTED: The deposit database lacks a required "
    "resource."
)
_TRANSACTION_MESSAGE = (
    "DATABASE_TRANSACTION_ERROR: The deposit database transaction entered "
    "an invalid state."
)
_INTERNAL_MESSAGE = (
    "DATABASE_INTERNAL_ERROR: The deposit database reported an internal "
    "failure."
)
_FAILED_MESSAGE = (
    "DATABASE_OPERATION_FAILED: The deposit database operation failed."
)


def postgres_deposit_error_message(exc: BaseException) -> str | None:
    """Return one safe, stable deposit diagnostic for a psycopg exception.

    SQL text, identifiers, values, connection strings, and server-provided
    detail are intentionally excluded. Only errors with explicitly retryable
    SQLSTATEs are reported as concurrent-write conflicts.
    """
    if psycopg is None or not isinstance(exc, psycopg.Error):
        return None

    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate in _CONCURRENT_SQLSTATES:
        return _CONCURRENT_MESSAGE
    if sqlstate == "23505":
        return _UNIQUENESS_MESSAGE
    if isinstance(sqlstate, str) and sqlstate.startswith("23"):
        return _INTEGRITY_MESSAGE
    if sqlstate == "57014":
        return _CANCELLED_MESSAGE
    if sqlstate in _UNAVAILABLE_SQLSTATES or (
        isinstance(sqlstate, str) and sqlstate.startswith("08")
    ):
        return _UNAVAILABLE_MESSAGE
    if isinstance(sqlstate, str) and sqlstate.startswith("42"):
        return _SCHEMA_MESSAGE
    if isinstance(sqlstate, str) and sqlstate.startswith("53"):
        return _RESOURCE_MESSAGE
    if isinstance(sqlstate, str) and (
        sqlstate.startswith("25")
        or sqlstate.startswith("2D")
        or sqlstate.startswith("40")
    ):
        return _TRANSACTION_MESSAGE
    if isinstance(sqlstate, str) and sqlstate.startswith("XX"):
        return _INTERNAL_MESSAGE

    # Some client-side failures do not carry a SQLSTATE. Preserve useful,
    # stable categories without treating every psycopg error as concurrency.
    if isinstance(exc, psycopg.ProgrammingError):
        return _SCHEMA_MESSAGE
    if isinstance(exc, psycopg.IntegrityError):
        return _INTEGRITY_MESSAGE
    if isinstance(exc, psycopg.OperationalError):
        return _UNAVAILABLE_MESSAGE
    return _FAILED_MESSAGE
