"""MCP Tool: get_community — explore architectural communities/clusters.

Returns the community a file belongs to, its members, cohesion score,
label, and neighboring communities. Helps Claude Code understand module
boundaries, refactoring safety, and architectural coupling.
"""

from __future__ import annotations

import json
import time
from typing import Any

from repowise.core.persistence.crud import (
    get_community_members,
    get_cross_community_edges,
    get_graph_node,
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


def _parse_community_meta(node: GraphNode) -> dict[str, Any]:
    """Extract label and cohesion from community_meta_json."""
    try:
        meta = json.loads(node.community_meta_json or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}
    return meta


@mcp.tool()
async def get_community(
    target: str,
    include_members: bool = True,
    member_limit: int = 30,
    repo: str | None = None,
) -> dict:
    """Show the architectural community/cluster a file belongs to.

    Returns community label, cohesion score, members, and neighboring
    communities. Useful for understanding module boundaries and
    refactoring safety.

    Args:
        target: File path (resolves to its community) or community ID (e.g. "3").
        include_members: Include member file list (default true).
        member_limit: Max members to return (default 30).
        repo: Usually omitted.
    """
    if repo == "all":
        return _unsupported_repo_all("get_community")
    ctx = await _resolve_repo_context(repo)

    t0 = time.perf_counter()
    if not target or not target.strip():
        return {
            "target": target,
            "error": "target is required",
            "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
        }

    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        repo_id = repository.id

        community_id: int | None = None
        label: str = ""
        cohesion: float = 0.0

        # Resolve target to community_id
        if target.strip().isdigit():
            community_id = int(target.strip())
        else:
            node = await get_graph_node(session, repo_id, target)
            if node is None:
                return {
                    "target": target,
                    "error": f"Node not found in graph: {target!r}. Check the file path.",
                    "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
                }
            community_id = node.community_id
            meta = _parse_community_meta(node)
            label = meta.get("label", "")
            cohesion = meta.get("cohesion", 0.0)

        if community_id is None:
            return {
                "target": target,
                "error": "Could not resolve community ID",
                "_meta": _build_meta(timing_ms=(time.perf_counter() - t0) * 1000),
            }

        # Get members
        members_list: list[dict[str, Any]] = []
        member_count = 0
        if include_members:
            members = await get_community_members(
                session, repo_id, community_id, limit=member_limit
            )
            member_count = len(members)
            for m in members:
                entry: dict[str, Any] = {
                    "path": m.node_id,
                    "pagerank": round(m.pagerank, 6) if m.pagerank else 0.0,
                    "is_entry_point": m.is_entry_point,
                }
                members_list.append(entry)

                # If we haven't found label/cohesion yet (target was a community ID),
                # grab it from the first member
                if not label:
                    meta = _parse_community_meta(m)
                    label = meta.get("label", "")
                    cohesion = meta.get("cohesion", 0.0)
        else:
            # Still need count
            members = await get_community_members(
                session, repo_id, community_id, limit=1000
            )
            member_count = len(members)
            if not label and members:
                meta = _parse_community_meta(members[0])
                label = meta.get("label", "")
                cohesion = meta.get("cohesion", 0.0)

        # Get neighboring communities
        cross_edges = await get_cross_community_edges(session, repo_id, community_id)

        # Resolve labels for neighbor communities from any member node
        neighbor_communities: list[dict[str, Any]] = []
        for ce in cross_edges[:10]:  # cap at 10 neighbors
            neighbor_id = ce["target_community_id"]
            # Get one member to read the label
            neighbor_members = await get_community_members(
                session, repo_id, neighbor_id, limit=1
            )
            neighbor_label = ""
            if neighbor_members:
                nmeta = _parse_community_meta(neighbor_members[0])
                neighbor_label = nmeta.get("label", "")
            neighbor_communities.append({
                "community_id": neighbor_id,
                "label": neighbor_label,
                "cross_edge_count": ce["edge_count"],
            })

    result: dict[str, Any] = {
        "target": target,
        "community_id": community_id,
        "label": label or f"cluster_{community_id}",
        "cohesion": round(cohesion, 3),
        "member_count": member_count,
    }

    if include_members:
        result["members"] = members_list
        result["truncated"] = member_count >= member_limit

    result["neighboring_communities"] = neighbor_communities

    hint = None
    if neighbor_communities:
        hint = "Use get_callers_callees on key symbols to understand cross-community coupling."
    result["_meta"] = _build_meta(
        timing_ms=(time.perf_counter() - t0) * 1000,
        hint=hint,
    )
    return result
