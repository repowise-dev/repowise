"""MCP Tool: get_architecture — workspace architecture-complexity metrics.

Answers "how coupled is this whole system, and which services are its
architectural core?" with the standard complexity metrics over the system graph:
propagation cost, the cyclic core, per-service core-periphery roles, and a single
deterministic 1-10 architecture score. Read-only; the full payload is also on
``GET /api/workspace/architecture``.
"""

from __future__ import annotations

from typing import Any

from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _is_workspace_mode
from repowise.server.mcp_server._meta import build_meta as _build_meta

#: Cap the per-role member lists in the agent payload; full roles are on REST.
_MCP_CORE_MEMBER_LIMIT = 25


@mcp.tool()
async def get_architecture() -> dict[str, Any]:
    """Workspace architecture metrics — coupling, core, and a 1-10 score.

    Workspace-only. Reports propagation cost (the share of other services the
    average service can reach transitively), the largest cyclic core, the
    core-periphery role breakdown, and a deterministic 1-10 architecture score
    (higher = lower coupling, smaller core). Call to gauge overall system
    structure before a cross-service refactor, or to compare against a prior
    snapshot. Structural edges only; co-change is excluded.
    """
    if not _is_workspace_mode():
        return {
            "error": "get_architecture is only available in workspace mode.",
            "_meta": _build_meta(),
        }

    enricher = _state._cross_repo_enricher
    metrics = enricher.get_architecture_metrics() if enricher is not None else None
    if not metrics:
        return {
            "error": (
                "No system graph is available yet. Run `repowise update --workspace` "
                "to build cross-repo relationships first."
            ),
            "_meta": _build_meta(),
        }

    core_members = metrics.get("core_members", [])
    shown_core = core_members[:_MCP_CORE_MEMBER_LIMIT]
    breakdown = metrics.get("role_breakdown", {})

    if metrics.get("core_size", 0):
        core_phrase = (
            f"a cyclic core of {metrics['core_size']} service(s) "
            f"({metrics.get('core_ratio', 0) * 100:.0f}% of the system)"
        )
    else:
        core_phrase = "no cyclic core (acyclic structure)"
    summary = (
        f"Architecture score {metrics.get('score', 0)}/10 "
        f"({metrics.get('architecture_type', 'hierarchical')}): propagation cost "
        f"{metrics.get('propagation_cost_pct', 0)}%, {core_phrase}, "
        f"{metrics.get('cycle_count', 0)} dependency cycle(s)."
    )

    return {
        "score": metrics.get("score", 0.0),
        "architecture_type": metrics.get("architecture_type", "hierarchical"),
        "propagation_cost_pct": metrics.get("propagation_cost_pct", 0.0),
        "node_count": metrics.get("node_count", 0),
        "core_size": metrics.get("core_size", 0),
        "core_ratio": metrics.get("core_ratio", 0.0),
        "core_members": shown_core,
        "core_members_truncated": max(0, len(core_members) - len(shown_core)),
        "cycle_count": metrics.get("cycle_count", 0),
        "conformance_violations": metrics.get("conformance_violations", 0),
        "role_breakdown": breakdown,
        "summary": summary,
        "_meta": _build_meta(),
    }
