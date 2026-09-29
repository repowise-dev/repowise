"""Repository summaries for get_health: KPIs, gap analysis, per-file leads, the directive."""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.health.grading import TARGET_SCORE, band_for
from repowise.core.analysis.health.models import primary_finding, split_by_origin
from repowise.core.analysis.health.perf.coverage import PerfCoverage
from repowise.core.analysis.health.scoring import (
    hotspot_health,
    nloc_weighted_attr,
)
from repowise.core.persistence.models import HealthFileMetric

# ``fix_first`` plus the two in ``then``. The plan lookup, the lead set and the
# directive must agree on it.
_DIRECTIVE_CANDIDATES = 3


def _leads_by_file(findings: list[Any]) -> dict[str, dict[str, Any]]:
    """Reduce each file's findings to its dominant cause + pre-clamp magnitude.

    ``primary_*`` is the one reason to lead with; ``total_deduction`` (summed
    ``health_impact``) tells apart two files that both floor at 1.0. The
    selection rule lives in ``analysis.health.models.primary_finding``.
    """
    by_file: dict[str, list[Any]] = {}
    for f in findings:
        by_file.setdefault(f.file_path, []).append(f)
    leads: dict[str, dict[str, Any]] = {}
    for path, fs in by_file.items():
        primary = primary_finding(fs)
        if primary is None:
            continue
        # No edit resolves a history marker, so ``actionable_*`` uses code shape
        # alone and ``watch_*`` carries history as context.
        code_shape, history = split_by_origin(fs)
        actionable = primary_finding(code_shape)
        watch = primary_finding(history)
        leads[path] = {
            "primary_biomarker": primary.biomarker_type,
            "primary_reason": primary.reason,
            "actionable_biomarker": actionable.biomarker_type if actionable else None,
            "actionable_reason": actionable.reason if actionable else None,
            "watch_biomarker": watch.biomarker_type if watch else None,
            "watch_reason": watch.reason if watch else None,
            "total_deduction": round(sum(float(x.health_impact or 0.0) for x in fs), 3),
        }
    return leads


def _directive(
    by_leverage: list[HealthFileMetric],
    leads: dict[str, dict[str, Any]],
    gap_points: int,
    plan_biomarkers_by_path: dict[str, set[str]] | None = None,
    plan_count_by_path: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    """The one file to fix first, and what fixing it buys.

    Every other block ranks and describes; this one recommends, like
    ``get_risk``'s ``directive``. Ranked by ``weighted_deficit`` (``score``
    floors at 1.0), so it names the file that moves the repo average.
    """
    if not by_leverage:
        return None
    # The highest-leverage file with something an edit can remove, else the
    # top file, flagged below rather than given an invented task.
    top = next(
        (m for m in by_leverage if (leads.get(m.file_path) or {}).get("actionable_biomarker")),
        by_leverage[0],
    )
    recovers = round(max(TARGET_SCORE - top.score, 0.0) * max(top.nloc, 1))
    lead = leads.get(top.file_path) or {}
    # Does a plan behind ``plan_via`` address the cause in ``reason``? Some
    # biomarkers (e.g. ``coverage_gradient``) have no plan kind at all.
    lead_biomarker = lead.get("actionable_biomarker")
    available = (plan_biomarkers_by_path or {}).get(top.file_path, set())
    addresses = bool(lead_biomarker) and lead_biomarker in available
    out = {
        "fix_first": top.file_path,
        "reason": lead.get("actionable_reason") or f"scores {round(top.score, 2)}",
        # What this file recovers at target, and its share of the gross gap
        # (see ``_gap_analysis`` for the denominator).
        "recovers_weighted_deficit_points": recovers,
        "recovers_points": recovers,
        "recovers_points_compatibility": {
            "deprecated": True,
            "replacement": "recovers_weighted_deficit_points",
            "equivalent_value": True,
        },
        "share_of_repo_gap_pct": (round(100.0 * recovers / gap_points, 1) if gap_points else None),
        "then": [m.file_path for m in by_leverage if m.file_path != top.file_path][:2],
        # Projected with ``only``: the bare ``include`` call restates the whole
        # dashboard and can exceed the MCP token cap.
        "plan_via": "get_health(include=['refactoring'], only=['refactoring_plans'])",
        "plan_addresses_reason": addresses,
    }
    # Context, not a task: no edit to this file settles history.
    if lead.get("watch_biomarker"):
        out["watch"] = {
            "biomarker": lead["watch_biomarker"],
            "reason": lead.get("watch_reason"),
            "note": "History-derived. Read it as context; there is nothing here to fix.",
        }
    if not lead_biomarker:
        # No file has a code-shape lead: the deficit is history.
        out["next_action"] = (
            "No file's leading cause is code shape; the deficit on this one is "
            "history. Read watch for what is moving and leave it alone."
        )
        return out
    if addresses:
        out["next_action"] = "inspect matching plan via plan_via"
        return out
    out["plan_note"] = _plan_note(
        lead_biomarker, available, (plan_count_by_path or {}).get(top.file_path, 0)
    )
    out["next_action"] = f"investigate {lead_biomarker}"
    return out


def _plan_note(lead_biomarker: str, available: set[str], n_plans: int) -> str:
    """Name the gap rather than leaving the caller to diff two biomarker vocabularies.

    The three branches call for different next moves: plans for other causes,
    plans with no recorded cause, or no plans.
    """
    if available:
        # Not "only X, Y": plans with an empty ``source_biomarker`` cannot be named.
        return (
            f"No stored plan addresses {lead_biomarker!r}; the plans on this file "
            f"target {', '.join(sorted(available))}. Treat plan_via as related "
            f"cleanup, not the fix for reason."
        )
    if n_plans:
        return (
            f"No stored plan addresses {lead_biomarker!r}; this file's {n_plans} "
            f"plan(s) record no source biomarker. Treat plan_via as related "
            f"cleanup, not the fix for reason."
        )
    return (
        f"No plan addresses {lead_biomarker!r}; this file has no plans. "
        "Use the finding itself."
    )


def _gap_analysis(metrics: list[HealthFileMetric]) -> dict[str, Any]:
    """How few files must reach 8.0 for the *weighted average* to reach 8.0.

    The NLOC-weighted average is held down by a few large low-scoring files, not
    the long tail. Two gaps, kept distinct:

    - ``weighted_gap_points``: the net points the average needs
      (``8.0 * total_nloc - sum(score * nloc)``). Above-target files cushion it.
      ``files_to_reach_target`` lifts the worst-deficit files until it closes.
    - ``weighted_gross_gap_points``: ``sum(max(8.0 - score, 0) * nloc)`` over
      below-target files, the ``share_of_repo_gap_pct`` denominator. Positive
      whenever any file is below target, and per-file shares sum to 100%; the
      net gap would let one large file exceed 100% (issue #1437).

    Pure over the metrics in hand.
    """
    total_nloc = sum(max(m.nloc, 1) for m in metrics)
    weighted_sum = sum(m.score * max(m.nloc, 1) for m in metrics)
    net_gap = TARGET_SCORE * total_nloc - weighted_sum
    below = sorted(
        (
            max(TARGET_SCORE - m.score, 0.0) * max(m.nloc, 1)
            for m in metrics
            if m.score < TARGET_SCORE
        ),
        reverse=True,
    )
    gross_gap = sum(below)
    if not below:
        return {
            "target_score": TARGET_SCORE,
            "weighted_gap_points": 0,
            "weighted_gross_gap_points": 0,
            "files_below_target": 0,
            "files_to_reach_target": 0,
            "files_for_half_gap": 0,
        }

    def _files_for(points: float) -> int:
        acc = 0.0
        for i, d in enumerate(below, 1):
            acc += d
            if acc >= points:
                return i
        return len(below)

    # At or above target there is nothing to reach, but the gross gap still
    # stands while any file is below target.
    reachable = max(net_gap, 0)
    return {
        "target_score": TARGET_SCORE,
        "weighted_gap_points": round(max(net_gap, 0)),
        "weighted_gross_gap_points": round(gross_gap),
        "files_below_target": len(below),
        "files_to_reach_target": _files_for(reachable),
        "files_for_half_gap": _files_for(0.5 * reachable),
    }


def _perf_kpis(performance_findings: int, coverage: PerfCoverage | None) -> dict[str, Any]:
    """The honest performance headline: finding count + density + coverage.

    Says how much of the code the perf pass ran on, so a bare
    ``performance_average`` near 10 is not read as "fast" on thin coverage.
    """
    density: float | None = None
    if coverage is not None and coverage.covered_nloc > 0:
        density = round(10000.0 * performance_findings / coverage.covered_nloc, 2)
    return {
        "performance_findings": performance_findings,
        "performance_findings_density_per_10k_loc": density,
        "performance_coverage_pct": (
            coverage.pct_loc if (coverage and coverage.analyzed_files) else None
        ),
        "performance_covered_files": coverage.covered_files if coverage else 0,
        "performance_analyzed_files": coverage.analyzed_files if coverage else 0,
        "performance_skipped_files": coverage.skipped_files if coverage else 0,
        "performance_unsupported_languages": (coverage.unsupported_languages if coverage else []),
    }


def _avg(metrics: list[HealthFileMetric], attr: str) -> float | None:
    """NLOC-weighted mean of one metric column, rounded for the wire."""
    value = nloc_weighted_attr(metrics, attr)
    return round(value, 2) if value is not None else None


def _compute_kpis(
    metrics: list[HealthFileMetric],
    *,
    hotspot_paths: set[str] | None = None,
    performance_findings: int = 0,
    coverage: PerfCoverage | None = None,
) -> dict[str, Any]:
    if not metrics:
        return {
            "file_count": 0,
            "average_health": None,
            "band": None,
            "analysis_status": "unavailable",
            "hotspot_health": None,
            "worst_performer_path": None,
            "worst_performer_score": None,
            "maintainability_average": None,
            "performance_average": None,
            "structure_average": None,
            "history_average": None,
            **_perf_kpis(0, None),
        }
    total_nloc = sum(max(m.nloc, 1) for m in metrics)
    avg = sum(m.score * max(m.nloc, 1) for m in metrics) / total_nloc
    worst = min(metrics, key=lambda r: r.score)
    return {
        "file_count": len(metrics),
        "average_health": round(avg, 2),
        # ``None`` with no hotspot files, not an empty set averaged to 10.0.
        "hotspot_health": hotspot_health(metrics, hotspot_paths or set()),
        # Weighted vs plain mean: a gap means a few large files hold it down.
        "average_health_weighting": "nloc",
        "average_health_unweighted": round(sum(m.score for m in metrics) / len(metrics), 2),
        "band": band_for(round(avg, 2)),
        "worst_performer_path": worst.file_path,
        "worst_performer_score": round(worst.score, 2),
        # ``None`` until the pillar is measured.
        "maintainability_average": _avg(metrics, "maintainability_score"),
        "performance_average": _avg(metrics, "performance_score"),
        # The headline's code-shape and history halves, in deduction points.
        "structure_average": _avg(metrics, "structure_deduction"),
        "history_average": _avg(metrics, "history_deduction"),
        # Performance leads with count + density + coverage, not the diluted /10.
        **_perf_kpis(performance_findings, coverage),
    }
