from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from research_registry.mcp.write_runtime import WriteMcpRuntime
from research_registry.mcp_tools import create_mcp_server
from tests.fixtures.v2_review import seed_review_registry


def _call(server, name: str, arguments: dict) -> dict:
    result = asyncio.run(server.call_tool(name, arguments))
    assert isinstance(result, tuple)
    content, structured = result
    assert json.loads(content[0].text) == structured
    return structured


def test_write_tools_are_closed_bounded_and_keep_v1_contracts(
    tmp_path: Path,
) -> None:
    registry, _ = seed_review_registry(tmp_path, key="mcp-schema")
    server = create_mcp_server(registry, service=registry)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    assert {"research_review", "research_refresh"} <= set(tools)
    assert {"create_claim", "create_research_bundle", "publish"} <= set(tools)
    for name in ("research_review", "research_refresh"):
        tool = tools[name]
        assert tool.parameters["additionalProperties"] is False
        assert tool.output_schema is not None
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False
    assert tools["research_refresh"].parameters["properties"]["mode"]["enum"] == [
        "inspect",
        "enqueue",
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
