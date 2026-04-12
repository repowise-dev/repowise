"""Shared graph query utilities used by both MCP tools and REST routers.

This module avoids duplicating BFS trace logic and community-meta parsing
across `tool_flows.py` and `routers/graph.py`.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from repowise.core.persistence.crud import (
    get_graph_edges_for_node,
    get_graph_nodes_by_ids,
)
from repowise.core.persistence.models import GraphNode


def parse_community_meta(node: GraphNode) -> dict[str, Any]:
    """Safely parse ``community_meta_json`` from a GraphNode."""
    try:
        return json.loads(node.community_meta_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def community_label(node: GraphNode) -> str:
    """Extract human-readable community label, falling back to 'cluster_N'."""
    meta = parse_community_meta(node)
    return meta.get("label") or f"cluster_{node.community_id}"


def community_cohesion(node: GraphNode) -> float:
    """Extract cohesion score from community_meta_json."""
    meta = parse_community_meta(node)
    return float(meta.get("cohesion", 0.0) or 0.0)


def entry_point_score(node: GraphNode) -> float:
    """Extract entry_point_score from community_meta_json (symbol nodes only)."""
    meta = parse_community_meta(node)
    return float(meta.get("entry_point_score", 0.0) or 0.0)


def percentile_rank(value: float, all_values: list[float]) -> int:
    """Compute the percentile rank (0–100) of *value* within *all_values*."""
    if not all_values:
        return 0
    count_below = sum(1 for v in all_values if v < value)
    return round(100.0 * count_below / len(all_values))


async def bfs_trace(
    session: Any,
    repo_id: str,
    entry_id: str,
    max_depth: int,
    node_cache: dict[str, GraphNode] | None = None,
) -> list[str]:
    """BFS trace from *entry_id* following ``calls`` edges.

    Returns an ordered list of symbol IDs in the trace.  Uses greedy
    successor ordering (highest confidence first for the primary path)
    and a visited set for cycle safety.
    """
    if node_cache is None:
        node_cache = {}

    trace: list[str] = [entry_id]
    visited: set[str] = {entry_id}
    queue: deque[tuple[str, int]] = deque([(entry_id, 0)])

    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue

        edges = await get_graph_edges_for_node(
            session,
            repo_id,
            current,
            direction="callees",
            edge_types=["calls"],
            limit=20,
        )

        successors: list[tuple[str, float]] = []
        for e in edges:
            if e.target_node_id not in visited:
                successors.append((e.target_node_id, e.confidence or 0.0))

        successors.sort(key=lambda x: -x[1])

        for target_id, _ in successors:
            if target_id in visited:
                continue
            visited.add(target_id)
            trace.append(target_id)
            queue.append((target_id, depth + 1))

    return trace


async def resolve_trace_communities(
    session: Any,
    repo_id: str,
    trace: list[str],
    node_cache: dict[str, GraphNode],
) -> tuple[list[int], bool]:
    """Resolve community IDs for trace nodes.

    Returns ``(communities_visited, crosses_community)``.
    """
    missing = [nid for nid in trace if nid not in node_cache]
    if missing:
        batch = await get_graph_nodes_by_ids(session, repo_id, missing)
        node_cache.update(batch)

    communities_visited: list[int] = []
    seen: set[int] = set()
    for nid in trace:
        n = node_cache.get(nid)
        cid = n.community_id if n else 0
        if cid is not None and cid not in seen:
            seen.add(cid)
            communities_visited.append(cid)

    return communities_visited, len(communities_visited) > 1


def build_visual_context(
    graph: Any,
    source: str,
    target: str,
    nodes: list,
    nx: Any,
) -> dict:
    """Build diagnostic context when no directed path exists.

    Used by the REST API router (routers/graph.py) for dependency path queries.
    Moved here from tool_dependency.py to avoid importing the @mcp.tool()-decorated
    module just for this helper.
    """
    node_meta = {n.node_id: n for n in nodes}
    context: dict[str, Any] = {}

    # --- Reverse path check ---
    try:
        rev_path = nx.shortest_path(graph, target, source)
        context["reverse_path"] = {
            "exists": True,
            "path": rev_path,
            "distance": len(rev_path) - 1,
            "note": f"A path exists in the reverse direction ({target} -> {source}). "
            "The dependency flows the other way.",
        }
    except nx.NetworkXNoPath:
        context["reverse_path"] = {"exists": False}

    # --- Nearest common ancestors (via undirected graph) ---
    undirected = graph.to_undirected()
    source_reachable = set(nx.single_source_shortest_path_length(undirected, source))
    target_reachable = set(nx.single_source_shortest_path_length(undirected, target))
    common = source_reachable & target_reachable
    common.discard(source)
    common.discard(target)

    if common:
        source_dist = nx.single_source_shortest_path_length(undirected, source)
        target_dist = nx.single_source_shortest_path_length(undirected, target)
        scored = [(node, source_dist[node] + target_dist[node]) for node in common]
        scored.sort(key=lambda x: x[1])
        context["nearest_common_ancestors"] = [
            {
                "node": node,
                "distance_from_source": source_dist[node],
                "distance_from_target": target_dist[node],
            }
            for node, _ in scored[:5]
        ]
    else:
        context["nearest_common_ancestors"] = []

    # --- Shared neighbors (direct) ---
    source_neighbors = set(graph.predecessors(source)) | set(graph.successors(source))
    target_neighbors = set(graph.predecessors(target)) | set(graph.successors(target))
    shared = source_neighbors & target_neighbors
    context["shared_neighbors"] = sorted(shared) if shared else []

    # --- Community analysis ---
    src_meta = node_meta.get(source)
    tgt_meta = node_meta.get(target)
    src_community = src_meta.community_id if src_meta else None
    tgt_community = tgt_meta.community_id if tgt_meta else None

    context["community"] = {
        "source_community": src_community,
        "target_community": tgt_community,
        "same_community": src_community is not None and src_community == tgt_community,
    }

    # --- Bridge suggestions ---
    if src_community is not None and tgt_community is not None and src_community != tgt_community:
        bridge_nodes = []
        nodes_by_community: dict[int, set[str]] = {}
        for n in nodes:
            nodes_by_community.setdefault(n.community_id, set()).add(n.node_id)

        src_community_nodes = nodes_by_community.get(src_community, set())
        tgt_community_nodes = nodes_by_community.get(tgt_community, set())

        for node_id in graph.nodes():
            neighbors = set(graph.predecessors(node_id)) | set(graph.successors(node_id))
            touches_src = bool(neighbors & src_community_nodes)
            touches_tgt = bool(neighbors & tgt_community_nodes)
            if touches_src and touches_tgt:
                meta = node_meta.get(node_id)
                bridge_nodes.append({
                    "node": node_id,
                    "pagerank": meta.pagerank if meta else 0.0,
                })
        bridge_nodes.sort(key=lambda x: x["pagerank"], reverse=True)
        context["bridge_suggestions"] = bridge_nodes[:5]
    else:
        context["bridge_suggestions"] = []

    # --- Connectivity summary ---
    components = list(nx.weakly_connected_components(graph))
    src_comp = next((c for c in components if source in c), set())
    tgt_comp = next((c for c in components if target in c), set())
    actually_disconnected = src_comp != tgt_comp

    if actually_disconnected:
        context["disconnected"] = True
        context["source_component_size"] = len(src_comp)
        context["target_component_size"] = len(tgt_comp)
        context["suggestion"] = (
            "These nodes are in completely separate dependency clusters with "
            "no shared connections. Look for shared configuration files, API "
            "contracts, or event buses that should bridge them."
        )
    else:
        context["disconnected"] = False
        if context["nearest_common_ancestors"]:
            top = context["nearest_common_ancestors"][0]["node"]
            context["suggestion"] = (
                f"No direct path, but both nodes connect through '{top}'. "
                "This shared dependency may be the architectural bridge point."
            )
        elif context["shared_neighbors"]:
            context["suggestion"] = (
                f"No direct path, but they share neighbor(s): "
                f"{', '.join(context['shared_neighbors'])}. "
                "These shared files may serve as the missing link."
            )
        elif context["reverse_path"].get("exists"):
            context["suggestion"] = (
                "No direct path in this direction, but a reverse path exists. "
                "The dependency flows the other way."
            )
        else:
            context["suggestion"] = (
                "These nodes are in the same cluster but have no direct "
                "or reverse dependency. Check for indirect connections."
            )

    return context
