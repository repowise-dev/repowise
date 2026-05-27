"""Django convention edges.

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


def _add_django_edges(graph: nx.DiGraph, path_set: set[str]) -> int:
    """Django conventions: admin->models, urls->views in the same directory."""
    count = 0
    by_dir: dict[str, dict[str, str]] = {}
    for p in path_set:
        pp = Path(p)
        d = pp.parent.as_posix()
        by_dir.setdefault(d, {})[pp.stem] = p

    for _d, stems in by_dir.items():
        if (
            "admin" in stems
            and "models" in stems
            and _add_edge_if_new(graph, stems["admin"], stems["models"])
        ):
            count += 1
        if (
            "urls" in stems
            and "views" in stems
            and _add_edge_if_new(graph, stems["urls"], stems["views"])
        ):
            count += 1
        if (
            "forms" in stems
            and "models" in stems
            and _add_edge_if_new(graph, stems["forms"], stems["models"])
        ):
            count += 1
        if (
            "serializers" in stems
            and "models" in stems
            and _add_edge_if_new(graph, stems["serializers"], stems["models"])
        ):
            count += 1
    return count


class _DjangoHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return "django" in dctx.stack_lower

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_django_edges(graph, path_set)


HANDLERS: list[FrameworkHandler] = [_DjangoHandler()]
