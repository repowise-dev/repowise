"""pytest conftest convention edges.

Split out of ``framework_edges.py`` (PR 3.5) — behaviour-preserving move.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
)

if TYPE_CHECKING:
    import networkx as nx


def _add_conftest_edges(graph: nx.DiGraph, path_set: set[str]) -> int:
    """conftest.py -> test files in the same or child directories."""
    count = 0
    conftest_paths = [p for p in path_set if Path(p).name == "conftest.py"]

    for conf in conftest_paths:
        conf_dir = Path(conf).parent.as_posix()
        prefix = f"{conf_dir}/" if conf_dir != "." else ""
        for p in path_set:
            if p == conf:
                continue
            node = graph.nodes.get(p, {})
            if not node.get("is_test", False):
                continue
            if (p.startswith(prefix) or (prefix == "" and "/" not in p)) and _add_edge_if_new(
                graph, p, conf
            ):
                count += 1
    return count


class _ConftestHandler:
    """pytest ``conftest.py`` fixtures are imported implicitly by collection."""

    def detect(self, dctx: DetectionContext) -> bool:
        return True

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_conftest_edges(graph, path_set)


HANDLERS: list[FrameworkHandler] = [_ConftestHandler()]
