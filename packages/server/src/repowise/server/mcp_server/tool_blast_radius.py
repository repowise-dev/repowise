"""MCP Tool: get_blast_radius — cross-repo downstream impact (workspace only).

Answers "if I change this service, what breaks across the other repos?" by
traversing the workspace system graph. Structural dependencies (contracts,
package deps) are ranked above behavioral co-change. Mirrors the single-repo
change-risk vocabulary (``get_risk`` PR-mode, ``blast-radius.ts``): impacted
services carry an impact ``score`` and a ``distance``.
"""

from __future__ import annotations

from typing import Any

from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _is_workspace_mode
from repowise.server.mcp_server._meta import build_meta as _build_meta

#: How many impacted services the MCP response carries inline. The full set is
#: available via the REST endpoint / the map; here we keep the agent payload
#: tight and report the true count in ``total_impacted``.
_MCP_IMPACTED_LIMIT = 25


@mcp.tool(requires_workspace=True)
async def get_blast_radius(
    targets: list[str],
    max_depth: int = 3,
    include_behavioral: bool = True,
) -> dict[str, Any]:
    """Cross-repo blast radius — what downstream services break if you change this.

    Workspace-only. Traverses the system graph from the given service(s) and
    returns the impacted services across every repo, ranked by impact score.
    Structural edges (http / grpc / event / package) outweigh behavioral
    co-change. Call before changing a high-fan-out provider to see who consumes
    it across repo boundaries.

    Args:
        targets: node ids ("repo" or "repo::service/path") or repo aliases.
        max_depth: reachability depth (1-8, default 3).
        include_behavioral: include co-change (behavioral) edges (default true).
    """
    if not _is_workspace_mode():
        return {
            "error": "get_blast_radius is only available in workspace mode.",
            "_meta": _build_meta(),
        }

    enricher = _state._cross_repo_enricher
    raw = enricher.get_system_graph() if enricher is not None else None
    if not raw:
        return {
            "error": (
                "No system graph is available yet. Run `repowise update --workspace` "
                "to build cross-repo relationships."
            ),
            "_meta": _build_meta(),
        }

    from repowise.core.workspace.blast_radius import cross_repo_blast_radius
    from repowise.core.workspace.system_graph import SystemGraph

    graph = SystemGraph.from_dict(raw)
    result = cross_repo_blast_radius(
        graph,
        targets,
        max_depth=max(1, min(max_depth, 8)),
        include_behavioral=include_behavioral,
    )

    impacted = [n.to_dict() for n in result.impacted[:_MCP_IMPACTED_LIMIT]]

    summary = (
        f"Changing {len(result.targets)} service(s) impacts {result.total_impacted} "
        f"downstream service(s) across {len(result.impacted_repos)} other repo(s): "
        f"{result.structural_count} via a real dependency, "
        f"{result.behavioral_count} via co-change only."
    )
    if not result.targets:
        summary = (
            f"None of the requested targets matched a service in the graph: "
            f"{result.unresolved_targets}."
        )

    return {
        "targets": result.targets,
        "target_repos": result.target_repos,
        "impacted": impacted,
        "impacted_truncated": max(0, len(result.impacted) - len(impacted)),
        "impacted_repos": result.impacted_repos,
        "structural_count": result.structural_count,
        "behavioral_count": result.behavioral_count,
        "max_distance": result.max_distance,
        "total_impacted": result.total_impacted,
        "unresolved_targets": result.unresolved_targets,
        "summary": summary,
        "_meta": _build_meta(),
    }
