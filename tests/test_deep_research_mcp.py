from __future__ import annotations

import asyncio
import json
from pathlib import Path

from research_registry.mcp.deep_research import create_deep_research_server
from tests.test_mcp_v2_read import _seed_v2


def _call(server, name: str, arguments: dict) -> dict:
    result = asyncio.run(server.call_tool(name, arguments))
    assert isinstance(result, tuple)
    content, structured = result
    assert json.loads(content[0].text) == structured
    return structured


def test_deep_research_profile_exposes_exact_read_only_contracts(
    tmp_path: Path,
) -> None:
    registry, _ = _seed_v2(tmp_path)
    server = create_deep_research_server(registry, service=registry)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    assert set(tools) == {"search", "fetch"}
    assert tools["search"].parameters == {
        "additionalProperties": False,
        "properties": {"query": {"title": "Query", "type": "string"}},
        "required": ["query"],
        "title": "searchArguments",
        "type": "object",
    }
    assert tools["fetch"].parameters == {
        "additionalProperties": False,
        "properties": {"id": {"title": "Id", "type": "string"}},
        "required": ["id"],
        "title": "fetchArguments",
        "type": "object",
    }
    assert set(tools["search"].output_schema["properties"]) == {"results"}
    assert set(tools["fetch"].output_schema["properties"]) == {
        "id",
        "title",
        "text",
        "url",
        "metadata",
    }
    assert tools["fetch"].output_schema["required"] == ["id", "title", "text", "url"]
    assert tools["search"].annotations.readOnlyHint is True
    assert tools["fetch"].annotations.readOnlyHint is True


def test_deep_research_search_then_fetch_is_bounded_and_labels_content(
    tmp_path: Path,
) -> None:
    registry, ids = _seed_v2(tmp_path)
    server = create_deep_research_server(registry, service=registry)

    searched = _call(server, "search", {"query": "bounded research retrieval"})
    assert searched["results"]
    assert set(searched["results"][0]) == {"id", "title", "url"}
    assert ids["claim"] in {result["id"] for result in searched["results"]}

    fetched = _call(server, "fetch", {"id": ids["claim"]})
    assert set(fetched) == {"id", "title", "text", "url", "metadata"}
    assert fetched["id"] == ids["claim"]
    assert fetched["text"].startswith("UNTRUSTED RESEARCH MATERIAL")
    assert fetched["metadata"]["content_label"] == "untrusted research material"
    assert len(json.dumps(fetched).encode()) <= 65_536
