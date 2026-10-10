"""get_health dashboard mode: the repository-wide view when no target is named."""

from __future__ import annotations

import re
from typing import Any

from repowise.core.analysis.health.aggregation import module_rollups as _module_rollups
from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
from repowise.core.analysis.health.grading import TARGET_SCORE
from repowise.core.analysis.health.grading import distribution as health_distribution
from repowise.server.mcp_server._budget import register_post_enforce
from repowise.server.mcp_server.tool_health.loading import HealthData
from repowise.server.mcp_server.tool_health.paging import Pager
from repowise.server.mcp_server.tool_health.request import HealthRequest
from repowise.server.mcp_server.tool_health.serialize import _serialize_finding, _serialize_metric
from repowise.server.mcp_server.tool_health.summary import _compute_kpis, _gap_analysis
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
        unanalysed=len(pop.unanalysed_paths),
    )
    result: dict[str, Any] = {
        # The one lead; every block below ranks and describes.
        **({"fix_first": _fix_first_block(data, req, pager)} if data.fix_first is not None else {}),
        "mode": "dashboard",
        "scope": pop.reported_scope,
        "counts": pop.reported_counts,
        "unscored_files": pop.unscored_files,
        # Files in a language health has no dialect for: no score, so in no
        # figure here. Only said when there are some.
        **({"unanalysed_file_count": len(pop.unanalysed_paths)} if pop.unanalysed_paths else {}),
        "kpis": kpis,
        "distribution": health_distribution(all_metrics),
        # Where the gap to the target concentrates: a short list of files.
        "gap_analysis": gap,
        # Production files only: a test is never the worst file to work on.
        # Test files rank in their own list, as findings do.
        "worst_files": pager.bound([_metric_row(data, m) for m in data.metric_rows], "worst_files"),
        "worst_files_total": len(data.metric_rows),
        "test_worst_files": pager.bound(
            [_metric_row(data, m) for m in data.test_metric_rows], "test_worst_files"
        ),
        "test_worst_files_total": len(data.test_metric_rows),
        "high_leverage_files": pager.bound(_high_leverage_rows(data, gap), "high_leverage_files"),
        "high_leverage_files_total": len(data.by_leverage),
        "top_findings": pager.bound(
            [
                _serialize_finding(
                    f, data.reference_repository, findings.lower_priority.get(f.id)
                )
                for f in findings.finding_rows
            ],
            "top_findings",
        ),
        "top_findings_total": findings.findings_total,
        # The test half of the same ranked set, kept out of the production list.
        "test_findings": pager.bound(
            [
                _serialize_finding(
                    f, data.reference_repository, findings.lower_priority.get(f.id)
                )
                for f in findings.test_finding_rows
            ],
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


def _fix_first_block(data: HealthData, req: HealthRequest, pager: Pager) -> dict[str, Any]:
    """Core's queue in the compact projection, its counts, and the call for one full item.

    ``lead`` is the queue's first item, whatever page ``items`` is;
    ``items_total`` is the eligible queue the items were cut from, starting at
    ``cursor``. Only the lead carries ``next_call``: it is the bulk of a compact
    item, every item's is one ``fix_id`` call away, and without it a named page
    of 25 fits the default budget.
    """
    page, full = data.fix_first, data.fix_first_full
    block = page.as_dict(compact=True)
    for item in block["items"]:
        item.pop("next_call", None)
    block["lead"] = full.lead.compact() if full.lead is not None else None
    block["counts"] = full.counts(shown=len(page.items))
    eligible = full.totals.eligible
    block["items_total"] = eligible
    cursor = req.cursor if req.pages_fix_first else 0
    block["cursor"] = cursor
    if full.lead is not None:
        block["detail_call"] = f"get_health(fix_id={full.lead.id!r})"
    next_cursor = cursor + len(page.items)
    if req.pages_fix_first and page.items and next_cursor < eligible:
        pager.recoveries["fix_first"] = (next_cursor, len(page.items), eligible - next_cursor)
    return block


_RECOVERY_CURSOR = re.compile(r"cursor=\d+\)$")


def _settle_fix_first_page(result: dict[str, Any]) -> None:
    """Restate a named page after the response budget trimmed its tail, so
    ``counts.shown`` and the next page's cursor count what was delivered."""
    block = result.get("fix_first")
    if not isinstance(block, dict) or "cursor" not in block:
        return
    shown = len(block.get("items") or [])
    if isinstance(block.get("counts"), dict):
        block["counts"]["shown"] = shown
    recovery = (result.get("recovery") or {}).get("fix_first")
    if isinstance(recovery, dict):
        next_cursor = block["cursor"] + shown
        recovery["remaining"] = int(block.get("items_total") or 0) - next_cursor
        recovery["call"] = _RECOVERY_CURSOR.sub(f"cursor={next_cursor})", recovery["call"])


register_post_enforce("get_health", _settle_fix_first_page)


def _metric_row(data: HealthData, m: Any) -> dict[str, Any]:
    return _serialize_metric(m, data.leads.get(m.file_path), is_test=m.file_path in data.test_paths)


def _high_leverage_rows(data: HealthData, gap: dict[str, Any]) -> list[dict[str, Any]]:
    """The leverage ranking, each row with its share of the repository's gap.

    The one place ``weighted_deficit`` gets a denominator. The denominator is the gross deficit of below-target
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
        "test_worst_files": len(data.test_metric_rows),
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
