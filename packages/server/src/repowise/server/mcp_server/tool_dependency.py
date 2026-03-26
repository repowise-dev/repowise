"""MCP Tool 6: get_dependency_path — dependency graph path finding."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    GitMetadata,
    GraphEdge,
    GraphNode,
)
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _get_repo
from repowise.server.mcp_server._server import mcp


@mcp.tool()
async def get_dependency_path(
    source: str, target: str, repo: str | None = None
) -> dict:
    """Find how two files/modules are connected in the dependency graph.

    When no direct path exists, returns visual context: nearest common
    ancestors, shared neighbors, community analysis, and bridge suggestions
    to help debug architectural silos.

    Args:
        source: Source file or module path.
        target: Target file or module path.
        repo: Repository path, name, or ID.
    """
    async with get_session(_state._session_factory) as session:
        repository = await _get_repo(session, repo)

        edge_result = await session.execute(
            select(GraphEdge).where(
                GraphEdge.repository_id == repository.id,
            )
        )
        edges = edge_result.scalars().all()

        node_result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository.id,
            )
        )
        nodes = node_result.scalars().all()

    try:
        import networkx as nx
    except ImportError:
        return {"path": [], "distance": -1, "explanation": "networkx not available for path queries"}

    G = nx.DiGraph()
    for e in edges:
        G.add_edge(
            e.source_node_id,
            e.target_node_id,
            edge_type=getattr(e, "edge_type", None) or "imports",
        )

    if source not in G:
        return {"path": [], "distance": -1, "explanation": f"Source node '{source}' not found in graph"}
    if target not in G:
        return {"path": [], "distance": -1, "explanation": f"Target node '{target}' not found in graph"}

    try:
        path = nx.shortest_path(G, source, target)
    except nx.NetworkXNoPath:
        result_data: dict[str, Any] = {
            "path": [],
            "distance": -1,
            "explanation": "No direct dependency path found",
            "visual_context": _build_visual_context(G, source, target, nodes, nx),
        }
        # Phase 4: check co-change coupling even without import dependency
        async with get_session(_state._session_factory) as session:
            src_res = await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repository.id,
                    GitMetadata.file_path == source,
                )
            )
            src_meta = src_res.scalar_one_or_none()
            if src_meta and src_meta.co_change_partners_json:
                partners = json.loads(src_meta.co_change_partners_json)
                for p in partners:
                    partner_path = p.get("file_path", "")
                    if partner_path == target:
                        result_data["co_change_signal"] = {
                            "co_change_count": p.get("co_change_count", 0),
                            "last_co_change": p.get("last_co_change"),
                            "note": (
                                "No import dependency, but these files co-change "
                                "frequently — likely logical coupling."
                            ),
                        }
                        break
        return result_data

    # Build path with relationships
    path_with_info = []
    for i, node in enumerate(path):
        relationship = ""
        if i < len(path) - 1:
            next_node = path[i + 1]
            relationship = G[node][next_node].get("edge_type", "imports")
        path_with_info.append({"node": node, "relationship": relationship})

    return {
        "path": path_with_info,
        "distance": len(path) - 1,
        "explanation": f"Shortest path from {source} to {target} has {len(path) - 1} hops",
    }


def _build_visual_context(
    G: Any, source: str, target: str, nodes: list, nx: Any,
) -> dict:
    """Build diagnostic context when no directed path exists."""
    node_meta = {n.node_id: n for n in nodes}
    context: dict[str, Any] = {}

    # --- Reverse path check ---
    try:
        rev_path = nx.shortest_path(G, target, source)
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
    U = G.to_undirected()
    source_reachable = set(nx.single_source_shortest_path_length(U, source))
    target_reachable = set(nx.single_source_shortest_path_length(U, target))
    common = source_reachable & target_reachable
    common.discard(source)
    common.discard(target)

    if common:
        source_dist = nx.single_source_shortest_path_length(U, source)
        target_dist = nx.single_source_shortest_path_length(U, target)
        scored = [
            (node, source_dist[node] + target_dist[node])
            for node in common
        ]
        scored.sort(key=lambda x: x[1])
        context["nearest_common_ancestors"] = [
            {"node": node, "distance_from_source": source_dist[node],
             "distance_from_target": target_dist[node]}
            for node, _ in scored[:5]
        ]
    else:
        context["nearest_common_ancestors"] = []

    # --- Shared neighbors (direct) ---
    source_neighbors = set(G.predecessors(source)) | set(G.successors(source))
    target_neighbors = set(G.predecessors(target)) | set(G.successors(target))
    shared = source_neighbors & target_neighbors
    if shared:
        context["shared_neighbors"] = sorted(shared)
    else:
        context["shared_neighbors"] = []

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

    # --- Bridge suggestions: high-centrality nodes between communities ---
    if src_community is not None and tgt_community is not None and src_community != tgt_community:
        # Find nodes that have edges crossing these two communities
        bridge_nodes = []
        nodes_by_community: dict[int, set[str]] = {}
        for n in nodes:
            nodes_by_community.setdefault(n.community_id, set()).add(n.node_id)

        src_community_nodes = nodes_by_community.get(src_community, set())
        tgt_community_nodes = nodes_by_community.get(tgt_community, set())

        for node_id in G.nodes():
            neighbors = set(G.predecessors(node_id)) | set(G.successors(node_id))
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
    # Check if they're in completely disconnected components
    components = list(nx.weakly_connected_components(G))
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
