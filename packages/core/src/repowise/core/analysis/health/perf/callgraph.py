"""Shared one-pass index of the resolved ``calls`` graph.

Both the cross-function N+1 bridge (:mod:`.crossfn`) and the centrality gate
(:mod:`.ranking`, Primitive 2) need the same three things off the engine's
resolved dependency graph: a symbol-node name lookup, a ``calls`` adjacency in
both directions, and a resolver from ``(file, def-line)`` to a symbol-node id.
Building it once and sharing it keeps the graph passes to a single ``O(V + E)``
scan per ``analyze()`` instead of one per consumer.

Extracted verbatim from ``crossfn._CallGraphIndex`` (the 6a/PR4 shape) so the
behaviour the cross-function precision study measured is unchanged; the only
difference is that it is now importable by the ranker too.
"""

from __future__ import annotations

from typing import Any


def module_node_id(path: str) -> str:
    """The synthetic ``::__module__`` symbol-node id for a file's module scope."""
    return f"{path}::__module__"


class CallGraphIndex:
    """One-pass index of the dependency graph for the perf graph passes.

    Holds only what the passes need: symbol-node name lookup, a ``calls``
    adjacency in both directions, and a resolver from ``(file, def-line)`` to a
    symbol-node id. Built once; never mutates the source graph.
    """

    __slots__ = ("_by_file_line", "_ranges", "forward", "in_degree", "name", "nodes", "reverse")

    def __init__(self, graph: Any) -> None:
        self.nodes: set[str] = set()
        self.name: dict[str, str] = {}
        self.forward: dict[str, list[str]] = {}
        self.reverse: dict[str, list[str]] = {}
        # Exact (path, start_line) -> symbol id, plus a per-file range list for
        # the containment fallback when a def line doesn't match exactly.
        self._by_file_line: dict[tuple[str, int], str] = {}
        self._ranges: dict[str, list[tuple[int, int, str]]] = {}

        for node_id, attrs in graph.nodes(data=True):
            self.nodes.add(node_id)
            if attrs.get("node_type") != "symbol":
                continue
            self.name[node_id] = attrs.get("name") or ""
            path = attrs.get("file_path")
            if not path:
                continue
            start = attrs.get("start_line")
            end = attrs.get("end_line")
            if isinstance(start, int):
                self._by_file_line.setdefault((path, start), node_id)
                if isinstance(end, int):
                    self._ranges.setdefault(path, []).append((start, end, node_id))

        for src, dst, data in graph.edges(data=True):
            if data.get("edge_type") != "calls":
                continue
            self.forward.setdefault(src, []).append(dst)
            self.reverse.setdefault(dst, []).append(src)

        # Distinct direct callers per node (the centrality proxy used by the
        # ranker). Computed off the reverse adjacency in one pass.
        self.in_degree: dict[str, int] = {
            node: len(set(callers)) for node, callers in self.reverse.items()
        }

    def resolve_function(self, path: str, func_start: int) -> str | None:
        """Symbol-node id for the function defined at ``func_start`` in ``path``.

        ``func_start == 0`` is module scope (the synthetic ``::__module__``
        node). Otherwise prefer an exact def-line match, then fall back to the
        innermost symbol whose line range contains ``func_start`` (tolerates a
        decorator/def off-by-one without matching an enclosing scope).
        """
        if func_start == 0:
            mod = module_node_id(path)
            return mod if mod in self.nodes else None
        exact = self._by_file_line.get((path, func_start))
        if exact is not None:
            return exact
        best: str | None = None
        best_start = -1
        for start, end, node_id in self._ranges.get(path, ()):
            if start <= func_start <= end and start > best_start:
                best, best_start = node_id, start
        return best
