from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from research_registry.application.deposit import DepositError
from research_registry.application.postgres_errors import (
    postgres_deposit_error_message,
)
from tests.test_v2_deposit import _bundle, _service


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            psycopg.errors.SerializationFailure("private serialization detail"),
            "CONCURRENT_WRITE_CONFLICT",
        ),
        (
            psycopg.errors.DeadlockDetected("private deadlock detail"),
            "CONCURRENT_WRITE_CONFLICT",
        ),
        (
            psycopg.errors.LockNotAvailable("private lock detail"),
            "CONCURRENT_WRITE_CONFLICT",
        ),
        (
            psycopg.errors.UniqueViolation("private unique detail"),
            "DATABASE_UNIQUENESS_CONFLICT",
        ),
        (
            psycopg.errors.ForeignKeyViolation("private foreign key detail"),
            "DATABASE_INTEGRITY_ERROR",
        ),
        (
            psycopg.errors.CheckViolation("private check detail"),
            "DATABASE_INTEGRITY_ERROR",
        ),
        (
            psycopg.errors.NotNullViolation("private not-null detail"),
            "DATABASE_INTEGRITY_ERROR",
        ),
        (
            psycopg.errors.UndefinedTable("private table detail"),
            "DATABASE_SCHEMA_ERROR",
        ),
        (
            psycopg.errors.UndefinedColumn("private column detail"),
            "DATABASE_SCHEMA_ERROR",
        ),
        (
            psycopg.errors.SyntaxError("private SQL detail"),
            "DATABASE_SCHEMA_ERROR",
        ),
        (
            psycopg.errors.QueryCanceled("private cancellation detail"),
            "DATABASE_OPERATION_CANCELLED",
        ),
        (
            psycopg.errors.AdminShutdown("private shutdown detail"),
            "DATABASE_UNAVAILABLE",
        ),
        (
            psycopg.errors.DiskFull("private disk detail"),
            "DATABASE_RESOURCE_EXHAUSTED",
        ),
        (
            psycopg.errors.InvalidTransactionState("private transaction detail"),
            "DATABASE_TRANSACTION_ERROR",
        ),
        (
            psycopg.errors.InternalError_("private internal detail"),
            "DATABASE_INTERNAL_ERROR",
        ),
        (
            psycopg.ProgrammingError("private client programming detail"),
            "DATABASE_SCHEMA_ERROR",
        ),
        (
            psycopg.IntegrityError("private client integrity detail"),
            "DATABASE_INTEGRITY_ERROR",
        ),
        (
            psycopg.OperationalError("private client operational detail"),
            "DATABASE_UNAVAILABLE",
        ),
        (
            psycopg.DatabaseError("private generic database detail"),
            "DATABASE_OPERATION_FAILED",
        ),
    ],
)
def test_postgres_errors_receive_specific_safe_diagnostics(
    error: BaseException,
    code: str,
) -> None:
    message = postgres_deposit_error_message(error)

    assert message is not None
    assert message.startswith(f"{code}:")
    assert "private" not in message
    assert "SELECT" not in message


def test_non_psycopg_errors_are_not_translated() -> None:
    assert postgres_deposit_error_message(RuntimeError("sentinel")) is None


def test_deposit_translates_schema_failure_and_discards_staged_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, blobs, deposits = _service(tmp_path)

    def fail(*args, **kwargs):
        raise psycopg.errors.UndefinedTable(
            "SELECT private_value FROM private_missing_table"
        )

    monkeypatch.setattr(deposits, "_deposit_transaction", fail)

    with pytest.raises(DepositError) as raised:
        deposits.deposit(_bundle(key="postgres-schema-error"))

    message = str(raised.value)
    assert message.startswith("DATABASE_SCHEMA_ERROR:")
    assert "private_value" not in message
    assert "private_missing_table" not in message
    assert blobs.inspect([]).stored_objects == 0


def test_deposit_does_not_swallow_non_database_programming_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, deposits = _service(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("non-database-sentinel")

    monkeypatch.setattr(deposits, "_deposit_transaction", fail)

    with pytest.raises(RuntimeError, match="non-database-sentinel"):
        deposits.deposit(_bundle(key="non-database-error"))
