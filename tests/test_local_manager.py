from __future__ import annotations

from dataclasses import replace

from research_registry.local_manager import (
    MANAGED_MCP_BEGIN,
    MANAGED_MCP_END,
    build_local_config,
    render_codex_mcp_block,
    render_compose_env,
    render_compose_yaml,
    upsert_managed_codex_config,
)


def test_render_compose_files_include_localhost_port_and_image() -> None:
    config = build_local_config(port=8017)

    compose_yaml = render_compose_yaml(config)
    compose_env = render_compose_env(config)

    assert config.image_tag in compose_yaml
    assert '127.0.0.1:8017:8000' in compose_yaml
    assert "RESEARCH_REGISTRY_PORT=8000" in compose_env
    assert "RESEARCH_REGISTRY_PUBLIC_BASE_URL=http://127.0.0.1:8017" in compose_env


def test_upsert_managed_codex_config_appends_and_replaces_block() -> None:
    config = replace(build_local_config(port=8018), api_key="test-key")

    initial = 'model = "gpt-5.4"\n'
    updated = upsert_managed_codex_config(initial, config)
    replaced = upsert_managed_codex_config(updated, replace(config, api_key="new-key"))

    assert MANAGED_MCP_BEGIN in updated
    assert MANAGED_MCP_END in updated
    assert '[mcp_servers.researchRegistry]' in updated
    assert '"x-api-key" = "test-key"' in updated
    assert '"x-api-key" = "new-key"' in replaced
    assert replaced.count(MANAGED_MCP_BEGIN) == 1


def test_render_codex_mcp_block_requires_api_key() -> None:
    config = build_local_config(port=8020)
    try:
        render_codex_mcp_block(config)
    except RuntimeError as exc:
        assert "api_key" in str(exc)
    else:
        raise AssertionError("expected render_codex_mcp_block to require api_key")
