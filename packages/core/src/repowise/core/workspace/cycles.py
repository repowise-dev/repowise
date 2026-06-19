"""Cross-repo dependency cycles — find circular service dependencies.

A cycle in the *structural* edges of the system graph (``A`` depends on ``B``
depends on … depends on ``A``) is an architecture smell: the services cannot be
built, deployed, or reasoned about independently. This module finds those cycles
over the same :class:`~repowise.core.workspace.system_graph.SystemGraph` every
other workspace view reads.

**Structural edges only.** Co-change is *behavioral* — two files changing
together does not assert a dependency, so a co-change "loop" is not a cycle. We
walk only ``edge.structural`` edges (http / grpc / event / package / db), exactly
the distinction the system graph already encodes.

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
from dataclasses import dataclass
from typing import Any

from repowise.core.workspace.system_graph import SystemEdge, SystemGraph

_log = logging.getLogger("repowise.workspace.cycles")

# ---------------------------------------------------------------------------
# Constants (single source of truth)
# ---------------------------------------------------------------------------

#: Cap on the number of elementary cycles reported. Governance only needs the
#: representative loops, not every elementary cycle of a dense tangle; the true
#: count is reported separately and truncation is logged (never silent).
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
    """

    nodes: list[str]
    edge_ids: list[str]

    @property
    def length(self) -> int:
        return len(self.nodes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edge_ids": self.edge_ids,
            "length": self.length,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DependencyCycle:
        return cls(
            nodes=list(data.get("nodes", [])),
            edge_ids=list(data.get("edge_ids", [])),
        )


# ---------------------------------------------------------------------------
# Detection (pure)
# ---------------------------------------------------------------------------


def _structural_adjacency(edges: list[SystemEdge]) -> dict[tuple[str, str], str]:
    """Map each structural ``(source, target)`` pair to a representative edge id.

    Parallel structural edges (e.g. http + package between the same services)
    collapse onto one entry — a cycle highlights the seam, not every transport.
    Self-loops (``source == target``) are kept: a service depending on itself is
    a degenerate 1-cycle worth reporting.
    """
    adjacency: dict[tuple[str, str], str] = {}
    for edge in edges:
        if not edge.structural:
            continue
        key = (edge.source, edge.target)
        adjacency.setdefault(key, edge.id)
    return adjacency


def _cycle_edges(nodes: list[str], adjacency: dict[tuple[str, str], str]) -> list[str]:
    """Resolve the edge ids for the loop ``nodes[0] -> … -> nodes[0]``."""
    edge_ids: list[str] = []
    for i, src in enumerate(nodes):
        tgt = nodes[(i + 1) % len(nodes)]
        eid = adjacency.get((src, tgt))
        if eid is not None:
            edge_ids.append(eid)
    return edge_ids


def detect_cycles(graph: SystemGraph) -> list[DependencyCycle]:
    """Return the elementary structural dependency cycles in *graph*.

    Cycles are ordered shortest-first (the tightest loops are the most
    actionable), then lexicographically for stability. Capped at
    :data:`MAX_CYCLES`; truncation is logged. Returns ``[]`` when the structural
    subgraph is acyclic or NetworkX is unavailable.
    """
    adjacency = _structural_adjacency(graph.edges)
    if not adjacency:
        return []

    try:
        import networkx as nx
    except Exception:  # pragma: no cover - networkx is a hard dependency
        _log.warning("networkx unavailable; skipping cycle detection", exc_info=True)
        return []

    digraph = nx.DiGraph()
    digraph.add_nodes_from(n.id for n in graph.nodes)
    digraph.add_edges_from(adjacency.keys())

    cycles: list[DependencyCycle] = []
    try:
        for raw in nx.simple_cycles(digraph):
            edge_ids = _cycle_edges(raw, adjacency)
            if edge_ids:
                cycles.append(DependencyCycle(nodes=list(raw), edge_ids=edge_ids))
    except Exception:  # pragma: no cover - defensive
        _log.warning("cycle enumeration failed", exc_info=True)
        return []

    cycles.sort(key=lambda c: (c.length, c.nodes))
    if len(cycles) > MAX_CYCLES:
        _log.info(
            "Cycle detection found %d cycles; reporting the %d shortest.",
            len(cycles),
            MAX_CYCLES,
        )
        cycles = cycles[:MAX_CYCLES]
    return cycles
