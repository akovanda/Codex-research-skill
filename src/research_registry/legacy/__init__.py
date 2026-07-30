"""Deprecated heuristic research adapters.

These modules are retained for compatibility only. Their entry points require
``RESEARCH_REGISTRY_LEGACY_HEURISTICS=1`` and are not imported by normal
Research Registry startup.
"""

from .feature import (
    LEGACY_HEURISTICS_ENV,
    MCP_LEGACY_ENV,
    LegacyFeatureDisabledError,
    LegacyHeuristicDeprecationWarning,
    legacy_heuristics_enabled,
    legacy_mcp_tools_enabled,
    require_legacy_heuristics,
)

__all__ = [
    "LEGACY_HEURISTICS_ENV",
    "MCP_LEGACY_ENV",
    "LegacyFeatureDisabledError",
    "LegacyHeuristicDeprecationWarning",
    "legacy_heuristics_enabled",
    "legacy_mcp_tools_enabled",
    "require_legacy_heuristics",
]
