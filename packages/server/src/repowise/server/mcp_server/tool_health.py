"""MCP tool: get_health — code-health biomarkers and per-file scores."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import HealthFileMetric, HealthFinding
from repowise.server.mcp_server._helpers import _get_repo, _resolve_repo_context
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._server import mcp


def _serialize_finding(f: HealthFinding) -> dict[str, Any]:
    try:
        details = json.loads(f.details_json) if f.details_json else {}
    except Exception:
        details = {}
    return {
        "biomarker_type": f.biomarker_type,
        "severity": f.severity,
        "file_path": f.file_path,
        "function_name": f.function_name,
        "line_start": f.line_start,
        "line_end": f.line_end,
        "health_impact": round(f.health_impact, 3),
        "reason": f.reason,
        "details": details,
        "status": f.status,
    }


def _serialize_metric(m: HealthFileMetric) -> dict[str, Any]:
    return {
        "file_path": m.file_path,
        "score": round(m.score, 2),
        "max_ccn": m.max_ccn,
        "max_nesting": m.max_nesting,
        "nloc": m.nloc,
        "has_test_file": m.has_test_file,
        "line_coverage_pct": m.line_coverage_pct,
        "module": m.module,
    }


def _compute_kpis(metrics: list[HealthFileMetric]) -> dict[str, Any]:
    if not metrics:
        return {
            "file_count": 0,
            "average_health": 10.0,
            "worst_performer_path": None,
            "worst_performer_score": None,
        }
    total_nloc = sum(max(m.nloc, 1) for m in metrics)
    avg = sum(m.score * max(m.nloc, 1) for m in metrics) / total_nloc
    worst = min(metrics, key=lambda r: r.score)
    return {
        "file_count": len(metrics),
        "average_health": round(avg, 2),
        "worst_performer_path": worst.file_path,
        "worst_performer_score": round(worst.score, 2),
    }


@mcp.tool()
async def get_health(
    targets: list[str] | None = None,
    include: list[str] | None = None,
    repo: str | None = None,
    limit: int = 20,
) -> dict:
    """Code-health biomarkers and per-file scores.

    Dashboard mode (no ``targets``) returns repo-level KPIs + the
    lowest-scoring files. Targeted mode returns per-file findings and
    metrics for each path in ``targets``.

    Biomarkers in v1: ``brain_method``, ``nested_complexity``,
    ``complex_method``. Phase 2 adds coverage biomarkers; Phase 3 adds
    duplication + organizational biomarkers.

    Args:
        targets: List of file paths. Empty → dashboard mode.
        include: Optional flags. ``"biomarkers"`` always returns findings;
            ``"coverage"`` will surface coverage data when available
            (Phase 2).
        repo: Repo alias / id / path.
        limit: Max rows in the lowest-scoring file list (capped at 50).
    """
    limit = min(max(limit, 1), 50)
    include_set = set(include or [])

    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session, repo)

        metric_q = select(HealthFileMetric).where(HealthFileMetric.repository_id == repository.id)
        if targets:
            metric_q = metric_q.where(HealthFileMetric.file_path.in_(list(targets)))
        metric_q = metric_q.order_by(HealthFileMetric.score.asc())
        metric_rows = list((await session.execute(metric_q)).scalars().all())

        finding_q = select(HealthFinding).where(
            HealthFinding.repository_id == repository.id,
            HealthFinding.status == "open",
        )
        if targets:
            finding_q = finding_q.where(HealthFinding.file_path.in_(list(targets)))
        finding_q = finding_q.order_by(HealthFinding.health_impact.desc())
        finding_rows = list((await session.execute(finding_q)).scalars().all())

    kpis = _compute_kpis(metric_rows)

    if targets:
        result: dict[str, Any] = {
            "mode": "targets",
            "targets": list(targets),
            "metrics": [_serialize_metric(m) for m in metric_rows],
            "findings": [_serialize_finding(f) for f in finding_rows],
        }
    else:
        # Dashboard mode — top-N worst files + headline findings.
        result = {
            "mode": "dashboard",
            "kpis": kpis,
            "worst_files": [_serialize_metric(m) for m in metric_rows[:limit]],
            "top_findings": [_serialize_finding(f) for f in finding_rows[:limit]],
        }

    if "biomarkers" in include_set and "findings" not in result:
        result["findings"] = [_serialize_finding(f) for f in finding_rows]

    result["_meta"] = _build_meta(repository=repository)
    return result
