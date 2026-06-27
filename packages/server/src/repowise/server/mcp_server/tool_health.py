"""MCP tool: get_health — code-health markers and per-file scores."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.health.churn_complexity import churn_complexity_points
from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
from repowise.core.analysis.health.grading import band_for
from repowise.core.analysis.health.grading import distribution as health_distribution
from repowise.core.analysis.health.signals import file_signals
from repowise.core.analysis.health.suggestions import suggestion_for
from repowise.core.analysis.health.trends import diff_snapshots, file_trend, recent_kpis
from repowise.core.persistence.crud import (
    get_all_git_metadata,
    get_coverage_summary,
    get_git_metadata,
    get_graph_node,
    get_node_degree_counts,
    get_refactoring_suggestions,
    list_health_snapshots,
    load_coverage_for_repo,
)
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import HealthFileMetric, HealthFinding
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    filter_rows_by_attr,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta


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
        # Health pillar this finding homes under (defect / maintainability /
        # performance) for per-dimension filtering.
        "dimension": getattr(f, "dimension", None) or "defect",
    }


def _serialize_refactoring(r: Any) -> dict[str, Any]:
    """Serialize a ``RefactoringSuggestion`` ORM row into a structured plan.

    The ``*_json`` columns are decoded back into their open dicts so an agent
    reads the concrete plan (the split groups, the evidence, the blast radius)
    rather than a prose string.
    """

    def _load(raw: str | None) -> Any:
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    return {
        # The persisted row id — pass it to ``generate_refactoring_code`` to
        # turn this plan into actual code + a diff (opt-in).
        "id": getattr(r, "id", None),
        "refactoring_type": r.refactoring_type,
        "file_path": r.file_path,
        "target_symbol": r.target_symbol,
        "line_start": r.line_start,
        "line_end": r.line_end,
        "plan": _load(r.plan_json),
        "evidence": _load(r.evidence_json),
        "impact_delta": round(r.impact_delta, 3),
        "effort_bucket": r.effort_bucket,
        "blast_radius": _load(r.blast_radius_json),
        "confidence": r.confidence,
        "source_biomarker": r.source_biomarker,
    }


def _round_opt(v: Any) -> float | None:
    """Round a nullable per-dimension score, preserving ``None`` (not measured)."""
    return round(v, 2) if v is not None else None


def _serialize_metric(m: HealthFileMetric) -> dict[str, Any]:
    return {
        "file_path": m.file_path,
        "score": round(m.score, 2),
        "max_ccn": m.max_ccn,
        "max_nesting": m.max_nesting,
        "nloc": m.nloc,
        "has_test_file": m.has_test_file,
        "line_coverage_pct": m.line_coverage_pct,
        "branch_coverage_pct": m.branch_coverage_pct,
        "module": m.module,
        # Per-dimension scores from the three-signal split. ``score`` is the
        # overall surfaced number (== ``defect_score`` for now);
        # ``performance_score`` is computed but not yet surfaced as its own pillar.
        "defect_score": _round_opt(getattr(m, "defect_score", None)),
        "maintainability_score": _round_opt(getattr(m, "maintainability_score", None)),
        "performance_score": _round_opt(getattr(m, "performance_score", None)),
    }


def _serialize_coverage_row(row: Any) -> dict[str, Any]:
    try:
        covered = json.loads(row.covered_lines_json) if row.covered_lines_json else []
    except Exception:
        covered = []
    return {
        "file_path": row.file_path,
        "source_format": row.source_format,
        "line_coverage_pct": row.line_coverage_pct,
        "branch_coverage_pct": row.branch_coverage_pct,
        "covered_lines": covered,
        "total_coverable_lines": row.total_coverable_lines,
        "ingested_at": row.ingested_at.isoformat() if row.ingested_at else None,
        "ingested_commit_sha": row.ingested_commit_sha,
    }


def _module_rollups(metrics: list[HealthFileMetric]) -> list[dict[str, Any]]:
    """NLOC-weighted module rollups derived from ``HealthFileMetric.module``.

    One row per module; ``None`` modules are dropped. Sorted by health
    ascending so the worst modules surface first — matches the per-file
    ordering and what the dashboard already expects.
    """
    buckets: dict[str, list[HealthFileMetric]] = {}
    for m in metrics:
        if m.module:
            buckets.setdefault(m.module, []).append(m)
    out: list[dict[str, Any]] = []
    for name, rows in buckets.items():
        total_nloc = sum(max(r.nloc, 1) for r in rows)
        if total_nloc:
            avg = sum(r.score * max(r.nloc, 1) for r in rows) / total_nloc
        else:
            avg = sum(r.score for r in rows) / len(rows)
        worst = min(rows, key=lambda r: r.score)
        out.append(
            {
                "module": name,
                "file_count": len(rows),
                "nloc": sum(r.nloc for r in rows),
                "average_health": round(avg, 2),
                "worst_performer_path": worst.file_path,
                "worst_performer_score": round(worst.score, 2),
            }
        )
    out.sort(key=lambda r: r["average_health"])
    return out


def _dimension_average(metrics: list[HealthFileMetric], attr: str) -> float | None:
    """NLOC-weighted headline over a per-dimension score attribute.

    Skips rows without the attribute (those predating that pillar) so the KPI
    reads "not measured" rather than a misleading 10.0; ``None`` when no row
    carries it.
    """
    scored = [m for m in metrics if getattr(m, attr, None) is not None]
    if not scored:
        return None
    total_nloc = sum(max(m.nloc, 1) for m in scored)
    if not total_nloc:
        return round(sum(getattr(m, attr) for m in scored) / len(scored), 2)
    return round(sum(getattr(m, attr) * max(m.nloc, 1) for m in scored) / total_nloc, 2)


def _compute_kpis(metrics: list[HealthFileMetric]) -> dict[str, Any]:
    if not metrics:
        return {
            "file_count": 0,
            "average_health": 10.0,
            "worst_performer_path": None,
            "worst_performer_score": None,
            "maintainability_average": None,
            "performance_average": None,
        }
    total_nloc = sum(max(m.nloc, 1) for m in metrics)
    avg = sum(m.score * max(m.nloc, 1) for m in metrics) / total_nloc
    worst = min(metrics, key=lambda r: r.score)
    return {
        "file_count": len(metrics),
        "average_health": round(avg, 2),
        "band": band_for(round(avg, 2)),
        "worst_performer_path": worst.file_path,
        "worst_performer_score": round(worst.score, 2),
        # Maintainability + performance pillar headlines alongside the
        # defect-backed average. Each is ``None`` until its pillar is measured.
        "maintainability_average": _dimension_average(metrics, "maintainability_score"),
        "performance_average": _dimension_average(metrics, "performance_score"),
    }


@mcp.tool()
async def get_health(
    targets: list[str] | None = None,
    include: list[str] | None = None,
    repo: str | None = None,
    limit: int = 20,
) -> dict:
    """Code-health markers and per-file scores.

    Dashboard mode (no ``targets``) returns repo-level KPIs + the
    lowest-scoring files. Targeted mode returns per-file findings and
    metrics for each path in ``targets``.

    Markers in v1: ``brain_method``, ``nested_complexity``,
    ``complex_method``. Phase 2 adds coverage markers; Phase 3 adds
    duplication + organizational markers.

    Three-signal health: every file metric carries per-dimension scores. ``score``
    is the overall, defect-calibrated number surfaced everywhere (== ``defect_score``);
    ``maintainability_score`` is a co-equal signal made of the smells the defect
    calibration floors (cohesion, brain methods, primitive obsession, duplication,
    error handling) given full weight in their own pillar; ``performance_score`` is
    the third co-equal pillar: static performance RISK (I/O-in-loop / N+1 shapes that
    waste work), high-precision / low-recall, NEVER blended into the defect headline.
    ``kpis.maintainability_average`` and ``kpis.performance_average`` are the
    NLOC-weighted repo headlines for those pillars (``None`` when unmeasured). Each
    finding carries a ``dimension`` (``defect`` / ``maintainability`` /
    ``performance``) naming the pillar it homes under, so findings can be filtered
    per dimension. A performance finding's ``details`` carry the ``boundary_kind``
    it crosses (``db`` / ``network`` / ``filesystem`` / ``subprocess`` / ``lock``),
    a ``cross_function`` flag, and, for a cross-function N+1, the ``path`` (the
    resolved ``caller -> ... -> sink`` symbol chain). Performance is a static signal:
    dynamic dispatch, ORM lazy-load, and unmodelled libraries are out of scope.

    Self-check before a PR: an agent can read the same signals the code-health
    merge-gate judges a change on. ``include=["accuracy"]`` returns
    ``defect_accuracy`` (does the score actually rank the buggy files first —
    precision@K of the least-healthy files vs the repo bug-fix base rate, with a
    ``lift`` headline); ``include=["signals"]`` attaches per-file process / people
    / topology ``signals`` (prior-defect count, churn, owners, in/out degree) to
    each targeted metric; ``include=["churn_complexity"]`` returns the
    churn x complexity quadrant points (volatile-and-complex files are where
    defects concentrate); and a dimension name in ``include``
    (``"performance"`` / ``"defect"`` / ``"maintainability"``) filters the
    returned findings to that pillar.

    Args:
        targets: List of file paths (or ``module:<name>``). Empty → dashboard mode.
        include: Optional opt-in flags (default response stays lean):
            ``"biomarkers"`` returns findings in dashboard mode;
            ``"refactoring"`` attaches a deterministic ``suggestion`` per finding;
            ``"trend"`` adds the repo trend + alert block;
            ``"coverage"`` surfaces coverage rows when ingested;
            ``"accuracy"`` adds repo-level ``defect_accuracy`` (dashboard mode);
            ``"signals"`` attaches per-file ``signals`` (targeted mode);
            ``"churn_complexity"`` adds the churn x complexity points (dashboard);
            ``"performance"`` / ``"defect"`` / ``"maintainability"`` filter
            findings to that dimension.
        repo: Repo alias / id / path.
        limit: Max rows in the lowest-scoring file list (capped at 50).
    """
    limit = min(max(limit, 1), 50)
    include_set = set(include or [])

    # Split ``module:foo`` targets out of the path list. A target that
    # matches one or more modules is expanded into the set of files
    # belonging to those modules.
    raw_targets = list(targets or [])
    module_targets = [t.split(":", 1)[1] for t in raw_targets if t.startswith("module:")]
    file_targets = [t for t in raw_targets if not t.startswith("module:")]

    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session, repo)

        all_metrics_q = select(HealthFileMetric).where(
            HealthFileMetric.repository_id == repository.id
        )
        exclude_spec = _get_exclude_spec(ctx.path)
        all_metrics = filter_rows_by_attr(
            list((await session.execute(all_metrics_q)).scalars().all()),
            "file_path",
            exclude_spec,
        )

        if module_targets:
            module_set = set(module_targets)
            for m in all_metrics:
                if m.module in module_set:
                    file_targets.append(m.file_path)
            file_targets = sorted(set(file_targets))

        effective_targets = file_targets if (raw_targets) else []

        if effective_targets:
            metric_rows = [
                m
                for m in sorted(all_metrics, key=lambda r: r.score)
                if m.file_path in set(effective_targets)
            ]
        else:
            metric_rows = sorted(all_metrics, key=lambda r: r.score)

        finding_q = select(HealthFinding).where(
            HealthFinding.repository_id == repository.id,
            HealthFinding.status == "open",
        )
        if effective_targets:
            finding_q = finding_q.where(HealthFinding.file_path.in_(effective_targets))
        finding_q = finding_q.order_by(HealthFinding.health_impact.desc())
        finding_rows = filter_rows_by_attr(
            list((await session.execute(finding_q)).scalars().all()),
            "file_path",
            exclude_spec,
        )

        # Structured refactoring plans (Extract Class, ...) — loaded only when
        # asked for, scoped to the same targets, exclude-filtered like findings.
        refactoring_rows: list[Any] = []
        if "refactoring" in include_set:
            refactoring_rows = filter_rows_by_attr(
                await get_refactoring_suggestions(
                    session,
                    repository.id,
                    file_paths=list(effective_targets) if effective_targets else None,
                ),
                "file_path",
                exclude_spec,
            )

        coverage_rows: list[Any] = []
        coverage_summary: dict[str, Any] = {}
        if "coverage" in include_set:
            coverage_rows = filter_rows_by_attr(
                await load_coverage_for_repo(
                    session, repository.id, file_paths=list(targets) if targets else None
                ),
                "file_path",
                exclude_spec,
            )
            # coverage_summary is a repo-wide stored aggregate, not recomputed
            # here; the per-file rows above are exclude-filtered.
            coverage_summary = await get_coverage_summary(session, repository.id)

        # Per-file process/people/topology signals for targeted files — the
        # same join the file-detail drawer and REST breakdown use, so an agent
        # can read why a file is risky (prior defects, churn, owners, degree)
        # before touching it. Targeted mode only; the target set is small.
        signals_by_path: dict[str, dict[str, Any]] = {}
        if "signals" in include_set and effective_targets:
            for path in effective_targets:
                git_meta = await get_git_metadata(session, repository.id, path)
                node = await get_graph_node(session, repository.id, path)
                degrees = (
                    await get_node_degree_counts(session, repository.id, path)
                    if node is not None
                    else None
                )
                signals_by_path[path] = asdict(file_signals(git_meta, degrees))

        # Churn x complexity quadrant for the whole repo (dashboard mode). One
        # git-metadata query joined against the already-loaded metrics.
        churn_points: list[dict[str, Any]] = []
        if "churn_complexity" in include_set and not effective_targets:
            git_meta_by_path = await get_all_git_metadata(session, repository.id)
            churn_points = [
                asdict(p) for p in churn_complexity_points(all_metrics, git_meta_by_path)[:limit]
            ]

        # Load the snapshot window for the repo-level trend block and/or the
        # per-file trajectory we attach in targeted mode ("should I touch this
        # file" context for agents).
        snapshots: list[Any] = []
        if "trend" in include_set or effective_targets:
            snapshots = await list_health_snapshots(session, repository.id, limit=20)

    kpis = _compute_kpis(metric_rows if effective_targets else all_metrics)

    if effective_targets:
        metric_payload: list[dict[str, Any]] = []
        for m in metric_rows:
            row = _serialize_metric(m)
            if m.file_path in signals_by_path:
                row["signals"] = signals_by_path[m.file_path]
            metric_payload.append(row)
        result: dict[str, Any] = {
            "mode": "targets",
            "targets": raw_targets,
            "metrics": metric_payload,
            "findings": [_serialize_finding(f) for f in finding_rows],
        }
        # Per-file score trajectory for each target — silent (omitted) when a
        # file has < 2 snapshots of history rather than a misleading flat line.
        trends = []
        for m in metric_rows:
            t = file_trend(snapshots, m.file_path)
            if not t.points:
                continue
            trends.append(
                {
                    "file_path": t.file_path,
                    "series": [round(p.score, 2) for p in t.points],
                    "current": t.current,
                    "delta": t.delta,
                    "declining": t.declining,
                }
            )
        if trends:
            result["trends"] = trends
        if module_targets:
            scoped = [m for m in all_metrics if m.module in set(module_targets)]
            result["modules"] = _module_rollups(scoped)
    else:
        # Dashboard mode — top-N worst files + headline findings + the
        # per-module rollup so the overview page doesn't need a second
        # round-trip.
        result = {
            "mode": "dashboard",
            "kpis": kpis,
            "distribution": health_distribution(all_metrics),
            "worst_files": [_serialize_metric(m) for m in metric_rows[:limit]],
            "top_findings": [_serialize_finding(f) for f in finding_rows[:limit]],
            "modules": _module_rollups(all_metrics),
        }
        if "churn_complexity" in include_set:
            result["churn_complexity"] = churn_points
        if "accuracy" in include_set:
            # Self-validation: does the score rank the buggy files first? Pure
            # over the already-loaded metrics + findings (no extra query).
            # ``None`` when there isn't enough signal for an honest number.
            result["defect_accuracy"] = compute_defect_accuracy(
                all_metrics,
                [_serialize_finding(f) for f in finding_rows],
            )

    if "biomarkers" in include_set and "findings" not in result:
        result["findings"] = [_serialize_finding(f) for f in finding_rows]

    if "refactoring" in include_set:
        # Structured refactoring plans (the concrete split groups / evidence /
        # blast radius) for detectors that have one — the upgrade over the old
        # prose-string suggestions.
        result["refactoring_plans"] = [_serialize_refactoring(r) for r in refactoring_rows]
        # Attach the deterministic prose suggestion to every finding as the
        # fallback for biomarkers without a structured detector yet. Surfaces
        # on the dashboard cards too so both consumers stay in sync.
        for field in ("findings", "top_findings"):
            rows = result.get(field)
            if rows:
                result[field] = [
                    {**row, "suggestion": suggestion_for(row.get("biomarker_type", ""))}
                    for row in rows
                ]
        if "findings" not in result and "top_findings" not in result:
            result["findings"] = [
                {
                    **_serialize_finding(f),
                    "suggestion": suggestion_for(f.biomarker_type),
                }
                for f in finding_rows
            ]

    if "trend" in include_set:
        summary = diff_snapshots(snapshots)
        result["trend"] = {
            "current_hotspot_health": summary.current_hotspot_health,
            "current_average_health": summary.current_average_health,
            "previous_hotspot_health": summary.previous_hotspot_health,
            "previous_average_health": summary.previous_average_health,
            "hotspot_delta": summary.hotspot_delta,
            "average_delta": summary.average_delta,
            "alerts": [
                {
                    "kind": a.kind,
                    "metric": a.metric,
                    "current": a.current,
                    "baseline": a.baseline,
                    "delta": a.delta,
                    "message": a.message,
                }
                for a in summary.alerts
            ],
            "recent": recent_kpis(snapshots, limit=10),
        }

    if "coverage" in include_set:
        # Drop the bulky covered-lines arrays from dashboard mode; full
        # detail is available in targeted mode.
        if targets:
            coverage_payload = [_serialize_coverage_row(r) for r in coverage_rows]
        else:
            coverage_payload = [
                {k: v for k, v in _serialize_coverage_row(r).items() if k != "covered_lines"}
                for r in coverage_rows[:limit]
            ]
        # ``ingested_at`` is a datetime on the summary too — coerce.
        if coverage_summary.get("ingested_at") is not None:
            coverage_summary = {
                **coverage_summary,
                "ingested_at": coverage_summary["ingested_at"].isoformat(),
            }
        result["coverage"] = {
            "summary": coverage_summary,
            "files": coverage_payload,
        }

    # Dimension filter: ``include=["performance"]`` (or "defect" /
    # "maintainability") narrows the returned findings to that pillar so an
    # agent can ask "show me only the performance risk in this change". Applied
    # after the biomarkers/refactoring includes so it filters whatever findings
    # the response carries. Each finding's ``dimension`` is set at scoring time.
    dimension_filter = include_set & {"performance", "defect", "maintainability"}
    if dimension_filter:
        for field in ("findings", "top_findings"):
            rows = result.get(field)
            if rows:
                result[field] = [
                    r for r in rows if (r.get("dimension") or "defect") in dimension_filter
                ]

    result["_meta"] = _build_meta(repository=repository)
    return result
