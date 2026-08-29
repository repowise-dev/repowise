"""The narrow graph view analyses use to ask about an edge between two files.

Analyses that only need "is there an edge from A to B, of this type?" should
not pull NetworkX into their signature, because that makes every one of their
tests build a real graph to say no. :class:`HasEdge` is the contract and
:class:`ImportEdgeView` is the adapter over the real ``DiGraph``.

It lives under ``analysis/`` rather than beside any one consumer because more
than one of them needs it, and a view of the graph belongs to none of them.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..ingestion.models import FILE_DEPENDENCY_EDGE_TYPES

__all__ = ["HasEdge", "ImportEdgeView"]


class HasEdge(Protocol):
    """Minimal graph view: does an edge of some type join these two files?"""

    def has_edge(self, src: str, dst: str, key: str = "imports") -> bool: ...

    def has_dependency(self, a: str, b: str) -> bool: ...

    def knows(self, path: str) -> bool: ...


class ImportEdgeView:
    """``HasEdge`` over a NetworkX DiGraph, matched on the ``edge_type`` attribute.

    The graph stores one edge between any two file nodes and records its kind as
    an attribute, so *key* is compared against that rather than being a
    multigraph key. A missing graph answers ``False`` to everything, which lets
    a caller pass ``None`` instead of branching.
    """

    __slots__ = ("_graph",)

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    def has_edge(self, src: str, dst: str, key: str = "imports") -> bool:
        g = self._graph
        if g is None:
            return False
        try:
            if not g.has_edge(src, dst):
                return False
            data = g.get_edge_data(src, dst) or {}
        except Exception:
            return False
        return data.get("edge_type") == key

    def has_dependency(self, a: str, b: str) -> bool:
        """Whether any file-level dependency joins the two, either direction.

        ``imports`` is only one of these; matching it alone reports a pair as
        unexplained when a type reference or a framework binding already
        accounts for it.
        """
        return self._typed(a, b) or self._typed(b, a)

    def _typed(self, src: str, dst: str) -> bool:
        g = self._graph
        if g is None:
            return False
        try:
            if not g.has_edge(src, dst):
                return False
            data = g.get_edge_data(src, dst) or {}
        except Exception:
            return False
        return data.get("edge_type") in FILE_DEPENDENCY_EDGE_TYPES

    def knows(self, path: str) -> bool:
        """Whether the parser ingested this file, so an edge is possible at all.

        A lockfile or a changelog is tracked by git and co-changes constantly,
        but never becomes a node, so it has no edge to find and its absence
        says nothing.
        """
        g = self._graph
        if g is None:
            return False
        try:
            return path in g
        except Exception:
            return False
