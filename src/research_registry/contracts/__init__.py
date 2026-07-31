"""Versioned external contracts.

The runtime continues to use :mod:`research_registry.models` during the v1
compatibility window. Import a version explicitly from this package when
building a versioned adapter.
"""

from . import v1, v2

__all__ = ["v1", "v2"]
