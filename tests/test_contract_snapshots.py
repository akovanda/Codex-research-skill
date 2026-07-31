from __future__ import annotations

import json
from pathlib import Path

from research_registry.app import create_app
from research_registry.contract_snapshots import (
    database_inventory_snapshot,
    http_openapi_snapshot,
    mcp_tools_snapshot,
)
from research_registry.mcp_tools import create_mcp_server
from research_registry.service import RegistryService
from tests.test_registry import make_settings


CONTRACTS = Path(__file__).parent / "contracts"


def test_v1_http_openapi_snapshot_is_stable(tmp_path: Path) -> None:
    current = http_openapi_snapshot(create_app(make_settings(tmp_path)))
    expected = json.loads((CONTRACTS / "v1_openapi.json").read_text(encoding="utf-8"))

    assert current == expected


def test_v1_mcp_tool_input_schema_snapshot_is_stable() -> None:
    current = mcp_tools_snapshot(
        create_mcp_server(object(), legacy_tools_enabled=True)
    )
    expected = json.loads((CONTRACTS / "v1_mcp_tools.json").read_text(encoding="utf-8"))

    assert current == expected
    assert {tool["name"] for tool in current["tools"]} >= {
        "search",
        "create_question",
        "get_annotation",
        "get_finding",
        "create_research_bundle",
    }


def test_v1_database_inventory_snapshot_is_stable(tmp_path: Path) -> None:
    database_path = tmp_path / "v1.sqlite3"
    service = RegistryService(database_path)
    service.initialize()

    current = database_inventory_snapshot(database_path)
    expected = json.loads(
        (CONTRACTS / "v1_database_inventory.json").read_text(encoding="utf-8")
    )

    assert current == expected
