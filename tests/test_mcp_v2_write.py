from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from research_registry.mcp.write_runtime import WriteMcpRuntime
from research_registry.mcp_tools import create_mcp_server
from research_registry.models import ApiKeyCreate
from tests.test_v2_deposit import _bundle
from tests.fixtures.v2_review import seed_review_registry


def _call(server, name: str, arguments: dict) -> dict:
    result = asyncio.run(server.call_tool(name, arguments))
    assert isinstance(result, tuple)
    content, structured = result
    assert json.loads(content[0].text) == structured
    return structured


def test_write_tools_are_closed_bounded_and_hide_v1_contracts_by_default(
    tmp_path: Path,
) -> None:
    registry, _ = seed_review_registry(tmp_path, key="mcp-schema")
    server = create_mcp_server(registry, service=registry)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    assert {"research_deposit", "research_review", "research_refresh"} <= set(tools)
    assert {"create_claim", "create_research_bundle", "publish"}.isdisjoint(tools)
    for name in ("research_deposit", "research_review", "research_refresh"):
        tool = tools[name]
        assert tool.parameters["additionalProperties"] is False
        assert tool.output_schema is not None
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is (name == "research_refresh")
    assert tools["research_deposit"].annotations.destructiveHint is False
    visibility_schema = tools["research_deposit"].parameters["properties"][
        "visibility"
    ]
    assert visibility_schema["enum"] == ["private"]
    evidence_schema = tools["research_deposit"].parameters["$defs"][
        "DepositEvidence"
    ]
    assert evidence_schema["additionalProperties"] is False
    assert (
        evidence_schema["properties"]["review_state"]["const"]
        == "unreviewed"
    )
    assert "trust_tier" not in evidence_schema["properties"]
    assert tools["research_refresh"].parameters["properties"]["mode"]["enum"] == [
        "inspect",
        "enqueue",
        "verify",
        "capture",
    ]
    assert (
        tools["research_refresh"]
        .parameters["properties"]["entities"]["maxItems"]
        == 100
    )


def test_mcp_review_and_refresh_translate_to_application_services(
    tmp_path: Path,
) -> None:
    registry, ids = seed_review_registry(tmp_path, key="mcp-write")
    server = create_mcp_server(registry, service=registry)

    reviewed = _call(
        server,
        "research_review",
        {
            "idempotency_key": "mcp-approve",
            "entity": {"kind": "claim_revision", "id": ids["revision"]},
            "action": "approve",
            "expected_revision_id": ids["revision"],
            "expected_state": "unreviewed",
        },
    )
    assert reviewed["status"] == "applied"
    assert reviewed["current_state"]["review_state"] == "reviewed"

    inspected = _call(
        server,
        "research_refresh",
        {
            "mode": "inspect",
            "entities": [{"kind": "source", "id": ids["source"]}],
        },
    )
    assert inspected["status"] == "inspected"
    assert inspected["committed"] is False
    with registry.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM refresh_queue"
        ).fetchone()["count"]
    assert count == 0


def test_remote_write_runtime_requires_admin_and_never_networks(
    tmp_path: Path,
) -> None:
    registry, ids = seed_review_registry(tmp_path, key="mcp-auth")
    runtime = WriteMcpRuntime(
        registry,
        service=registry,
        allow_admin_fallback=False,
    )

    with pytest.raises(PermissionError, match="AUTH_REQUIRED"):
        runtime.research_review(
            idempotency_key="unauthorized-review",
            entity={"kind": "claim_revision", "id": ids["revision"]},
            action="approve",
            expected_revision_id=ids["revision"],
            expected_state="unreviewed",
            note=None,
            new_revision=None,
            ctx=None,
        )
    with pytest.raises(PermissionError, match="AUTH_REQUIRED"):
        runtime.research_refresh(
            mode="enqueue",
            idempotency_key="unauthorized-refresh",
            entities=[{"kind": "claim", "id": ids["claim"]}],
            snapshot_policy=None,
            priority=0.5,
            ctx=None,
        )


def _context(api_key: str):
    return SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(headers={"x-api-key": api_key})
        )
    )


def test_mcp_deposit_local_and_remote_ingest_scope_with_attribution(
    tmp_path: Path,
) -> None:
    registry, _ = seed_review_registry(tmp_path, key="mcp-deposit")
    local = create_mcp_server(registry, service=registry)
    payload = _bundle(key="mcp-local-deposit")
    payload.pop("protocol")

    validated = _call(local, "research_deposit", {**payload, "validate_only": True})
    committed = _call(local, "research_deposit", payload)
    replay = _call(local, "research_deposit", payload)

    assert validated["committed"] is False
    assert committed["committed"] is True
    assert validated["request_sha256"] == committed["request_sha256"]
    assert replay["idempotent_replay"] is True
    local_evidence_id = committed["records"]["evidence_ids"]["evidence"]
    with registry.connect() as conn:
        local_evidence = conn.execute(
            "SELECT review_state, trust_tier, metadata_json "
            "FROM evidence_spans WHERE id = ?",
            (local_evidence_id,),
        ).fetchone()
        local_excerpt_id = json.loads(local_evidence["metadata_json"])[
            "v1_excerpt_id"
        ]
        local_excerpt = conn.execute(
            "SELECT review_state, trust_tier, human_reviewed "
            "FROM excerpts WHERE id = ?",
            (local_excerpt_id,),
        ).fetchone()
    assert (local_evidence["review_state"], local_evidence["trust_tier"]) == (
        "unreviewed",
        "low",
    )
    assert (
        local_excerpt["review_state"],
        local_excerpt["trust_tier"],
        local_excerpt["human_reviewed"],
    ) == ("unreviewed", "low", 0)

    issued = registry.issue_api_key(
        ApiKeyCreate(
            label="deposit-writer",
            actor_user_id="deposit-user",
            namespace_id="deposit-user",
            scopes=["ingest"],
        )
    )
    runtime = WriteMcpRuntime(
        registry,
        service=registry,
        allow_admin_fallback=False,
    )
    remote = _bundle(key="mcp-remote-deposit")
    remote["namespace"] = {"kind": "user", "id": "deposit-user"}
    remote["evidence"][0]["metadata"] = {"suggested_trust_tier": "high"}

    for field, value in (
        ("review_state", "reviewed"),
        ("trust_tier", "high"),
    ):
        attempted = _bundle(key=f"mcp-forged-{field}")
        attempted["namespace"] = {"kind": "user", "id": "deposit-user"}
        attempted["evidence"][0][field] = value
        with pytest.raises(ValidationError):
            runtime.research_deposit(attempted, ctx=_context(issued.token))

    result = runtime.research_deposit(remote, ctx=_context(issued.token))
    assert result.committed is True
    with registry.connect() as conn:
        evidence = conn.execute(
            "SELECT review_state, trust_tier, metadata_json "
            "FROM evidence_spans WHERE id = ?",
            (result.records.evidence_ids["evidence"],),
        ).fetchone()
        excerpt_id = json.loads(evidence["metadata_json"])["v1_excerpt_id"]
        excerpt = conn.execute(
            "SELECT review_state, trust_tier, human_reviewed "
            "FROM excerpts WHERE id = ?",
            (excerpt_id,),
        ).fetchone()
        claim = conn.execute(
            "SELECT actor_user_id, api_key_id FROM claims WHERE id = ?",
            (result.records.claim_ids["claim"],),
        ).fetchone()
        audit = conn.execute(
            "SELECT actor_user_id, api_key_id FROM audit_log "
            "WHERE action = 'research_deposit' AND record_id = ?",
            ("mcp-remote-deposit",),
        ).fetchone()
        review_count = conn.execute(
            "SELECT COUNT(*) AS count FROM review_events"
        ).fetchone()["count"]
    assert (evidence["review_state"], evidence["trust_tier"]) == (
        "unreviewed",
        "low",
    )
    assert json.loads(evidence["metadata_json"])["suggested_trust_tier"] == (
        "high"
    )
    assert (
        excerpt["review_state"],
        excerpt["trust_tier"],
        excerpt["human_reviewed"],
    ) == ("unreviewed", "low", 0)
    assert review_count == 0
    assert claim["actor_user_id"] == "deposit-user"
    assert claim["api_key_id"] == issued.record.id
    assert audit["actor_user_id"] == "deposit-user"
    assert audit["api_key_id"] == issued.record.id

    before_search = _call(
        local,
        "research_search",
        {
            "query": "preserve source-backed evidence",
            "kinds": ["evidence"],
            "include_private": True,
            "limit": 10,
        },
    )
    before_hit = next(
        hit
        for hit in before_search["hits"]
        if hit["id"] == result.records.evidence_ids["evidence"]
    )
    before_get = _call(
        local,
        "research_get",
        {
            "id": result.records.evidence_ids["evidence"],
            "include_private": True,
        },
    )
    before_claim_get = _call(
        local,
        "research_get",
        {
            "id": result.records.claim_ids["claim"],
            "include": ["evidence"],
            "include_private": True,
        },
    )
    assert before_hit["review_state"] == "unreviewed"
    assert before_get["review_state"] == "unreviewed"
    assert before_get["record"]["trust_tier"] == "low"
    assert before_claim_get["includes"]["evidence"][0]["review_state"] == (
        "unreviewed"
    )
    assert before_claim_get["includes"]["evidence"][0]["trust_tier"] == "low"

    reviewer = registry.issue_api_key(
        ApiKeyCreate(
            label="deposit-reviewer",
            actor_user_id="review-user",
            namespace_id="deposit-user",
            scopes=["admin"],
        )
    )
    reviewed = runtime.research_review(
        idempotency_key="approve-remote-evidence",
        entity={
            "kind": "evidence",
            "id": result.records.evidence_ids["evidence"],
        },
        action="approve",
        expected_revision_id=None,
        expected_state="unreviewed",
        note=None,
        new_revision=None,
        ctx=_context(reviewer.token),
    )
    assert reviewed.event_id

    after_search = _call(
        local,
        "research_search",
        {
            "query": "preserve source-backed evidence",
            "kinds": ["evidence"],
            "include_private": True,
            "limit": 10,
        },
    )
    after_hit = next(
        hit
        for hit in after_search["hits"]
        if hit["id"] == result.records.evidence_ids["evidence"]
    )
    after_get = _call(
        local,
        "research_get",
        {
            "id": result.records.evidence_ids["evidence"],
            "include_private": True,
        },
    )
    after_claim_get = _call(
        local,
        "research_get",
        {
            "id": result.records.claim_ids["claim"],
            "include": ["evidence"],
            "include_private": True,
        },
    )
    assert after_hit["review_state"] == "reviewed"
    assert after_get["review_state"] == "reviewed"
    assert after_get["record"]["trust_tier"] == "low"
    assert after_claim_get["includes"]["evidence"][0]["review_state"] == (
        "reviewed"
    )
    assert after_claim_get["includes"]["evidence"][0]["trust_tier"] == "low"


def test_remote_deposit_rejects_missing_scope_and_cross_namespace(
    tmp_path: Path,
) -> None:
    registry, _ = seed_review_registry(tmp_path, key="mcp-deposit-auth")
    no_ingest = registry.issue_api_key(
        ApiKeyCreate(
            label="reader",
            actor_user_id="reader",
            scopes=["read_private"],
        )
    )
    ingest = registry.issue_api_key(
        ApiKeyCreate(
            label="writer",
            actor_user_id="writer",
            scopes=["ingest"],
        )
    )
    runtime = WriteMcpRuntime(
        registry,
        service=registry,
        allow_admin_fallback=False,
    )
    with pytest.raises(PermissionError, match="INSUFFICIENT_SCOPE"):
        runtime.research_deposit(
            _bundle(key="missing-ingest"),
            ctx=_context(no_ingest.token),
        )
    crossing = _bundle(key="crossing-deposit")
    crossing["namespace"] = {"kind": "org", "id": "other"}
    with pytest.raises(PermissionError, match="NAMESPACE_ACCESS_DENIED"):
        runtime.research_deposit(crossing, ctx=_context(ingest.token))
