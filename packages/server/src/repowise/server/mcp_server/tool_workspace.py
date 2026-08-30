"""MCP Tool: get_workspace_test_impact — cross-repo test impact (workspace only).

Answers "which tests in consumer repos should I run when this provider changes?"
by joining the workspace contract map with per-repo test reachability.
"""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.workspace_test_impact import (
    DEFAULT_CALL_DEPTH,
    DEFAULT_MAX_DEPTH,
    analyze_workspace_test_impact,
    workspace_test_impact_to_dict,
)
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _is_workspace_mode
from repowise.server.mcp_server._meta import build_meta as _build_meta

#: How many recommendations the MCP response carries inline.
_MCP_RECOMMENDATIONS_LIMIT = 30


@mcp.tool(
    default=False,
    requires_workspace=True,
    surface_order=220,
    trust_kind="structural",
    artifact_type="test_impact",
    presentation="list",
)
async def get_workspace_test_impact(
    changed_files: list[dict[str, str]],
    call_depth: int = DEFAULT_CALL_DEPTH,
    import_depth: int = DEFAULT_MAX_DEPTH,
    include_measured: bool = True,
    include_inferred: bool = True,
    min_confidence: float = 0.0,
    target_repos: list[str] | None = None,
) -> dict[str, Any]:
    """Cross-repository test impact analysis — which downstream tests to run.

    Workspace-only. Given a list of changed files in provider repositories,
    returns the test files in consumer repositories that cover those changes,
    via:

    - **Measured** per-test coverage (if ingested via ``repowise coverage add``)
    - **Inferred** call-graph reachability (3-hop forward walk from test symbols)
    - **Inferred** import-graph fallback (1-hop from test file imports)

    The response is tiered: measured coverage wins where present, then
    call-graph, then import-graph. Each recommendation carries the contract
    link it originated from and its confidence.

    Args:
        changed_files: List of {"repo": provider_alias, "path": file_path}
            specifying the provider-side changes. Example:
            [{"repo": "backend-api", "path": "src/api/users.py"}]
        call_depth: Call graph walk depth (1-8, default 3).
        import_depth: Import graph fallback depth (1-3, default 1).
        include_measured: Include coverage-backed recommendations (default true).
        include_inferred: Include graph-inferred recommendations (default true).
        min_confidence: Minimum contract link confidence to consider (0.0-1.0).
        target_repos: Optional list of consumer repo aliases to limit analysis to.

    Returns:
        Recommendations grouped by consumer repo, with basis and tier labels.
    """
    if not _is_workspace_mode():
        return {
            "error": "get_workspace_test_impact is only available in workspace mode.",
            "_meta": _build_meta(),
        }

    enricher = _state._cross_repo_enricher
    if enricher is None:
        return {
            "error": "No workspace enricher available.",
            "_meta": _build_meta(),
        }

    raw = enricher.get_system_graph() if enricher is not None else None
    if not raw:
        return {
            "error": (
                "No system graph is available yet. Run `repowise update --workspace` "
                "to build cross-repo relationships."
            ),
            "_meta": _build_meta(),
        }

    if not changed_files:
        return {
            "error": "changed_files is required: provide [{\"repo\": \"alias\", \"path\": \"file.py\"}, ...]",
            "_meta": _build_meta(),
        }

    ws_root = _state._workspace_root
    if not ws_root:
        return {
            "error": "Workspace root not configured.",
            "_meta": _build_meta(),
        }

    result = await analyze_workspace_test_impact(
        ws_root,
        changed_files,
        call_depth=max(1, min(call_depth, 8)),
        import_depth=max(1, min(import_depth, 3)),
        include_measured=include_measured,
        include_inferred=include_inferred,
        min_confidence=min_confidence,
        target_repos=target_repos,
    )

    payload = workspace_test_impact_to_dict(result)

    # Cap recommendations for MCP response
    total = payload["recommendations_total"]
    recommendations = payload["recommendations"][:_MCP_RECOMMENDATIONS_LIMIT]
    truncated = max(0, total - len(recommendations))

    # Build summary
    by_basis = payload["recommendations_by_basis"]
    by_consumer = payload["recommendations_by_consumer_repo"]
    by_provider = payload["recommendations_by_repo"]

    summary_parts = []
    if total > 0:
        summary_parts.append(
            f"{total} test recommendation(s) across {len(by_consumer)} consumer repo(s)"
        )
        if by_basis.get("measured", 0):
            summary_parts.append(f"{by_basis['measured']} from measured coverage")
        if by_basis.get("inferred", 0):
            summary_parts.append(f"{by_basis['inferred']} inferred from call/import graph")
    else:
        summary_parts.append("No test recommendations found for the given changes.")

    summary = ". ".join(summary_parts) + "."

    return {
        "workspace": True,
        "recommendations": recommendations,
        "recommendations_total": total,
        "recommendations_truncated": truncated,
        "recommendations_by_basis": by_basis,
        "recommendations_by_consumer_repo": by_consumer,
        "recommendations_by_provider_repo": by_provider,
        "files_analyzed": payload["files_analyzed"],
        "summary": summary,
        "_meta": _build_meta(
            extra={
                "index_age_days": None,
                "indexed_commit": None,
            }
        ),
    }