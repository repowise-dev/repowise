"""Cross-repo dependency cycles — find circular service dependencies.

A cycle in the *structural* edges of the system graph (``A`` depends on ``B``
depends on … depends on ``A``) is an architecture smell: the services cannot be
built, deployed, or reasoned about independently. This module finds those cycles
over the same :class:`~repowise.core.workspace.system_graph.SystemGraph` every
other workspace view reads.

**Structural edges only.** Co-change is *behavioral* — two files changing
together does not assert a dependency, so a co-change "loop" is not a cycle. We
walk :meth:`SystemGraph.adjacency`, the structural view every workspace metric
reads (http / grpc / socket / event / package / db).

**An async leg still closes a cycle.** A loop that only closes through an event
edge couples message schemas and deploy order all the same, so it counts. Each
cycle carries ``leg_kinds``, the edge kinds on every leg, so a view can say
which legs are asynchronous (:data:`ASYNC_EDGE_KINDS`) instead of hiding them.

**Reuse, not reinvention.** Cycle finding is delegated to NetworkX
(``simple_cycles`` / ``strongly_connected_components``) — the same library the
single-repo graph metrics use — rather than a hand-rolled DFS. The graph is
service-granular (tens of nodes), so enumeration is cheap; we still cap the
reported set (:data:`MAX_CYCLES`) and log truncation so a pathological graph
never bloats the artifact silently.

Pure and I/O-free: it consumes a ``SystemGraph`` and returns dataclasses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from repowise.core.workspace.system_graph import ASYNC_EDGE_KINDS, SystemEdge, SystemGraph

_log = logging.getLogger("repowise.workspace.cycles")

# ---------------------------------------------------------------------------
# Constants (single source of truth)
# ---------------------------------------------------------------------------

#: Cap on the number of elementary cycles reported. Governance only needs the
#: representative loops, not every elementary cycle of a dense tangle; the true
#: count is returned alongside the capped list so no caller can mistake the one
#: for the other.
MAX_CYCLES = 50


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class DependencyCycle:
    """One elementary dependency cycle among services.

    ``nodes`` lists the participating service ids in traversal order (each edge
    goes ``nodes[i] -> nodes[i+1]``, and the last wraps back to the first).
    ``edge_ids`` are the system-graph edges that form the loop, so a view can
    highlight exactly those seams. ``length`` is the number of services.
    ``leg_kinds[i]`` lists the edge kinds joining ``nodes[i]`` to the next node,
    sorted: more than one when, say, an http and an event edge both do.
    """

    nodes: list[str]
    edge_ids: list[str]
    leg_kinds: list[list[str]] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.nodes)

    @property
    def async_legs(self) -> int:
        """How many legs only an asynchronous edge (an event) joins."""
        return sum(1 for kinds in self.leg_kinds if kinds and set(kinds) <= ASYNC_EDGE_KINDS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edge_ids": self.edge_ids,
            "length": self.length,
            "leg_kinds": self.leg_kinds,
            "async_legs": self.async_legs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DependencyCycle:
        return cls(
            nodes=list(data.get("nodes", [])),
            edge_ids=list(data.get("edge_ids", [])),
            leg_kinds=[list(kinds) for kinds in data.get("leg_kinds", [])],
        )


# ---------------------------------------------------------------------------
# Detection (pure)
# ---------------------------------------------------------------------------


def _cycle(
    nodes: list[str], adjacency: dict[str, dict[str, list[SystemEdge]]]
) -> DependencyCycle:
    """The loop ``nodes[0] -> … -> nodes[0]`` with each leg's edges.

    Parallel structural edges (e.g. http + package between the same services)
    are one leg: the first names it in ``edge_ids`` (a cycle highlights the
    seam, not every transport) and all of them name its ``leg_kinds``.
    """
    edge_ids: list[str] = []
    leg_kinds: list[list[str]] = []
    for i, src in enumerate(nodes):
        edges = adjacency.get(src, {}).get(nodes[(i + 1) % len(nodes)], [])
        if edges:
            edge_ids.append(edges[0].id)
            leg_kinds.append(sorted({e.kind for e in edges}))
    return DependencyCycle(nodes=list(nodes), edge_ids=edge_ids, leg_kinds=leg_kinds)


def detect_cycles(graph: SystemGraph) -> tuple[list[DependencyCycle], int]:
    """Return the structural dependency cycles in *graph*, and how many exist.

    Cycles are ordered shortest-first (the tightest loops are the most
    actionable), then lexicographically for stability. The returned list is
    capped at :data:`MAX_CYCLES`; the second element is the true count before
    that cap, so a caller reporting "N cycles" cannot accidentally report the
    cap instead. Returns ``([], 0)`` when the structural subgraph is acyclic or
    NetworkX is unavailable.
    """
    adjacency = graph.adjacency()
    if not adjacency:
        return [], 0

    try:
        import networkx as nx
    except Exception:  # pragma: no cover - networkx is a hard dependency
        _log.warning("networkx unavailable; skipping cycle detection", exc_info=True)
        return [], 0

    digraph = nx.DiGraph()
    digraph.add_nodes_from(n.id for n in graph.nodes)
    digraph.add_edges_from((src, tgt) for src, targets in adjacency.items() for tgt in targets)

    cycles: list[DependencyCycle] = []
    try:
        for raw in nx.simple_cycles(digraph):
            cycle = _cycle(raw, adjacency)
            if cycle.edge_ids:
                cycles.append(cycle)
    except Exception:  # pragma: no cover - defensive
        _log.warning("cycle enumeration failed", exc_info=True)
        return [], 0

    cycles.sort(key=lambda c: (c.length, c.nodes))
    total = len(cycles)
    if total > MAX_CYCLES:
        _log.info(
            "Cycle detection found %d cycles; reporting the %d shortest.",
            total,
            MAX_CYCLES,
        )
        cycles = cycles[:MAX_CYCLES]
    return cycles, total
