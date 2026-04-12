"""MCP Tool: get_callers_callees — find who calls a symbol and what it calls.

Provides symbol-level call graph navigation for AI coding assistants.
Supports call edges, heritage edges (extends/implements), and any other
edge type stored in the graph. Heritage queries use the same code path
via the ``edge_types`` parameter — no separate tool needed.

Resolution strategy:
  1. Exact match on GraphNode.node_id (the canonical "{path}::{name}" key)
  2. Fuzzy match on GraphNode.name column (bare name, e.g. "verify_token")
"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.crud import (
    get_graph_edges_for_node,
    get_graph_node,
    get_graph_nodes_by_ids,
)
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._server import mcp

_DEFAULT_EDGE_TYPES = ["calls"]


async def _resolve_symbol_node(
    session: Any, repo_id: str, symbol_id: str
) -> GraphNode | None:
    """Resolve a symbol ID to a GraphNode, with fuzzy fallback."""
    # 1. Exact match
    node = await get_graph_node(session, repo_id, symbol_id)
    if node and node.node_type == "symbol":
        return node

    # 2. Fuzzy: extract bare name and search
    bare_name = symbol_id
    if "::" in symbol_id:
        bare_name = symbol_id.split("::")[-1]
    # Try matching by the name column
    result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "symbol",
            GraphNode.name == bare_name,
        )
    )
    rows = list(result.scalars().all())
    if not rows:
        return None

    # If the symbol_id contains a file path hint, prefer matching file
    if "::" in symbol_id:
        file_hint = symbol_id.split("::")[0]
        for r in rows:
            if r.file_path == file_hint:
                return r

    # Return the first match (deterministic: lowest node_id)
    rows.sort(key=lambda r: r.node_id)
    return rows[0]


def _node_summary(node: GraphNode) -> dict[str, Any]:
    """Build a compact summary dict for a graph node."""
    out: dict[str, Any] = {
        "symbol_id": node.node_id,
        "name": node.name or node.node_id,
        "kind": node.kind,
        "file": node.file_path or node.node_id,
    }
    if node.start_line is not None:
        out["start_line"] = node.start_line
    if node.signature:
        out["signature"] = node.signature
    return out


@mcp.tool()
async def get_callers_callees(
    symbol_id: str,
    direction: str = "both",
    edge_types: list[str] | None = None,
    limit: int = 20,
    repo: str | None = None,
) -> dict:
    """Find who calls a symbol and what it calls. Also works for class hierarchy.

    Use direction="callers" to find what calls/extends this symbol.
    Use edge_types=["extends","implements"] for class hierarchy queries.
    Default edge_types is ["calls"].

    Args:
        symbol_id: "path/to/file.py::FunctionName" format from get_context.
        direction: "callers", "callees", or "both".
        edge_types: Filter by edge type. Default ["calls"].
        limit: Max results per direction (default 20).
        repo: Usually omitted.
    """
    if repo == "all":
        return _unsupported_repo_all("get_callers_callees")
    ctx = await _resolve_repo_context(repo)

    t0 = time.perf_counter()
    if not symbol_id or not symbol_id.strip():
        return {
            "symbol_id": symbol_id,
            "error": "symbol_id is required",
            "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
        }

    if edge_types is None:
        edge_types = _DEFAULT_EDGE_TYPES

    if direction not in ("callers", "callees", "both"):
        direction = "both"

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        repo_id = repository.id

        # Resolve symbol
        node = await _resolve_symbol_node(session, repo_id, symbol_id)
        if node is None:
            return {
                "symbol_id": symbol_id,
                "error": (
                    f"Symbol not found in graph: {symbol_id!r}. Use get_context "
                    "to list available symbols, then try with the exact "
                    "symbol_id from that response."
                ),
                "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
            }

        # Map direction to CRUD direction param
        crud_direction = direction
        if direction == "both":
            crud_direction = "both"

        edges = await get_graph_edges_for_node(
            session,
            repo_id,
            node.node_id,
            direction=crud_direction,
            edge_types=edge_types,
            limit=limit,
        )

        # Collect node IDs to hydrate
        other_ids: list[str] = []
        for e in edges:
            if e.source_node_id != node.node_id:
                other_ids.append(e.source_node_id)
            if e.target_node_id != node.node_id:
                other_ids.append(e.target_node_id)

        node_map = await get_graph_nodes_by_ids(session, repo_id, list(set(other_ids)))

    # Build caller/callee lists
    callers: list[dict[str, Any]] = []
    callees: list[dict[str, Any]] = []

    for e in edges:
        is_caller = e.target_node_id == node.node_id
        other_id = e.source_node_id if is_caller else e.target_node_id
        other_node = node_map.get(other_id)

        entry: dict[str, Any] = {
            "symbol_id": other_id,
            "name": other_node.name if other_node else other_id.split("::")[-1] if "::" in other_id else other_id,
            "kind": other_node.kind if other_node else None,
            "file": other_node.file_path if other_node else (other_id.split("::")[0] if "::" in other_id else other_id),
            "confidence": e.confidence,
            "edge_type": e.edge_type,
        }
        if other_node and other_node.start_line is not None:
            entry["start_line"] = other_node.start_line

        if is_caller:
            callers.append(entry)
        else:
            callees.append(entry)

    # Sort by confidence DESC, then by name
    callers.sort(key=lambda x: (-x.get("confidence", 0), x.get("name", "")))
    callees.sort(key=lambda x: (-x.get("confidence", 0), x.get("name", "")))

    result: dict[str, Any] = {
        "symbol_id": node.node_id,
        "symbol": _node_summary(node),
    }

    if direction in ("callers", "both"):
        result["callers"] = callers
        result["caller_count"] = len(callers)
    if direction in ("callees", "both"):
        result["callees"] = callees
        result["callee_count"] = len(callees)

    truncated = (
        (direction in ("callers", "both") and len(callers) >= limit)
        or (direction in ("callees", "both") and len(callees) >= limit)
    )
    result["truncated"] = truncated

    hint = None
    if callers or callees:
        hint = "Use get_symbol(symbol_id) to read the source of any caller/callee."
    result["_meta"] = _build_meta(
        timing_ms=(time.perf_counter() - t0) * 1000,
        hint=hint,
    )
    return result
