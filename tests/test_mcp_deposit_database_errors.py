from __future__ import annotations

from pathlib import Path

import pytest

from research_registry.application.deposit import DepositError
from research_registry.mcp.write_runtime import WriteMcpRuntime
from tests.fixtures.v2_review import seed_review_registry
from tests.test_v2_deposit import _bundle


def test_mcp_deposit_preserves_specific_safe_database_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _ = seed_review_registry(tmp_path, key="mcp-db-error")
    runtime = WriteMcpRuntime(registry, service=registry)
    assert runtime.deposits is not None

    def fail(*args, **kwargs):
        raise DepositError(
            "DATABASE_SCHEMA_ERROR: The deposit database schema or statement "
            "is incompatible."
        )

    monkeypatch.setattr(runtime.deposits, "deposit", fail)

    with pytest.raises(DepositError) as raised:
        runtime.research_deposit(
            _bundle(key="mcp-safe-db-error"),
            ctx=None,
        )

    message = str(raised.value)
    assert message.startswith("DATABASE_SCHEMA_ERROR:")
    assert "SELECT" not in message
    assert "private" not in message


def test_mcp_deposit_redacts_unclassified_internal_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _ = seed_review_registry(tmp_path, key="mcp-internal-error")
    runtime = WriteMcpRuntime(registry, service=registry)
    assert runtime.deposits is not None

    def fail(*args, **kwargs):
        raise RuntimeError(
            "SELECT private_value FROM private_internal_table"
        )

    monkeypatch.setattr(runtime.deposits, "deposit", fail)

    with pytest.raises(RuntimeError) as raised:
        runtime.research_deposit(
            _bundle(key="mcp-redacted-internal-error"),
            ctx=None,
        )

    message = str(raised.value)
    assert message == "DEPOSIT_FAILED: The deposit could not be committed."
    assert "private_value" not in message
    assert "private_internal_table" not in message
