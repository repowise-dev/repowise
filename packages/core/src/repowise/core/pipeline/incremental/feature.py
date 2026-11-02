"""Controls for incremental graph update support."""

from __future__ import annotations

import os

_INCREMENTAL_GRAPH_ENV = "REPOWISE_INCREMENTAL_GRAPH"


def incremental_graph_enabled() -> bool:
    """Whether updates may patch the graph instead of rebuilding it.

    On by default: an ineligible change still takes the full rebuild.
    ``REPOWISE_INCREMENTAL_GRAPH=0`` turns it off, and then no seed or
    resolved-import cache is read or written.
    """
    return os.environ.get(_INCREMENTAL_GRAPH_ENV, "1") != "0"
