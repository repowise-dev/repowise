"""Shared state.json / knowledge-graph persistence helpers for CLI commands.

The writers moved to :mod:`repowise.core.analysis.knowledge_graph` so the
core incremental update path (including the workspace updater) can refresh
the exported artifacts; re-exported here for the existing CLI imports.
"""

from __future__ import annotations

from repowise.core.analysis.knowledge_graph import (  # noqa: F401
    build_kg_state,
    save_knowledge_graph_json,
)
