"""get_health dashboard mode: the repository-wide view when no target is named."""

from __future__ import annotations

from functools import cache
from typing import Any

from repowise.core.analysis.health.aggregation import module_rollups as _module_rollups
from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
from repowise.core.analysis.health.grading import TARGET_SCORE
from repowise.core.analysis.health.grading import distribution as health_distribution
from repowise.server.mcp_server.tool_health.loading import HealthData
from repowise.server.mcp_server.tool_health.paging import Pager
from repowise.server.mcp_server.tool_health.request import FIX_FIRST_CAP, HealthRequest
from repowise.server.mcp_server.tool_health.serialize import _serialize_finding, _serialize_metric
from repowise.server.mcp_server.tool_health.summary import _compute_kpis, _gap_analysis
from repowise.server.mcp_server.tool_health.targeted import ModeTotals

_RANKED_FILE_LISTS = frozenset({"worst_files", "high_leverage_files", "test_worst_files"})


def build_dashboard(
    data: HealthData, req: HealthRequest, pager: Pager
) -> tuple[dict[str, Any], ModeTotals]:
    """Top-N worst files + headline findings + the per-module rollup.

    The rollup rides along so the overview page needs no second round-trip.
    ``by_leverage`` is built in ``loading``, before the leads.
    """
    # A block is a thunk, built only when the projection keeps its key: a list
    # call would otherwise pay for the whole dashboard and then drop it.
    result: dict[str, Any] = {
        key: value() if callable(value) else value
        for key, value in _dashboard_blocks(data, req, pager).items()
        if not callable(value) or _kept(req, key)
    }
    modules_total = result.get("modules_total", 0)
    if not req.only:
        _defer_to_secondary_rankings(result, req, data, modules_total)
    if "churn_complexity" in req.include_set:
        result["churn_complexity"] = pager.bound(data.churn_points, "churn_complexity")
    if "accuracy" in req.include_set:
        # Does the score rank the buggy files first? Scored over the full open
        # set, not the capped head, which would be circular.
        result["defect_accuracy"] = compute_defect_accuracy(
            data.pop.all_metrics,
            [_serialize_finding(f, data.reference_repository) for f in data.accuracy_rows],
            # Same map ``all_metrics`` was ranked with, so it scores these ``worst_files``.
            deductions=data.deductions,
        )
    return result, ModeTotals(metrics=None, trends=None, modules=modules_total)


def _kept(req: HealthRequest, key: str) -> bool:
    """Whether the projection keeps *key*; a ``*_total`` rides with its list."""
    return req.wants(key) or (key.endswith("_total") and req.wants(key.removesuffix("_total")))


def _dashboard_blocks(data: HealthData, req: HealthRequest, pager: Pager) -> dict[str, Any]:
    """Every dashboard key in payload order: a value, or a thunk that builds it.

    A thunk two blocks share is memoised, so it is built once or not at all.
    """
    pop = data.pop
    all_metrics = pop.all_metrics
    findings = data.findings
    modules = cache(lambda: _module_rollups(all_metrics, data.deductions))
    gap = cache(lambda: _gap_analysis(all_metrics))
    return {
        # The one lead; every block below ranks and describes.
        **({"fix_first": _fix_first_block(data, req, pager)} if data.fix_first is not None else {}),
        "mode": "dashboard",
        "scope": pop.reported_scope,
        "counts": pop.reported_counts,
        "unscored_files": pop.unscored_files,
        # Files in a language health has no dialect for: no score, so in no
        # figure here. Only said when there are some.
        **({"unanalysed_file_count": len(pop.unanalysed_paths)} if pop.unanalysed_paths else {}),
        # KPIs keep test files unless ``scope`` narrows them. Tests score better
        # than production code, so narrowing lowers the number with no new defect
        # found: a caller should ask for it knowingly.
        "kpis": lambda: _name_listed_worst(
            _compute_kpis(
                all_metrics,
                hotspot_paths=data.hotspot_paths,
                performance_findings=data.perf_findings_count,
                coverage=data.perf_coverage,
                unanalysed=len(pop.unanalysed_paths),
            ),
            data,
        ),
        "distribution": lambda: health_distribution(all_metrics),
        # Where the gap to the target concentrates: a short list of files.
        "gap_analysis": gap,
        # Production files only: a test is never the worst file to work on.
        # Test files rank in their own list, as findings do.
        "worst_files": lambda: pager.bound(
            [_metric_row(data, m) for m in data.metric_rows], "worst_files"
        ),
        "worst_files_total": len(data.metric_rows),
        "test_worst_files": lambda: pager.bound(
            [_metric_row(data, m) for m in data.test_metric_rows], "test_worst_files"
        ),
        "test_worst_files_total": len(data.test_metric_rows),
        # Scripts, generated and vendored files: in the KPIs, in neither list.
        # Said when a list is asked for by name, so the default stays its size.
        **(
            {"worst_files_hidden": data.worst_files_hidden}
            if data.worst_files_hidden and req.only_set & _RANKED_FILE_LISTS
            else {}
        ),
        "high_leverage_files": lambda: pager.bound(
            _high_leverage_rows(data, gap()), "high_leverage_files"
        ),
        "high_leverage_files_total": len(data.by_leverage),
        "top_findings": lambda: pager.bound(_finding_rows(data, findings.finding_rows), "top_findings"),
        "top_findings_total": findings.findings_total,
        **({"top_findings_counts": data.finding_counts} if data.finding_counts else {}),
        # The test half of the same ranked set, kept out of the production list.
        "test_findings": lambda: pager.bound(_finding_rows(data, findings.test_finding_rows), "test_findings"),
        "test_findings_total": findings.test_findings_total,
        # Worst-first, so the cap keeps the modules worth looking at.
        "modules": lambda: pager.bound(modules(), "modules"),
        "modules_total": lambda: len(modules()),
    }


def _fix_first_block(data: HealthData, req: HealthRequest, pager: Pager) -> dict[str, Any]:
    """Core's queue in the compact projection, its counts, and the call for one full item.

    ``lead`` is the queue's first item, whatever page ``items`` is;
    ``items_total`` is the eligible queue the items were cut from, starting at
    ``cursor``. Only the lead carries ``next_call``: it is the bulk of a compact
    item, every item's is one ``fix_id`` call away, and without it a named page
    of 25 fits the default budget. A due lead carries its ``first_step`` and
    ``verify`` on ``lead``; other due items carry theirs only on a named
    ``fix_first`` page, within its first :data:`FIX_FIRST_CAP`, so the
    dashboard stays near its old size.
    """
    page, full = data.fix_first, data.fix_first_full
    block = page.as_dict(compact=True)
    block["lead"] = full.lead.compact() if full.lead is not None else None
    lead_id = block["lead"]["id"] if block["lead"] else None
    for at, item in enumerate(block["items"]):
        item.pop("next_call", None)
        if not req.pages_fix_first or item["id"] == lead_id or at >= FIX_FIRST_CAP:
            item.pop("first_step", None)
            item.pop("verify", None)
    # ``counts.excluded`` carries the same reasons without the zeros, and
    # ``_meta.health_analysis`` the analysed commit and time; the model
    # version is for reading a stored queue back, not for the agent.
    del block["totals"]["excluded"], block["basis"], block["model_version"]
    block["counts"] = full.counts(shown=len(page.items)).as_dict()
    block["items_total"] = full.totals.eligible
    block["cursor"] = req.fix_first_cursor
    if full.lead is not None:
        block["detail_call"] = f"get_health(fix_id={full.lead.id!r})"
    if req.pages_fix_first:
        pager.note_page(
            "fix_first",
            start=req.fix_first_cursor,
            shown=len(page.items),
            total=full.totals.eligible,
            limit=req.fix_first_cap,
        )
    return block


def _finding_rows(data: HealthData, rows: list[Any]) -> list[dict[str, Any]]:
    labels = data.findings.lower_priority
    return [_serialize_finding(f, data.reference_repository, labels.get(f.id)) for f in rows]


def _name_listed_worst(kpis: dict[str, Any], data: HealthData) -> dict[str, Any]:
    """The worst file and worst test the KPIs name are the heads of the lists,
    so a script left out of ``worst_files`` is not named worst either."""
    if not kpis.get("file_count"):
        return kpis
    for prefix, rows in (("worst_performer", data.metric_rows), ("worst_test", data.test_metric_rows)):
        head = rows[0] if rows else None
        kpis[f"{prefix}_path"] = head.file_path if head else None
        kpis[f"{prefix}_score"] = round(head.score, 2) if head else None
    return kpis


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
