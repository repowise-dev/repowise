"""The narrow graph view analyses use to ask about an edge between two files.

Analyses that only need "is there an edge from A to B, of this type?" should
not pull NetworkX into their signature, because that makes every one of their
tests build a real graph to say no. :class:`HasEdge` is the contract and
:class:`ImportEdgeView` is the adapter over the real ``DiGraph``.

It lives under ``analysis/`` rather than beside any one consumer because more
than one of them needs it, and a view of the graph belongs to none of them.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Protocol

from ..ingestion.languages import REGISTRY
from ..ingestion.models import FILE_DEPENDENCY_EDGE_TYPES

__all__ = ["HasEdge", "ImportEdgeView"]


def _language_of(path: str) -> str:
    """The registry's language tag for *path*, or ``"unknown"``."""
    name = PurePosixPath(path.replace("\\", "/")).name
    return REGISTRY.from_filename(name) or REGISTRY.from_extension(
        PurePosixPath(name).suffix.lower()
    )


class HasEdge(Protocol):
    """Minimal graph view: does an edge of some type join these two files?"""

    def has_edge(self, src: str, dst: str, key: str = "imports") -> bool: ...

    def dependency_kind(self, a: str, b: str) -> str | None: ...

    def can_carry_dependency(self, path: str) -> bool: ...


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

    def dependency_kind(self, a: str, b: str) -> str | None:
        """The ``edge_type`` of any file-level dependency joining the two.

        ``imports`` is only one of these; matching it alone reports a pair as
        unexplained when a type reference or a framework binding already
        accounts for it. The kind, not just a yes/no, because an import and a
        framework binding are different claims about why two files move
        together. Direction is not reported: the pair is undirected, and
        ``a -> b`` is tried first only to make the answer deterministic.
        """
        return self._typed(a, b) or self._typed(b, a)

    def _typed(self, src: str, dst: str) -> str | None:
        g = self._graph
        if g is None:
            return None
        try:
            if not g.has_edge(src, dst):
                return None
            data = g.get_edge_data(src, dst) or {}
        except Exception:
            return None
        kind = data.get("edge_type")
        return kind if kind in FILE_DEPENDENCY_EDGE_TYPES else None

    def can_carry_dependency(self, path: str) -> bool:
        """Whether an absent edge at *path* would mean anything.

        Being a node is not enough. ``pyproject.toml`` and ``README.md`` are
        both ingested and both become nodes, yet no resolver can ever emit an
        edge for them, so "no edge found" is a fact about the language rather
        than about the repository. The registry already grades this per
        language as ``import_support``; ``"none"`` is the generic stem-lookup
        fallback, which is not a mechanism a finding can rest on.

        A file the parser never saw fails here too: a lockfile in a blocked
        directory has no edge to look for either.
        """
        g = self._graph
        if g is None:
            return False
        try:
            attrs = g.nodes[path]
        except Exception:
            return False
        language = attrs.get("language")
        if not isinstance(language, str):
            # Not every node carries the attribute: a node synthesised for a
            # dynamic edge target has none, and persistence drops null
            # columns on rehydrate. The path answers the same question.
            language = _language_of(path)
        return REGISTRY.import_support_for(language) != "none"
