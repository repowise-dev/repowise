"""Opt-in controls for incremental graph update support."""

from __future__ import annotations

import os

_INCREMENTAL_GRAPH_ENV = "REPOWISE_INCREMENTAL_GRAPH"


def incremental_graph_enabled() -> bool:
    """Whether the experimental incremental graph feature is explicitly enabled."""
    return os.environ.get(_INCREMENTAL_GRAPH_ENV) == "1"
