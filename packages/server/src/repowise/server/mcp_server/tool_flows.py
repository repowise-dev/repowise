"""MCP Tool: get_execution_flows — trace how the codebase executes.

Hybrid approach: reads persisted entry-point scores from community_meta_json,
then recomputes BFS call-path traces on demand from stored call edges. This
avoids a dedicated execution_flows table while keeping the expensive scoring
off the hot path.
"""

from __future__ import annotations

import time
from typing import Any

from repowise.core.persistence.crud import (
    get_graph_node,
    get_graph_nodes_by_ids,
    get_top_entry_points,
)
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode
from repowise.server.mcp_server._graph_utils import (
    bfs_trace,
    entry_point_score as _ep_score,
    resolve_trace_communities,
)
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._server import mcp


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
    if repo == "all":
        return _unsupported_repo_all("get_execution_flows")
    ctx = await _resolve_repo_context(repo)

    t0 = time.perf_counter()

    # Bound parameters
    top_n = max(1, min(top_n, 50))
    max_depth = max(1, min(max_depth, 20))

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
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
            entry_nodes = [(node, _ep_score(node))]
        else:
            # Top-N scored entry points from DB
            top_nodes = await get_top_entry_points(
                session, repo_id, min_score=0.0, limit=top_n
            )
            for n in top_nodes:
                entry_nodes.append((n, _ep_score(n)))

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
            trace = await bfs_trace(
                session, repo_id, ep_node.node_id, max_depth, node_cache
            )

            communities_visited, crosses = await resolve_trace_communities(
                session, repo_id, trace, node_cache
            )

            flows.append({
                "entry_point": ep_node.node_id,
                "entry_point_name": ep_node.name or ep_node.node_id.split("::")[-1],
                "entry_point_score": round(ep_score, 3),
                "trace": trace,
                "depth": len(trace) - 1,
                "crosses_community": crosses,
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
