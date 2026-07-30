from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from .data_audit import V1_TABLES, connect_database_read_only
from .db import DatabaseTarget, DbConnection


def http_openapi_snapshot(app: FastAPI) -> dict[str, Any]:
    """Return the deterministic v1 OpenAPI document for golden comparison."""
    return _sort_mapping(deepcopy(app.openapi()))


def mcp_tools_snapshot(server: FastMCP) -> dict[str, Any]:
    """Return v1 MCP names, descriptions, and complete input JSON schemas."""
    tools = server._tool_manager.list_tools()
    return {
        "contract": "research-registry-mcp-v1",
        "tools": [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": _sort_mapping(deepcopy(tool.parameters)),
            }
            for tool in sorted(tools, key=lambda item: item.name)
        ],
    }


def database_inventory_snapshot(
    target: str | Path | DatabaseTarget,
) -> dict[str, Any]:
    """Return a deterministic, row-content-free v1 database inventory."""
    with connect_database_read_only(target) as conn:
        database_kind = conn.target.kind
        if database_kind == "sqlite":
            present_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        else:
            present_rows = conn.execute(
                """
                SELECT table_name AS name
                FROM information_schema.tables
                WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'
                """
            ).fetchall()
        present = {row["name"] for row in present_rows}
        tables: dict[str, Any] = {}
        for table in V1_TABLES:
            if table not in present:
                continue
            if database_kind == "sqlite":
                columns = [
                    {
                        "name": row["name"],
                        "type": row["type"],
                        "nullable": not bool(row["notnull"]),
                        "primary_key_position": int(row["pk"]),
                    }
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                ]
            else:
                columns = [
                    {
                        "name": row["column_name"],
                        "type": row["data_type"],
                        "nullable": row["is_nullable"] == "YES",
                        "primary_key_position": 0,
                    }
                    for row in conn.execute(
                        """
                        SELECT column_name, data_type, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = current_schema() AND table_name = ?
                        ORDER BY ordinal_position
                        """,
                        (table,),
                    ).fetchall()
                ]
            tables[table] = {
                "columns": columns,
                "row_count": _row_count(conn, table),
            }
        migrations = (
            [
                {
                    "migration_id": row["migration_id"],
                    "checksum_sha256": row["checksum_sha256"],
                }
                for row in conn.execute(
                    "SELECT migration_id, checksum_sha256 FROM schema_migrations ORDER BY migration_id"
                ).fetchall()
            ]
            if "schema_migrations" in present
            else []
        )
    return {
        "contract": "research-registry-database-v1",
        "database_kind": database_kind,
        "migrations": migrations,
        "tables": tables,
    }


def _sort_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sort_mapping(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort_mapping(item) for item in value]
    return value


def _row_count(conn: DbConnection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(next(iter(dict(row).values())))
