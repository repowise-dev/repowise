"""Controls for incremental graph update support."""

from __future__ import annotations

import os
from typing import Any

_INCREMENTAL_GRAPH_ENV = "REPOWISE_INCREMENTAL_GRAPH"


def incremental_graph_enabled() -> bool:
    """Whether updates may patch the graph instead of rebuilding it.

    On by default: an ineligible change still takes the full rebuild.
    ``REPOWISE_INCREMENTAL_GRAPH=0`` turns it off, and then no seed or
    resolved-import cache is read or written.
    """
    return os.environ.get(_INCREMENTAL_GRAPH_ENV, "1") != "0"


def stamp_graph_update(state: dict[str, Any], decision: dict[str, Any] | None = None) -> None:
    """Record in ``state["graph_update"]`` how this run built the graph.

    *decision* is an update's patch-or-rebuild summary
    (``graph_builder.incremental_graph_decision``); ``None`` means a full
    index, which records whether it primed the patch caches. The next update
    reads the seed snapshot only when the record says ``enabled``.
    """
    if decision is None:
        enabled = incremental_graph_enabled()
        decision = {
            "enabled": enabled,
            "mode": "full",
            "reason": (
                "initial full graph build; incremental cache primed"
                if enabled
                else "feature disabled"
            ),
            "files": [],
        }
    state["graph_update"] = decision
