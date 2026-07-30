from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def close_tool_input_schema(server: FastMCP, name: str) -> None:
    """Make SDK-generated function arguments reject and document unknown fields."""
    tool = server._tool_manager.get_tool(name)
    if tool is None:
        raise RuntimeError(f"MCP tool is not registered: {name}")
    model = tool.fn_metadata.arg_model
    model.model_config["extra"] = "forbid"
    model.model_rebuild(force=True)
    tool.parameters = model.model_json_schema(by_alias=True)
