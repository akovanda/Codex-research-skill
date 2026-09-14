from copy import deepcopy
import logging
import re

import pytest

from research_registry.application.deposit import DepositError, ResearchDepositService
from research_registry.mcp.write_runtime import WriteMcpRuntime
from tests.test_v2_deposit import _bundle, _counts, _service


def restricted_runtime(tmp_path):
    registry, blobs, _ = _service(tmp_path)
    runtime = WriteMcpRuntime(registry)
    runtime.deposits = ResearchDepositService(
        registry.database, blobs, max_snapshot_policy="evidence_only"
    )
    return registry, blobs, runtime


@pytest.mark.parametrize("validate_only", [True, False])
def test_retention_rejection_is_named_and_rolls_back(tmp_path, validate_only):
    registry, blobs, runtime = restricted_runtime(tmp_path)
    before = _counts(registry)
    with pytest.raises(DepositError, match="SNAPSHOT_POLICY_DENIED"):
        runtime.research_deposit(_bundle(validate_only=validate_only), ctx=None)
    assert _counts(registry) == before
    assert not list(blobs.root.rglob("[0-9a-f]" * 64))
    assert not list((blobs.root / ".staging").iterdir())


def test_evidence_only_validate_commit_replay_and_conflict(tmp_path):
    registry, blobs, runtime = restricted_runtime(tmp_path)
    request = _bundle(validate_only=True)
    request["sources"][0]["version"]["snapshot"]["policy"] = "evidence_only"
    before = _counts(registry)
    validation = runtime.research_deposit(request, ctx=None)
    assert validation.status == "validated" and not validation.committed
    assert _counts(registry) == before
    request["validate_only"] = False
    committed = runtime.research_deposit(request, ctx=None)
    replay = runtime.research_deposit(request, ctx=None)
    assert validation.request_sha256 == committed.request_sha256
    assert committed.committed and replay.idempotent_replay
    after = _counts(registry)
    assert after["claims"] == before["claims"] + 1
    assert after["content_objects"] == before["content_objects"]
    assert not list((blobs.root / ".staging").iterdir())
    changed = deepcopy(request)
    changed["claims"][0]["title"] = "Changed content, identical key"
    with pytest.raises(DepositError):
        runtime.research_deposit(changed, ctx=None)
    assert _counts(registry) == after


def test_namespace_rejection_precedes_transaction(tmp_path):
    registry, _, runtime = restricted_runtime(tmp_path)
    request = _bundle(validate_only=True)
    request["namespace"] = {"kind": "user", "id": "somebody-else"}
    before = _counts(registry)
    with pytest.raises(PermissionError, match="NAMESPACE_ACCESS_DENIED"):
        runtime.research_deposit(request, ctx=None)
    assert _counts(registry) == before


def test_unexpected_error_has_safe_correlated_diagnostic(tmp_path, monkeypatch, caplog):
    _, _, runtime = restricted_runtime(tmp_path)
    sensitive = "SECRET-API-KEY and PRIVATE-REPORT-BODY"
    def fail(*args, **kwargs):
        raise RuntimeError(sensitive)
    monkeypatch.setattr(runtime.deposits, "deposit", fail)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="DEPOSIT_FAILED") as caught:
            runtime.research_deposit(_bundle(validate_only=True), ctx=None)
    message = str(caught.value)
    correlation_id = re.search(r"correlation_id=([0-9a-f]{16})", message).group(1)
    assert correlation_id in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert sensitive not in caplog.text and sensitive not in message
    assert all(record.exc_info is None for record in caplog.records)
