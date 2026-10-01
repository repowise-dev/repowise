"""Repository summaries for get_health: KPIs, gap analysis and per-file leads."""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.health.grading import TARGET_SCORE, band_for
from repowise.core.analysis.health.models import primary_finding, split_by_origin
from repowise.core.analysis.health.perf.coverage import PerfCoverage
from repowise.core.analysis.health.rows import split_tests
from repowise.core.analysis.health.scoring import (
    hotspot_health,
    nloc_weighted_attr,
)
from repowise.core.persistence.models import HealthFileMetric


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
            "worst_test_path": None,
            "worst_test_score": None,
            "maintainability_average": None,
            "performance_average": None,
            "structure_average": None,
            "history_average": None,
            **_perf_kpis(0, None),
        }
    total_nloc = sum(max(m.nloc, 1) for m in metrics)
    avg = sum(m.score * max(m.nloc, 1) for m in metrics) / total_nloc
    # Production first; test files are ranked apart and named on their own.
    production, tests = split_tests(metrics)
    worst = min(production or metrics, key=lambda r: r.score)
    worst_test = min(tests, key=lambda r: r.score) if tests else None
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
        "worst_test_path": worst_test.file_path if worst_test else None,
        "worst_test_score": round(worst_test.score, 2) if worst_test else None,
        # ``None`` until the pillar is measured.
        "maintainability_average": _avg(metrics, "maintainability_score"),
        "performance_average": _avg(metrics, "performance_score"),
        # The headline's code-shape and history halves, in deduction points.
        "structure_average": _avg(metrics, "structure_deduction"),
        "history_average": _avg(metrics, "history_deduction"),
        # Performance leads with count + density + coverage, not the diluted /10.
        **_perf_kpis(performance_findings, coverage),
    }
