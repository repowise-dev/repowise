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
    Every qualifying edge originates at a member, so walking each member's
    out-edges covers the same set as a full-graph edge scan at a fraction
    of the cost (this runs once per cycle-member file).
    """
    if graph is None:
        return []
    member_set = set(members)
    edges: list[tuple[str, str]] = []
    for u in members:
        if u not in graph:
            continue
        for _u, v, data in graph.out_edges(u, data=True):
            if u == v or v not in member_set:
                continue
            if data.get("edge_type") in _CYCLE_EDGE_TYPES:
                edges.append((u, v))
    return sorted(set(edges))


def build_methods_by_file(graph: Any) -> dict[str, tuple[str, ...]]:
    """Methods defined in each file, precomputed in one graph sweep.

    Mirrors ``MoveMethodDetector._methods_in_file`` for every file at once:
    a file's ``defines``-edge methods are authoritative; a file with none
    falls back to the ``file::``-prefix scan over symbol nodes. Doing both
    in one pass here replaces a per-file full-node scan that dominated the
    health evaluate loop on large repos. Files with no methods are absent.
    """
    if graph is None:
        return {}
    edge_map: dict[str, set[str]] = {}
    prefix_map: dict[str, set[str]] = {}
    for node_id, data in graph.nodes(data=True):
        if (
            data.get("node_type") == "symbol"
            and data.get("kind") == "method"
            and "::" in node_id
        ):
            prefix_map.setdefault(node_id.split("::", 1)[0], set()).add(node_id)
    for u, v, data in graph.edges(data=True):
        if data.get("edge_type") != "defines":
            continue
        node = graph.nodes[v] if v in graph else None
        if node is not None and node.get("kind") == "method":
            edge_map.setdefault(u, set()).add(v)
    out: dict[str, tuple[str, ...]] = {}
    for file_path in set(edge_map) | set(prefix_map):
        methods = edge_map.get(file_path) or prefix_map.get(file_path) or set()
        if methods:
            out[file_path] = tuple(sorted(methods))
    return out
