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
from .dead_code.file_reachability import BARREL_FILENAMES

__all__ = [
    "BARREL_DEPENDENCY",
    "INHERITANCE_DEPENDENCY",
    "HasEdge",
    "ImportEdgeView",
    "can_carry_dependency",
    "heritage_ancestors",
]

#: ``dependency_kind`` for a pair joined through a re-export barrel rather than
#: by a direct edge: ``a -> index.ts -> b``.
BARREL_DEPENDENCY = "barrel"

#: ``dependency_kind`` for a pair where a type in one file inherits, directly
#: or transitively, from a type in the other. Java and C# classes in one
#: package extend each other with no import, so no file edge records it.
INHERITANCE_DEPENDENCY = "inherits"

_HERITAGE_EDGE_TYPES = ("extends", "implements")


def heritage_ancestors(graph: Any, class_id: str) -> set[str]:
    """Every type symbol *class_id* inherits from, transitively."""
    seen: set[str] = set()
    stack = [class_id]
    while stack:
        for _u, base, data in graph.out_edges(stack.pop(), data=True):
            if data.get("edge_type") in _HERITAGE_EDGE_TYPES and base not in seen:
                seen.add(base)
                stack.append(base)
    return seen


def _language_of(path: str) -> str:
    """The registry's language tag for *path*, or ``"unknown"``."""
    name = PurePosixPath(path.replace("\\", "/")).name
    return REGISTRY.from_filename(name) or REGISTRY.from_extension(
        PurePosixPath(name).suffix.lower()
    )


def can_carry_dependency(path: str, language: str | None = None) -> bool:
    """Whether an absent edge at *path* would mean anything.

    ``pyproject.toml`` and ``README.md`` are both ingested and both become
    nodes, yet no resolver can ever emit an edge for them, so "no edge found"
    is a fact about the language rather than about the repository. The registry
    grades this per language as ``import_support``; ``"none"`` is the generic
    stem-lookup fallback, which is not a mechanism a finding can rest on.

    *language* is optional because it is not always recorded: a node
    synthesised for a dynamic edge target carries none and persistence drops
    null columns on rehydrate. The path answers the same question.
    """
    if not isinstance(language, str) or not language:
        language = _language_of(path)
    return REGISTRY.import_support_for(language) != "none"


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

    __slots__ = ("_barrels", "_graph", "_heritage")

    def __init__(self, graph: Any) -> None:
        self._graph = graph
        self._barrels: dict[str, tuple[str, ...]] = {}
        self._heritage: dict[str, frozenset[str]] = {}

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

        A consumer importing a package's barrel (``index.ts``, ``__init__.py``)
        that re-exports the other file depends on it as surely as a direct
        import, so a two-hop path whose middle node is a barrel answers
        :data:`BARREL_DEPENDENCY`. Only a chain through the barrel counts: two
        files a barrel merely re-exports side by side, or two consumers of one
        barrel, share a neighbour and nothing more.

        A subclass and any of its ancestors answer
        :data:`INHERITANCE_DEPENDENCY`; two subclasses of one base do not.
        """
        return (
            self._typed(a, b)
            or self._typed(b, a)
            or self._via_barrel(a, b)
            or self._via_heritage(a, b)
        )

    def _via_heritage(self, a: str, b: str) -> str | None:
        if b in self._heritage_files(a) or a in self._heritage_files(b):
            return INHERITANCE_DEPENDENCY
        return None

    def _heritage_files(self, path: str) -> frozenset[str]:
        """Files declaring a type some type in *path* inherits from, cached."""
        cached = self._heritage.get(path)
        if cached is None:
            cached = self._heritage[path] = frozenset(self._inherited_owners(path))
        return cached

    def _inherited_owners(self, path: str) -> set[str]:
        g = self._graph
        if g is None or path not in g:
            return set()
        types = [s for _u, s, d in g.out_edges(path, data=True) if d.get("edge_type") == "defines"]
        bases = set().union(*(heritage_ancestors(g, t) for t in types))
        # An external base has no node attributes, or no node: skip just it.
        owners = {(g.nodes.get(b) or {}).get("file_path") for b in bases}
        return {o for o in owners if o and o != path}

    def _via_barrel(self, a: str, b: str) -> str | None:
        for src, dst in ((a, b), (b, a)):
            for barrel in self._barrels_of(src):
                if self._typed(barrel, dst):
                    return BARREL_DEPENDENCY
        return None

    def _barrels_of(self, path: str) -> tuple[str, ...]:
        """The barrels *path* depends on directly, cached per file."""
        cached = self._barrels.get(path)
        if cached is not None:
            return cached
        found: tuple[str, ...] = ()
        g = self._graph
        if g is not None:
            try:
                found = tuple(
                    mid
                    for mid in g.successors(path)
                    if PurePosixPath(mid).name in BARREL_FILENAMES and self._typed(path, mid)
                )
            except Exception:
                found = ()
        self._barrels[path] = found
        return found

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
        """Whether an absent edge at *path* would mean anything, per the graph.

        Being a node is not enough, and a file the parser never saw fails here
        too: a lockfile in a blocked directory has no edge to look for either.
        """
        g = self._graph
        if g is None:
            return False
        try:
            attrs = g.nodes[path]
        except Exception:
            return False
        return can_carry_dependency(path, attrs.get("language"))
