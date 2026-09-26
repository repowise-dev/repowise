"""The ``include``-gated blocks get_health adds after the mode's own payload.

Applied in a fixed order: the response is a dict, so the order blocks are
added in is the order a caller reads them in.
"""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.doc_drift.constants import DETECTION_BASIS
from repowise.core.analysis.health.suggestions import suggestion_for
from repowise.core.analysis.health.trends import (
    diff_snapshots,
    drop_unscoped_fields,
    recent_kpis,
)
from repowise.core.persistence.crud import serialize_doc_drift_row, summarize_confidence_rows
from repowise.server.mcp_server.tool_health.coverage import _coverage_block
from repowise.server.mcp_server.tool_health.loading import HealthData
from repowise.server.mcp_server.tool_health.paging import Pager
from repowise.server.mcp_server.tool_health.pillars import (
    _recommendation_lede,
    _render_performance,
    _render_refactoring,
)
from repowise.server.mcp_server.tool_health.plans import _render_plans
from repowise.server.mcp_server.tool_health.request import HealthRequest
from repowise.server.mcp_server.tool_health.serialize import _serialize_finding


def add_optional_blocks(
    result: dict[str, Any], data: HealthData, req: HealthRequest, pager: Pager, repo_path: str
) -> None:
    include = req.include_set
    if "biomarkers" in include and "findings" not in result:
        _add_biomarkers(result, data, pager)
    if req.plans_requested:
        _render_plans(result, data, req, pager)
    if "trend" in include:
        result["trend"] = _trend_block(data, req, pager)
    _render_refactoring(result, data.refactoring, req, pager)
    _render_performance(result, data.performance, req, pager)
    if req.wants_lede:
        result["recommendation_lede"] = _recommendation_lede(
            data.performance, data.refactoring_recommendations, data.reference_repository, req
        )
    if "coverage" in include:
        result["coverage"] = _coverage_block(
            data.coverage_rows,
            data.coverage_summary,
            scoped=data.pop.scoped,
            pager=pager,
            repo_path=repo_path,
        )
    if "doc_drift" in include:
        result["doc_drift"] = _doc_drift_block(data, pager)
    # The dimension filter (``include=["performance"]`` etc.) is applied where
    # rows are selected: filtering here would filter a list already capped.
    if "refactoring" in include and req.wants("suggestion_legend"):
        result["suggestion_legend"] = _suggestion_legend(data)


def _add_biomarkers(result: dict[str, Any], data: HealthData, pager: Pager) -> None:
    """Capped like every other ranked list.

    Uncapped, this block could return the repo's entire open finding set, far
    past an agent's context. Findings arrive impact-ordered, so the cap keeps
    the ones worth reading.
    """
    findings, repository = data.findings, data.reference_repository
    result["findings"] = pager.bound(
        [_serialize_finding(f, repository) for f in findings.finding_rows],
        "findings",
    )
    result["findings_total"] = findings.findings_total
    # Dashboard mode only (targeted mode already set ``findings``), so this
    # uses the same production/test split as ``top_findings``.
    result["test_findings"] = pager.bound(
        [_serialize_finding(f, repository) for f in findings.test_finding_rows],
        "test_findings",
    )
    result["test_findings_total"] = findings.test_findings_total


_ALERT_FIELDS = (
    "kind",
    "metric",
    "current",
    "baseline",
    "delta",
    "message",
    "driver",
    "structure_delta",
    "history_delta",
)


def _trend_block(data: HealthData, req: HealthRequest, pager: Pager) -> dict[str, Any]:
    summary = diff_snapshots(data.snapshots)
    narrowed = data.pop.reported_scope == "production"
    recent = recent_kpis(data.snapshots, limit=10)
    if narrowed:
        recent = drop_unscoped_fields(recent)
    alerts = [{name: getattr(a, name) for name in _ALERT_FIELDS} for a in summary.alerts]
    limit = req.limit
    # The hotspot pair describes the whole repository whatever the scope,
    # since only the average was snapshotted for both populations.
    block: dict[str, Any] = {
        "current_hotspot_health": None if narrowed else summary.current_hotspot_health,
        "current_average_health": summary.current_average_health,
        "previous_hotspot_health": None if narrowed else summary.previous_hotspot_health,
        "previous_average_health": summary.previous_average_health,
        "hotspot_delta": None if narrowed else summary.hotspot_delta,
        "average_delta": summary.average_delta,
        "current_structure_deduction": summary.current_structure_deduction,
        "current_history_deduction": summary.current_history_deduction,
        "alerts": pager.bound(alerts, "trend.alerts"),
        "alerts_total": len(alerts),
        "alerts_emitted": min(len(alerts), limit),
        "recent": pager.bound(recent, "trend.recent"),
        "recent_total": len(recent),
        "recent_emitted": min(len(recent), limit),
    }
    if len(alerts) > limit:
        block["alerts_reduced_reason"] = "limit"
    if len(recent) > limit:
        block["recent_reduced_reason"] = "limit"
    return block


def _doc_drift_block(data: HealthData, pager: Pager) -> dict[str, Any]:
    if data.drift_unavailable is not None:
        return {"unavailable": data.drift_unavailable}
    drift_rows = data.drift_rows
    drift_payload = pager.bound(
        # Evidence restates the row plus a resolver trace: not worth this
        # budget. The CLI, which has no budget, keeps it.
        [serialize_doc_drift_row(r, evidence=False) for r in drift_rows],
        "doc_drift.findings",
    )
    block: dict[str, Any] = {
        "findings": drift_payload,
        "findings_total": len(drift_rows),
        "findings_emitted": len(drift_payload),
        "documents": len({r.file_path for r in drift_rows}),
        "confidence": summarize_confidence_rows(drift_rows),
        # Without it, the findings would claim coverage this detector lacks:
        # most references are uncheckable.
        "findings_basis": DETECTION_BASIS,
    }
    if len(drift_payload) < len(drift_rows):
        block["findings_reduced_reason"] = "limit"
    return block


def _suggestion_legend(data: HealthData) -> dict[str, str]:
    """One entry per biomarker type actually present in the findings this
    response carries.

    Built from the ranked rows, not the serialized blocks in ``result``: the
    ``only`` projection can skip building those, and a projection must never
    change what a surviving key contains.

    The legend explains the findings, not ``refactoring_plans``, so an entry can
    describe a biomarker no plan addresses. ``directive.plan_addresses_reason``
    reports that mismatch.
    """
    present_types = {getattr(r, "biomarker_type", None) for r in data.findings.legend_rows}
    return {bt: suggestion_for(bt) for bt in sorted(t for t in present_types if t)}
