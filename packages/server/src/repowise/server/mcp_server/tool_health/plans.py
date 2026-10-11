"""Refactoring-plan projection for get_health: the queue's plans and plan status."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from repowise.server.mcp_server._budget import register_post_enforce
from repowise.server.mcp_server.tool_health.paging import Pager
from repowise.server.mcp_server.tool_health.pillars import _merge_ignored
from repowise.server.mcp_server.tool_health.request import HealthRequest

if TYPE_CHECKING:
    from repowise.server.mcp_server.tool_health.loading import HealthData


def _render_plans(
    result: dict[str, Any], data: HealthData, req: HealthRequest, pager: Pager
) -> None:
    """The queue's plans in queue order, one compact row each, and why the list may be empty.

    ``refactoring_scope`` and the refactoring filters apply as on the queue;
    ``get_health(plan_id=...)`` returns one plan in full (detector detail,
    evidence, validation).
    """
    page = data.refactoring_plans
    rows = [_plan_row(item) for item in page.items] if page is not None else []
    total = page.total if page is not None else 0
    result["refactoring_plans"] = rows
    result["refactoring_plans_total"] = total
    if page is not None:
        result["refactoring_plans_scope"] = page.scope
        # The opportunities the plans are steps of: the queue's own count.
        result["refactoring_plans_opportunities_total"] = page.opportunities_total
        if page.hidden is not None:
            result["refactoring_plans_hidden"] = page.hidden
        result["refactoring_plans_counts"] = page.counts
        pager.note_page(
            "refactoring_plans",
            start=req.cursor,
            shown=page.next_offset - req.cursor,
            total=total,
            limit=req.plans_cap,
        )
    _merge_ignored(result, data.refactoring_plans_ignored)
    if req.wants("refactoring_plans"):
        scoped = data.pop.scoped
        result["refactoring_plans_status"] = _refactoring_plans_status(
            available_plans_total=total,
            plans_emitted=len(rows),
            scoped=scoped,
            has_eligible_metrics=bool(data.metric_rows if scoped else data.pop.all_metrics),
            finding=next(iter(data.findings.emitted), None),
            repo=req.repo,
        )
    # Prose suggestions for biomarkers without a structured plan ship once per
    # type as ``suggestion_legend``, not per finding: the text is keyed by type.


_PLAN_ROW_FIELDS = (
    "refactoring_type",
    "file_path",
    "target_symbol",
    "line_start",
    "line_end",
    "effort_bucket",
    "confidence",
    "impact_delta",
    "source_biomarker",
    "opportunity_id",
)


def _plan_row(step: dict[str, Any]) -> dict[str, Any]:
    """One stored step as a list row; the full plan is one ``plan_id`` call away."""
    row: dict[str, Any] = {"id": step.get("plan_id")}
    row.update((name, step.get(name)) for name in _PLAN_ROW_FIELDS)
    row["classification"] = (step.get("applicability") or {}).get("classification")
    if step.get("relocated_by"):
        row["relocated_by"] = step["relocated_by"]
    return row


def _finding_next_action(finding: Any, repo: str | None) -> dict[str, Any]:
    """Return one concrete source call for a finding without a stored plan."""
    path = str(getattr(finding, "file_path", ""))
    line_start = getattr(finding, "line_start", None)
    line_end = getattr(finding, "line_end", None)
    if path and line_start and line_end:
        arguments: dict[str, Any] = {"symbol_id": f"{path}:{line_start}-{line_end}"}
        if repo:
            arguments["repo"] = repo
        return {"tool": "get_symbol", "arguments": arguments}
    arguments = {"targets": [path], "include": ["skeleton"]}
    if repo:
        arguments["repo"] = repo
    return {"tool": "get_context", "arguments": arguments}


def _refactoring_plans_status(
    *,
    available_plans_total: int,
    plans_emitted: int,
    scoped: bool,
    has_eligible_metrics: bool,
    finding: Any | None,
    repo: str | None,
) -> dict[str, Any]:
    """Explain an explicitly requested plan projection deterministically."""
    if plans_emitted:
        return {"state": "available", "reason": None}
    if available_plans_total:
        return {
            "state": "available_not_emitted",
            "reason": "request_window_empty",
            "message": "Plans exist but the requested limit/cursor window emitted none.",
        }
    if scoped and not has_eligible_metrics:
        return {
            "state": "unavailable",
            "reason": "no_eligible_targets",
            "message": "No requested target resolved to an eligible stored health row.",
        }
    if not has_eligible_metrics:
        return {
            "state": "unavailable",
            "reason": "analysis_unavailable",
            "message": "No stored health analysis is available for this population.",
            "next_action": {
                "command": "repowise update",
                "reason": "run health analysis before interpreting scores or plans",
            },
        }
    if finding is None:
        return {
            "state": "empty",
            "reason": "no_applicable_findings",
            "message": "The eligible population has no applicable open findings.",
        }
    return {
        "state": "indeterminate",
        "reason": "plan_analysis_indeterminate",
        "message": "Findings exist, but stored plan evidence is absent.",
        "possible_causes": [
            "no_supported_structured_transformation",
            "refactoring_detector_disabled_or_failed",
        ],
        "next_action": _finding_next_action(finding, repo),
    }


def _reconcile_plan_status(result: dict[str, Any]) -> None:
    """Keep plan availability honest after the final budget mutates collections."""
    status = result.get("refactoring_plans_status")
    plans = result.get("refactoring_plans")
    if not isinstance(status, dict) or status.get("state") != "available":
        return
    if plans is not None and (not isinstance(plans, list) or plans):
        return
    if not result.get("refactoring_plans_total", 0):
        return
    status.update(
        state="available_not_emitted",
        reason="response_budget",
        message="Plans exist but were removed by the final response budget.",
    )


register_post_enforce("get_health", _reconcile_plan_status)
