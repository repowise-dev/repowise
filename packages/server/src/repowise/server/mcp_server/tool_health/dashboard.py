"""get_health dashboard mode: the repository-wide view when no target is named."""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.health.aggregation import module_rollups as _module_rollups
from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
from repowise.core.analysis.health.grading import TARGET_SCORE
from repowise.core.analysis.health.grading import distribution as health_distribution
from repowise.server.mcp_server.tool_health.loading import HealthData
from repowise.server.mcp_server.tool_health.paging import Pager
from repowise.server.mcp_server.tool_health.request import HealthRequest
from repowise.server.mcp_server.tool_health.serialize import _serialize_finding, _serialize_metric
from repowise.server.mcp_server.tool_health.summary import (
    _compute_kpis,
    _directive,
    _gap_analysis,
)
from repowise.server.mcp_server.tool_health.targeted import ModeTotals


def build_dashboard(
    data: HealthData, req: HealthRequest, pager: Pager
) -> tuple[dict[str, Any], ModeTotals]:
    """Top-N worst files + headline findings + the per-module rollup.

    The rollup rides along so the overview page needs no second round-trip.
    ``by_leverage`` is built in ``loading``, before the leads.
    """
    pop = data.pop
    all_metrics = pop.all_metrics
    findings = data.findings
    all_modules = _module_rollups(all_metrics, data.deductions)
    gap = _gap_analysis(all_metrics)
    # KPIs keep test files unless ``scope`` narrows them. Tests score better
    # than production code, so narrowing lowers the number with no new defect
    # found: a caller should ask for it knowingly.
    kpis = _compute_kpis(
        all_metrics,
        hotspot_paths=data.hotspot_paths,
        performance_findings=data.perf_findings_count,
        coverage=data.perf_coverage,
    )
    result: dict[str, Any] = {
        # Lead with the recommendation; every block below ranks and describes.
        "directive": _directive(
            data.by_leverage,
            data.leads,
            gap.get("weighted_gross_gap_points") or 0,
            data.plan_biomarkers_by_path,
            data.plan_count_by_path,
        ),
        # Additive leads for the refactoring and performance pillars, which
        # the deficit-ranked directive above cannot express.
        **(
            {"refactoring_directive": data.refactoring.directive}
            if data.refactoring.directive is not None
            else {}
        ),
        **(
            {"performance_directive": data.performance.directive}
            if data.performance.directive is not None
            else {}
        ),
        "mode": "dashboard",
        "scope": pop.reported_scope,
        "counts": pop.reported_counts,
        "unscored_files": pop.unscored_files,
        "kpis": kpis,
        "distribution": health_distribution(all_metrics),
        # Where the gap to the target concentrates: a short list of files.
        "gap_analysis": gap,
        "worst_files": pager.bound([_metric_row(data, m) for m in data.metric_rows], "worst_files"),
        # Ranked file lists keep test files in place and mark them; dropping
        # them would change which files are "worst". Only finding lists split.
        "worst_files_total": len(data.metric_rows),
        "high_leverage_files": pager.bound(_high_leverage_rows(data, gap), "high_leverage_files"),
        "high_leverage_files_total": len(data.by_leverage),
        "top_findings": pager.bound(
            [_serialize_finding(f, data.reference_repository) for f in findings.finding_rows],
            "top_findings",
        ),
        "top_findings_total": findings.findings_total,
        # The test half of the same ranked set, kept out of the production list.
        "test_findings": pager.bound(
            [_serialize_finding(f, data.reference_repository) for f in findings.test_finding_rows],
            "test_findings",
        ),
        "test_findings_total": findings.test_findings_total,
        # Worst-first, so the cap keeps the modules worth looking at.
        "modules": pager.bound(all_modules, "modules"),
        "modules_total": len(all_modules),
    }
    if not req.only:
        _defer_to_secondary_rankings(result, req, data, len(all_modules))
    if "churn_complexity" in req.include_set:
        result["churn_complexity"] = pager.bound(data.churn_points, "churn_complexity")
    if "accuracy" in req.include_set:
        # Does the score rank the buggy files first? Scored over the full open
        # set, not the capped head, which would be circular.
        result["defect_accuracy"] = compute_defect_accuracy(
            all_metrics,
            [_serialize_finding(f, data.reference_repository) for f in data.accuracy_rows],
            # Same map ``all_metrics`` was ranked with, so it scores these ``worst_files``.
            deductions=data.deductions,
        )
    return result, ModeTotals(metrics=None, trends=None, modules=len(all_modules))


def _metric_row(data: HealthData, m: Any) -> dict[str, Any]:
    return _serialize_metric(m, data.leads.get(m.file_path), is_test=m.file_path in data.test_paths)


def _high_leverage_rows(data: HealthData, gap: dict[str, Any]) -> list[dict[str, Any]]:
    """The leverage ranking, each row with its share of the repository's gap.

    The one place ``weighted_deficit`` gets a denominator, in the unit
    ``directive`` speaks. The denominator is the gross deficit of below-target
    files, so shares stay within 100%; the net gap would let above-target files
    shrink it and push one large file past 100% (issue #1437).
    """
    gross = gap.get("weighted_gross_gap_points")
    return [
        {
            **_metric_row(data, m),
            "share_of_repo_gap_pct": (
                round(
                    100.0
                    * max(TARGET_SCORE - m.score, 0.0)
                    * max(m.nloc, 1)
                    / gap["weighted_gross_gap_points"],
                    1,
                )
                if gross
                else None
            ),
        }
        for m in data.by_leverage
    ]


def _defer_to_secondary_rankings(
    result: dict[str, Any], req: HealthRequest, data: HealthData, modules_total: int
) -> None:
    """An unprojected dashboard leads with the summary; the ranked lists become calls."""
    repo = req.repo
    totals = {
        "worst_files": len(data.metric_rows),
        "top_findings": data.findings.findings_total,
        "test_findings": data.findings.test_findings_total,
        "modules": modules_total,
    }
    result["secondary_rankings"] = {
        block: {
            "total": total,
            "call": f"get_health(repo={repo!r}, only=['{block}'], limit=50)",
        }
        for block, total in totals.items()
    }
    for block in totals:
        result.pop(block, None)
        result.pop(f"{block}_total", None)
