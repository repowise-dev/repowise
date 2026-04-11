"""MCP Tool: get_execution_flows — trace how the codebase executes.

Hybrid approach: reads persisted entry-point scores from community_meta_json,
then recomputes BFS call-path traces on demand from stored call edges. This
avoids a dedicated execution_flows table while keeping the expensive scoring
off the hot path.
"""

from __future__ import annotations

import json
import time
from collections import deque
from typing import Any

from repowise.core.persistence.crud import (
    get_graph_edges_for_node,
    get_graph_node,
    get_graph_nodes_by_ids,
    get_top_entry_points,
)
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _get_repo
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._server import mcp


async def _bfs_trace(
    session: Any,
    repo_id: str,
    entry_id: str,
    max_depth: int,
    node_cache: dict[str, GraphNode],
) -> list[str]:
    """BFS trace from *entry_id* following ``calls`` edges.

    Returns an ordered list of symbol IDs in the trace. Uses greedy
    successor ordering (highest out-degree first for the primary path)
    and a visited set for cycle safety.
    """
    trace: list[str] = [entry_id]
    visited: set[str] = {entry_id}
    queue: deque[tuple[str, int]] = deque([(entry_id, 0)])

    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue

        # Get outgoing call edges
        edges = await get_graph_edges_for_node(
            session,
            repo_id,
            current,
            direction="callees",
            edge_types=["calls"],
            limit=20,
        )

        # Sort successors by confidence DESC for greedy primary path
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


@mcp.tool()
async def get_execution_flows(
    top_n: int = 10,
    max_depth: int = 8,
    entry_point: str | None = None,
    repo: str | None = None,
) -> dict:
    """Show how the codebase executes: top entry points and their call traces.

    Returns scored entry points with BFS call-path traces showing which
    functions are called in sequence and whether the flow crosses
    community boundaries.

    Args:
        top_n: Number of top entry points to trace (default 10).
        max_depth: Max trace depth per flow (default 8).
        entry_point: Trace from a specific symbol (overrides top_n scoring).
        repo: Usually omitted.
    """
    t0 = time.perf_counter()

    # Bound parameters
    top_n = max(1, min(top_n, 50))
    max_depth = max(1, min(max_depth, 20))

    async with get_session(_state._session_factory) as session:
        repository = await _get_repo(session, repo)
        repo_id = repository.id

        # Determine entry points
        entry_nodes: list[tuple[GraphNode, float]] = []

        if entry_point:
            # Trace from a specific symbol
            node = await get_graph_node(session, repo_id, entry_point)
            if node is None:
                return {
                    "entry_point": entry_point,
                    "error": f"Symbol not found: {entry_point!r}",
                    "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
                }
            try:
                meta = json.loads(node.community_meta_json or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            score = meta.get("entry_point_score", 0.0) or 0.0
            entry_nodes = [(node, score)]
        else:
            # Top-N scored entry points from DB
            top_nodes = await get_top_entry_points(
                session, repo_id, min_score=0.0, limit=top_n
            )
            for n in top_nodes:
                try:
                    meta = json.loads(n.community_meta_json or "{}")
                except (json.JSONDecodeError, TypeError):
                    meta = {}
                score = meta.get("entry_point_score", 0.0) or 0.0
                entry_nodes.append((n, score))

        if not entry_nodes:
            return {
                "total_entry_points": 0,
                "flows": [],
                "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
            }

        # BFS trace from each entry point
        node_cache: dict[str, GraphNode] = {}
        flows: list[dict[str, Any]] = []

        for ep_node, ep_score in entry_nodes:
            trace = await _bfs_trace(
                session, repo_id, ep_node.node_id, max_depth, node_cache
            )

            # Resolve community IDs for trace nodes
            trace_node_ids = [nid for nid in trace if nid not in node_cache]
            if trace_node_ids:
                batch = await get_graph_nodes_by_ids(session, repo_id, trace_node_ids)
                node_cache.update(batch)

            communities_visited: list[int] = []
            seen_communities: set[int] = set()
            for nid in trace:
                n = node_cache.get(nid)
                cid = n.community_id if n else 0
                if cid is not None and cid not in seen_communities:
                    seen_communities.add(cid)
                    communities_visited.append(cid)

            flows.append({
                "entry_point": ep_node.node_id,
                "entry_point_name": ep_node.name or ep_node.node_id.split("::")[-1],
                "entry_point_score": round(ep_score, 3),
                "trace": trace,
                "depth": len(trace) - 1,
                "crosses_community": len(communities_visited) > 1,
                "communities_visited": communities_visited,
            })

    # Sort by score descending
    flows.sort(key=lambda f: -f["entry_point_score"])

    result: dict[str, Any] = {
        "total_entry_points": len(flows),
        "flows": flows,
        "_meta": _build_meta(
            timing_ms=(time.perf_counter() - t0) * 1000,
            hint="Use get_callers_callees on any trace node for detail.",
        ),
    }
    return result
