"""Compatibility imports for shared execution-graph path reachability."""

from repowise.core.analysis.execution_graph import (
    ReachInfo,
    path_to_sink,
    reachable_to_sink,
)

__all__ = ["ReachInfo", "path_to_sink", "reachable_to_sink"]
