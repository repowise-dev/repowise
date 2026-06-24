"""Repo-level graph signals shared by the graph-native detectors.

Move Method and Break Cycle reason over the symbol/file graph rather than a
single file, but the detector contract is per-file. To keep that contract
intact — and avoid each detector re-deriving global structure on every file —
the health engine precomputes the few repo-wide signals here ONCE before the
per-file pass and threads the per-file slice into ``RefactoringContext``.

All functions are pure and deterministic: the same graph yields the same
output, with stable (sorted) ordering, and degrade to empty on a missing
graph rather than raising.
"""

from __future__ import annotations

from typing import Any

# File->file edge types that constitute a structural dependency cycle.
# ``imports`` is the actionable, invertible edge; the others are carried so a
# cycle that closes through a framework/type edge is still reported. Today the
# only file-level edge type the graph emits is ``imports`` (the rest are
# symbol-level), so this closely tracks ``GraphBuilder.file_subgraph`` (which
# drops only git ``co_changes``) and the persisted ``graph_node_membership``
# rows; the two could drift only if a new file-level edge type is added to one
# definition but not the other.
_CYCLE_EDGE_TYPES = ("imports", "framework", "dynamic", "type_use", "extends", "implements")


def build_file_scc_index(graph: Any) -> dict[str, tuple[str, ...]]:
    """Map each file in a non-trivial dependency cycle to its sorted members.

    Returns ``{file_path: (member, ...)}`` covering only files that sit in a
    strongly-connected component of size >= 2 (a real cycle). Files with no
    cycle — the overwhelming majority — are absent. The member tuple is
    sorted, so the canonical anchor (``members[0]``) is deterministic.
    """
    if graph is None:
        return {}
    try:
        import networkx as nx
    except Exception:
        return {}

    fg: nx.DiGraph = nx.DiGraph()
    for node, data in graph.nodes(data=True):
        if data.get("node_type", "file") in ("file", "external"):
            fg.add_node(node)
    for u, v, data in graph.edges(data=True):
        if u == v:
            continue
        if data.get("edge_type") in _CYCLE_EDGE_TYPES and u in fg and v in fg:
            fg.add_edge(u, v)

    out: dict[str, tuple[str, ...]] = {}
    for scc in nx.strongly_connected_components(fg):
        if len(scc) < 2:
            continue
        members = tuple(sorted(scc))
        for node in scc:
            out[node] = members
    return out


def cycle_edges(graph: Any, members: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return the directed dependency edges *within* a cycle's member set.

    Deterministic (sorted). Self-loops and edges leaving the cycle are
    excluded — only the edges that keep the cycle strongly connected.
    """
    if graph is None:
        return []
    member_set = set(members)
    edges: list[tuple[str, str]] = []
    for u, v, data in graph.edges(data=True):
        if u == v or u not in member_set or v not in member_set:
            continue
        if data.get("edge_type") in _CYCLE_EDGE_TYPES:
            edges.append((u, v))
    return sorted(set(edges))
