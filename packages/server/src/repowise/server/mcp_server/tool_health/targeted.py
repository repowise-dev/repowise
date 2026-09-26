"""get_health targeted mode: the files and modules the caller named."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from repowise.core.analysis.health.aggregation import module_rollups as _module_rollups
from repowise.core.analysis.health.trends import file_trend
from repowise.server.mcp_server.tool_health.loading import HealthData
from repowise.server.mcp_server.tool_health.paging import Pager
from repowise.server.mcp_server.tool_health.request import HealthRequest
from repowise.server.mcp_server.tool_health.serialize import _serialize_finding, _serialize_metric
from repowise.server.mcp_server.tool_health.targets import _unresolved_targets


@dataclass(frozen=True)
class ModeTotals:
    """Totals only the mode builder knows; ``None`` means the mode has no such list."""

    metrics: int | None
    trends: int | None
    modules: int


def build_targeted(
    data: HealthData, req: HealthRequest, pager: Pager, repo_root: Any
) -> tuple[dict[str, Any], ModeTotals]:
    """Metrics, findings and trends for exactly the named targets."""
    pop = data.pop
    findings_total = data.findings.findings_total
    metric_payload = _metric_payload(data)
    module_set = set(req.module_targets)
    module_rollup = _module_rollups(
        [m for m in pop.all_metrics if m.module in module_set], data.deductions
    )
    result: dict[str, Any] = {
        "mode": "targets",
        "targets": req.raw_targets,
        # ``metrics_total`` makes a trim visible: a ``module:`` target expands
        # to a file count the caller cannot infer from what they passed.
        **({"modules": module_rollup} if req.module_targets else {}),
        "metrics": pager.bound(metric_payload, "metrics"),
        "metrics_total": len(metric_payload),
        "findings": pager.bound(
            [_serialize_finding(f, data.reference_repository) for f in data.findings.finding_rows],
            "findings",
        ),
        "findings_total": findings_total,
        # Which reading the scores are on; last, so identity keys lead.
        "scope": pop.reported_scope,
        "counts": pop.reported_counts,
    }
    unresolved = _unresolved_targets(
        file_targets=pop.file_targets,
        module_targets=req.module_targets,
        matched_modules=pop.matched_modules,
        resolved_paths={m.file_path for m in data.metric_rows},
        excluded_paths=pop.excluded_paths,
        unscored_paths=pop.unscored_paths,
        repo_root=repo_root,
    )
    if unresolved:
        result["unresolved"] = unresolved
        if any(u["reason"] == "no_such_module" for u in unresolved):
            # ``module:`` has no discovery call, so list the valid names.
            result["known_modules"] = sorted({m.module for m in pop.all_metrics if m.module})
    trends = _file_trends(data)
    if trends:
        result["trends"] = pager.bound(trends, "trends")
    if req.module_targets and not req.only and not req.include_set:
        _defer_to_secondary_rankings(result, req, findings_total, len(trends))
    return result, ModeTotals(
        metrics=len(metric_payload), trends=len(trends), modules=len(module_rollup)
    )


def _metric_payload(data: HealthData) -> list[dict[str, Any]]:
    rows = []
    for m in data.metric_rows:
        row = _serialize_metric(
            m, data.leads.get(m.file_path), is_test=m.file_path in data.test_paths
        )
        if m.file_path in data.signals_by_path:
            row["signals"] = data.signals_by_path[m.file_path]
        rows.append(row)
    return rows


def _file_trends(data: HealthData) -> list[dict[str, Any]]:
    """Per-file score trajectory for each target, omitted when a file has
    fewer than 2 snapshots rather than drawn as a flat line."""
    trends = []
    for m in data.metric_rows:
        t = file_trend(data.snapshots, m.file_path)
        if not t.points:
            continue
        series = [round(p.score, 2) for p in t.points]
        entry: dict[str, Any] = {
            "file_path": t.file_path,
            "series": series,
            "current": t.current,
            "delta": t.delta,
            "declining": t.declining,
        }
        # The score floors at 1.0, flattening progress on the worst files.
        # Carry the unclamped series only where it differs.
        unclamped = [round(p.unclamped_score, 2) for p in t.points]
        if unclamped != series:
            entry["unclamped_series"] = unclamped
            entry["unclamped_delta"] = t.unclamped_delta
        trends.append(entry)
    return trends


def _defer_to_secondary_rankings(
    result: dict[str, Any], req: HealthRequest, findings_total: int, trends_total: int
) -> None:
    """A bare ``module:`` call leads with the rollup; the ranked lists become calls."""
    targets, repo, view = req.raw_targets, req.repo, req.refactoring_view
    result["secondary_rankings"] = {
        "findings": {
            "total": findings_total,
            "call": (
                f"get_health(targets={targets!r}, only=['findings'], "
                f"repo={repo!r}, limit=50, refactoring_view='{view}')"
            ),
        },
        "trends": {
            "total": trends_total,
            "call": (
                f"get_health(targets={targets!r}, only=['trends'], "
                f"repo={repo!r}, limit=50, refactoring_view='{view}')"
            ),
        },
    }
    for block in ("findings", "trends"):
        result.pop(block, None)
        result.pop(f"{block}_total", None)
