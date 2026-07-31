from __future__ import annotations

import os
import warnings


LEGACY_HEURISTICS_ENV = "RESEARCH_REGISTRY_LEGACY_HEURISTICS"
MCP_LEGACY_ENV = "RESEARCH_REGISTRY_MCP_LEGACY"
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})
_DEPRECATION_MESSAGE = (
    "Research Registry legacy heuristic research is deprecated since 0.2.0. "
    "Use the v2 research-recall and research-deposit skills instead. "
    "Removal is not scheduled before a future major release and requires a "
    "separate ADR; see docs/implicit-research-capture.md."
)
_warning_emitted = False


class LegacyFeatureDisabledError(RuntimeError):
    """Raised when a disabled heuristic compatibility adapter is invoked."""


class LegacyHeuristicDeprecationWarning(FutureWarning):
    """Visible warning for explicit use of a deprecated heuristic adapter."""


def legacy_heuristics_enabled() -> bool:
    return _env_flag_enabled(LEGACY_HEURISTICS_ENV)


def legacy_mcp_tools_enabled() -> bool:
    return _env_flag_enabled(MCP_LEGACY_ENV)


def require_legacy_heuristics() -> None:
    if not legacy_heuristics_enabled():
        raise LegacyFeatureDisabledError(
            "Legacy heuristic research is disabled. Set "
            f"{LEGACY_HEURISTICS_ENV}=1 only for temporary compatibility; "
            "use the v2 research-recall and research-deposit paths by default."
        )
    _warn_once()


def _warn_once() -> None:
    global _warning_emitted
    if _warning_emitted:
        return
    warnings.warn(
        _DEPRECATION_MESSAGE,
        LegacyHeuristicDeprecationWarning,
        stacklevel=3,
    )
    _warning_emitted = True


def _env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _ENABLED_VALUES
