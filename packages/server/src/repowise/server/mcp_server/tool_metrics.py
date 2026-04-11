"""MCP Tool: get_graph_metrics — importance metrics for a file or symbol.

Returns PageRank, betweenness centrality, community info, degree counts,
and percentile ranks. Helps Claude Code assess how central/important a
file or symbol is in the dependency graph.
"""

from __future__ import annotations

import json
import time
from typing import Any

from repowise.core.persistence.crud import (
    get_all_file_metrics,
    get_graph_node,
    get_node_degree_counts,
)
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _get_repo
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._server import mcp


def _percentile_rank(value: float, all_values: list[float]) -> int:
    """Compute percentile rank (0-100) of *value* within *all_values*."""
    if not all_values:
        return 0
    count_below = sum(1 for v in all_values if v < value)
    return round(100 * count_below / len(all_values))


@mcp.tool()
async def get_graph_metrics(
    target: str,
    repo: str | None = None,
) -> dict:
    """Get importance metrics for a file or symbol: PageRank, centrality, community.

    Returns raw scores plus percentile ranks across the repo. Helps assess
    how central/important a file or symbol is in the dependency graph.

    Args:
        target: File path (e.g. "src/auth/service.py") or symbol ID ("path::Name").
        repo: Usually omitted.
    """
    t0 = time.perf_counter()
    if not target or not target.strip():
        return {
            "target": target,
            "error": "target is required",
            "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
        }

    async with get_session(_state._session_factory) as session:
        repository = await _get_repo(session, repo)
        repo_id = repository.id

        # Try exact lookup first
        node = await get_graph_node(session, repo_id, target)
        if node is None:
            return {
                "target": target,
                "error": (
                    f"Node not found in graph: {target!r}. "
                    "Use get_context to find exact file paths or symbol IDs."
                ),
                "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
            }

        # Parse community metadata
        try:
            meta = json.loads(node.community_meta_json or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}

        community_label = meta.get("label", "")
        entry_point_score = meta.get("entry_point_score")

        # Degree counts
        degrees = await get_node_degree_counts(session, repo_id, node.node_id)

        # Percentile computation — compare against same node_type peers
        all_nodes = await get_all_file_metrics(session, repo_id)
        pr_values = [n.pagerank for n in all_nodes if n.pagerank is not None]
        bt_values = [n.betweenness for n in all_nodes if n.betweenness is not None]

        pr_pct = _percentile_rank(node.pagerank or 0.0, pr_values)
        bt_pct = _percentile_rank(node.betweenness or 0.0, bt_values)

    result: dict[str, Any] = {
        "target": node.node_id,
        "node_type": node.node_type,
        "pagerank": round(node.pagerank or 0.0, 6),
        "pagerank_percentile": pr_pct,
        "betweenness": round(node.betweenness or 0.0, 6),
        "betweenness_percentile": bt_pct,
        "community_id": node.community_id,
        "community_label": community_label or None,
        "is_entry_point": node.is_entry_point,
        "in_degree": degrees["in_degree"],
        "out_degree": degrees["out_degree"],
    }

    if node.node_type == "symbol":
        result["entry_point_score"] = (
            round(entry_point_score, 3) if entry_point_score is not None else None
        )
        result["kind"] = node.kind
        result["file"] = node.file_path

    hint = None
    if node.node_type == "file" and node.community_id is not None:
        hint = f"Use get_community('{node.node_id}') to see all community members."
    elif node.node_type == "symbol":
        hint = f"Use get_callers_callees('{node.node_id}') to explore call relationships."

    result["_meta"] = _build_meta(
        timing_ms=(time.perf_counter() - t0) * 1000,
        hint=hint,
    )
    return result
