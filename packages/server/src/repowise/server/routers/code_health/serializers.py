"""Pure wire-shape helpers: ORM rows / dataclasses -> JSON-able dicts."""

from __future__ import annotations

import json
from typing import Any

from repowise.core.analysis.health.signals import FileSignals
from repowise.core.analysis.health.trends import FileTrend


def _finding_to_dict(f: Any) -> dict:
    try:
        details = json.loads(f.details_json) if f.details_json else {}
    except Exception:
        details = {}
    return {
        "id": f.id,
        "file_path": f.file_path,
        "biomarker_type": f.biomarker_type,
        "severity": f.severity,
        "function_name": f.function_name,
        "line_start": f.line_start,
        "line_end": f.line_end,
        "health_impact": round(f.health_impact, 3),
        "reason": f.reason,
        "details": details,
        "status": f.status,
        # Pillar the finding homes under (defect / maintainability / performance)
        # so the UI can filter findings per dimension. Defaults to defect for
        # rows that predate the split.
        "dimension": getattr(f, "dimension", None) or "defect",
    }


def _round_opt(v: Any) -> float | None:
    """Round a nullable per-dimension score, preserving ``None`` (not measured)."""
    return round(v, 2) if v is not None else None


def _primary_and_magnitude(findings: list[Any]) -> dict:
    """Dominant cause + pre-clamp deduction magnitude for one file's findings.

    Two presentation signals the score alone can't carry:

    - ``primary_biomarker`` / ``primary_reason`` — the single worst finding, so a
      low file can lead with "the one reason" instead of a wall of markers.
    - ``total_deduction`` — the summed (pre-floor) ``health_impact``. Equal to
      the breakdown endpoint's ``total_deduction`` (each finding's stored impact
      is already the applied, capped value), so it distinguishes two files that
      both floor at 1.0 (a -25 file from a -9 one) without touching ``score``.

    All-null on an empty list: a clean file has no lead and no magnitude.
    """
    if not findings:
        return {"primary_biomarker": None, "primary_reason": None, "total_deduction": None}
    primary = max(findings, key=lambda x: x.health_impact)
    total = sum(float(x.health_impact or 0.0) for x in findings)
    return {
        "primary_biomarker": primary.biomarker_type,
        "primary_reason": primary.reason,
        "total_deduction": round(total, 3),
    }


def _leads_by_file(findings: list[Any]) -> dict[str, dict]:
    """Group findings by file and reduce each group to its dominant-cause lead."""
    by_file: dict[str, list[Any]] = {}
    for f in findings:
        by_file.setdefault(f.file_path, []).append(f)
    return {path: _primary_and_magnitude(fs) for path, fs in by_file.items()}


def _metric_to_dict(
    m: Any,
    lead: dict | None = None,
    *,
    perf_findings: int = 0,
    perf_analyzed: bool | None = None,
) -> dict:
    return {
        "file_path": m.file_path,
        "score": round(m.score, 2),
        "max_ccn": m.max_ccn,
        "max_nesting": m.max_nesting,
        "nloc": m.nloc,
        "has_test_file": m.has_test_file,
        "line_coverage_pct": m.line_coverage_pct,
        "module": m.module,
        "duplication_pct": getattr(m, "duplication_pct", None),
        # Per-dimension scores from the three-signal split. ``score`` above stays
        # the overall surfaced number (== defect_score for now).
        # ``performance_score`` is computed but not yet surfaced as its own pillar.
        "defect_score": _round_opt(getattr(m, "defect_score", None)),
        "maintainability_score": _round_opt(getattr(m, "maintainability_score", None)),
        "performance_score": _round_opt(getattr(m, "performance_score", None)),
        # Performance lens inputs for the code-health map: the open perf-finding
        # count colors the heat, and ``perf_analyzed`` (did a detector run on this
        # language?) separates green ("analyzed, none found") from grey ("never
        # looked"). Presentation only — the score above is untouched.
        "performance_findings": perf_findings,
        "performance_analyzed": perf_analyzed,
        # Dominant-cause lead + pre-clamp magnitude (null when findings weren't
        # loaded for this row, or the file is clean). Additive; readers degrade.
        "primary_biomarker": lead.get("primary_biomarker") if lead else None,
        "primary_reason": lead.get("primary_reason") if lead else None,
        "total_deduction": lead.get("total_deduction") if lead else None,
    }


def _file_trend_to_dict(t: FileTrend) -> dict:
    """Wire shape for ``FileHealthTrend`` (types/health.ts). ``points`` is
    empty on thin history so the UI shows a "no history yet" state."""
    return {
        "file_path": t.file_path,
        "points": [
            {
                "taken_at": p.taken_at.isoformat() if p.taken_at else None,
                "score": round(p.score, 2),
            }
            for p in t.points
        ],
        "current": t.current,
        "previous": t.previous,
        "delta": t.delta,
        "declining": t.declining,
        "snapshot_count": t.snapshot_count,
    }


def _file_signals_to_dict(s: FileSignals) -> dict:
    """Wire shape for ``FileSignals`` (types/health.ts). Each value is null
    when its source row is absent so the UI shows "no signal", never a
    misleading zero. ``change_entropy_pct`` is 0-100 (the column is 0-1)."""
    return {
        "prior_defect_count": s.prior_defect_count,
        "change_entropy_pct": s.change_entropy_pct,
        "lines_added_90d": s.lines_added_90d,
        "lines_deleted_90d": s.lines_deleted_90d,
        "commit_count_90d": s.commit_count_90d,
        "age_days": s.age_days,
        "primary_owner_name": s.primary_owner_name,
        "primary_owner_commit_pct": s.primary_owner_commit_pct,
        "recent_owner_name": s.recent_owner_name,
        "recent_owner_commit_pct": s.recent_owner_commit_pct,
        "in_degree": s.in_degree,
        "out_degree": s.out_degree,
    }


def _churn_complexity_to_dict(p: Any) -> dict:
    return {
        "file_path": p.file_path,
        "commit_count_90d": p.commit_count_90d,
        "max_ccn": p.max_ccn,
        "nloc": p.nloc,
        "score": p.score,
        "churn_percentile": p.churn_percentile,
    }
