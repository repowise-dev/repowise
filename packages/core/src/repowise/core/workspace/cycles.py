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
single-repo graph metrics use — rather than a hand-rolled DFS. We cap the
reported set (:data:`MAX_CYCLES`) and log truncation so a pathological graph
never bloats the artifact silently.

**Bounded enumeration.** Cycle count grows exponentially with the largest
strongly connected component (an 18-service tangle has millions), so the
enumeration stops after :data:`MAX_ENUMERATED_CYCLES`. Under that budget the
total is exact; past it the total is a lower bound (``total_capped``) and the
listed cycles come from a length-bounded pass, so they are still the shortest.

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

#: Enumeration budget. A graph with more cycles than this reports the total as
#: "at least" this many instead of enumerating every one (which is exponential
#: in the largest cyclic group and hangs on a dense tangle).
MAX_ENUMERATED_CYCLES = 20_000

#: Longest cycle the bounded pass looks for once the budget is hit. The short
#: loops are the actionable ones, and length-bounded enumeration stays cheap.
MAX_CYCLE_LENGTH = 6


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


def cycle_total(total: int, capped: bool) -> str:
    """``total`` as a reader should see it: "at least N" when the budget stopped the count."""
    return f"at least {total}" if capped else str(total)


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


def _canonical_rotation(nodes: list[str]) -> list[str]:
    """Rotate the cycle so the lexicographically smallest service id starts it.

    NetworkX's ``simple_cycles`` begins each cycle at whatever node its own
    iteration order lands on, which follows set iteration order and therefore
    varies with PYTHONHASHSEED. A cycle is the same loop regardless of where
    the rotation starts, so normalising the start node makes the reported
    ``nodes``, ``edge_ids`` and ``leg_kinds`` identical on every run.
    """
    if not nodes:
        return nodes
    start = nodes.index(min(nodes))
    return nodes[start:] + nodes[:start]


def _enumerate(digraph: Any, length_bound: int | None) -> tuple[list[list[str]], bool]:
    """Up to :data:`MAX_ENUMERATED_CYCLES` rotated cycles, and whether that is all of them."""
    import networkx as nx

    found: list[list[str]] = []
    for raw in nx.simple_cycles(digraph, length_bound=length_bound):
        if len(found) == MAX_ENUMERATED_CYCLES:
            return found, False
        found.append(_canonical_rotation(raw))
    return found, True


def _shortest_cycles(digraph: Any) -> list[list[str]]:
    """Every cycle up to the longest length whose bounded pass fits the budget.

    Raises the bound one length at a time and stops once the pass holds
    :data:`MAX_CYCLES` cycles (the shortest ones are then all in it) or the
    budget cuts it. The previous complete pass is then kept, even with fewer
    than :data:`MAX_CYCLES`: a cut pass is an arbitrary subset, and listing
    fewer cycles beats listing ones that change from run to run.
    """
    best: list[list[str]] = []
    for bound in range(1, MAX_CYCLE_LENGTH + 1):
        found, complete = _enumerate(digraph, bound)
        if not complete:
            return best or found
        best = found
        if len(best) >= MAX_CYCLES:
            break
    return best


def detect_cycles(graph: SystemGraph) -> tuple[list[DependencyCycle], int, bool]:
    """Return the structural dependency cycles in *graph*, how many, and whether that is a floor.

    Cycles are ordered shortest-first (the tightest loops are the most
    actionable), then lexicographically for stability. Every cycle is rotated
    to start at its smallest node id, so the same graph always produces the
    same cycles regardless of hash seed. The returned list is capped at
    :data:`MAX_CYCLES`; the second element is the count before that cap, so a
    caller reporting "N cycles" cannot accidentally report the cap instead.
    The third is True when the enumeration budget stopped the count: the total
    is then "at least N". Returns ``([], 0, False)`` when the structural
    subgraph is acyclic or NetworkX is unavailable.
    """
    adjacency = graph.adjacency()
    if not adjacency:
        return [], 0, False

    try:
        import networkx as nx
    except Exception:  # pragma: no cover - networkx is a hard dependency
        _log.warning("networkx unavailable; skipping cycle detection", exc_info=True)
        return [], 0, False

    digraph = nx.DiGraph()
    digraph.add_nodes_from(n.id for n in graph.nodes)
    digraph.add_edges_from((src, tgt) for src, targets in adjacency.items() for tgt in targets)

    try:
        raw_cycles, complete = _enumerate(digraph, None)
        total = len(raw_cycles)
        if not complete:
            # No cycle within MAX_CYCLE_LENGTH: list some from the cut full pass
            # rather than none beside "at least N" (which ones then follows
            # networkx iteration order).
            raw_cycles = _shortest_cycles(digraph) or raw_cycles
    except Exception:  # pragma: no cover - defensive
        _log.warning("cycle enumeration failed", exc_info=True)
        return [], 0, False

    raw_cycles.sort(key=lambda nodes: (len(nodes), nodes))
    if not complete:
        _log.info(
            "Cycle detection stopped at %d cycles; reporting the %d shortest.",
            total,
            min(total, MAX_CYCLES),
        )
    elif total > MAX_CYCLES:
        _log.info(
            "Cycle detection found %d cycles; reporting the %d shortest.",
            total,
            MAX_CYCLES,
        )
    cycles = [_cycle(nodes, adjacency) for nodes in raw_cycles[:MAX_CYCLES]]
    return cycles, total, not complete
