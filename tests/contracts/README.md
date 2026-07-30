# V1 contract snapshots

`v1_openapi.json`, `v1_mcp_tools.json`, and `v1_database_inventory.json` are
deterministic golden files for the HTTP, MCP, and SQLite schema surfaces that
predate the Research Registry v2 migration.

Regenerate them only from `http_openapi_snapshot` and `mcp_tools_snapshot` in
`research_registry.contract_snapshots`, then review the complete diff. RR2-000
records the current surface; it does not authorize changing that surface.
