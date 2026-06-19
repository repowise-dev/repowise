"""MCP Tool: get_conformance — architecture rule violations + cycles (workspace).

Answers "does the live architecture obey the dependency rules we declared, and
are there any circular service dependencies?" by reading the conformance report
built during ``repowise update --workspace``. Read-only; the full report is also
on ``GET /api/workspace/conformance``.
"""

from __future__ import annotations

from typing import Any

from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _is_workspace_mode
from repowise.server.mcp_server._meta import build_meta as _build_meta

#: Inline caps so the agent payload stays tight; true counts are reported and the
#: full set is on the REST endpoint.
_MCP_VIOLATION_LIMIT = 25
_MCP_CYCLE_LIMIT = 25


@mcp.tool(requires_workspace=True)
async def get_conformance(repo: str | None = None) -> dict[str, Any]:
    """Architecture conformance — dependency-rule violations + cycles.

    Workspace-only. Reports cross-repo dependencies that violate the team's
    declared allow/deny rules (e.g. ``frontend !-> db``) and any circular service
    dependencies. Call before a refactor that changes service boundaries, or to
    audit whether the live architecture still matches the intended one.

    Args:
        repo: optional repo alias — limit findings to those involving this repo.
    """
    if not _is_workspace_mode():
        return {
            "error": "get_conformance is only available in workspace mode.",
            "_meta": _build_meta(),
        }

    enricher = _state._cross_repo_enricher
    report = enricher.get_conformance() if enricher is not None else None
    if not report:
        return {
            "error": (
                "No conformance report is available yet. Run `repowise update "
                "--workspace` to build cross-repo relationships, and declare rules "
                "under `conformance:` in `.repowise-workspace.yaml`."
            ),
            "_meta": _build_meta(),
        }

    if repo:
        scoped = enricher.get_conformance_for_repo(repo)
        violations = scoped["violations"]
        cycles = scoped["cycles"]
    else:
        violations = report.get("violations", [])
        cycles = report.get("cycles", [])

    shown_violations = violations[:_MCP_VIOLATION_LIMIT]
    shown_cycles = cycles[:_MCP_CYCLE_LIMIT]

    if violations or cycles:
        summary = (
            f"{len(violations)} architecture rule violation(s) and {len(cycles)} "
            f"dependency cycle(s) from {report.get('rules_evaluated', 0)} declared rule(s)."
        )
    elif report.get("rules_evaluated", 0):
        summary = "No conformance violations or dependency cycles detected."
    else:
        summary = (
            "No conformance rules are declared; reporting dependency cycles only (none found)."
        )

    return {
        "rules_evaluated": report.get("rules_evaluated", 0),
        "violations": shown_violations,
        "violations_truncated": max(0, len(violations) - len(shown_violations)),
        "cycles": shown_cycles,
        "cycles_truncated": max(0, len(cycles) - len(shown_cycles)),
        "violation_count": len(violations),
        "cycle_count": len(cycles),
        "summary": summary,
        "_meta": _build_meta(),
    }
