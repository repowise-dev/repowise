"""Repository summaries for get_health: KPIs, gap analysis and per-file leads."""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.health.complexity.languages import NO_DIALECT_STATUS
from repowise.core.analysis.health.grading import TARGET_SCORE, analysed_share, headline_band
from repowise.core.analysis.health.models import primary_finding, split_by_origin
from repowise.core.analysis.health.perf.coverage import PerfCoverage
from repowise.core.analysis.health.ranking import worst_kpis
from repowise.core.analysis.health.rows import split_tests
from repowise.core.analysis.health.scoring import (
    hotspot_health,
    nloc_weighted_attr,
    nloc_weighted_score,
)
from repowise.core.persistence.crud.analysis.performance import PerformanceHeadline
from repowise.core.persistence.models import HealthFileMetric


def _leads_by_file(
    findings: list[Any], withheld: list[Any] | None = None
) -> dict[str, dict[str, Any]]:
    """Reduce each file's findings to its dominant cause + pre-clamp magnitude.

    ``primary_*`` is the one reason to lead with; ``total_deduction`` (summed
    ``health_impact``) tells apart two files that both floor at 1.0. The
    selection rule lives in ``analysis.health.models.primary_finding``.

    *withheld* rows, of types the registry keeps off this surface, never lead.
    The score counted them, so they add to ``total_deduction`` and show as
    ``withheld_deduction``: the card always accounts for what lowered the score.
    """
    by_file: dict[str, list[Any]] = {}
    for f in findings:
        by_file.setdefault(f.file_path, []).append(f)
    hidden: dict[str, float] = {}
    for f in withheld or ():
        hidden[f.file_path] = hidden.get(f.file_path, 0.0) + float(f.health_impact or 0.0)
    leads: dict[str, dict[str, Any]] = {}
    for path in by_file.keys() | hidden.keys():
        fs = by_file.get(path, [])
        withheld_points = round(hidden.get(path, 0.0), 3)
        total = round(sum(float(x.health_impact or 0.0) for x in fs) + withheld_points, 3)
        extra = {"withheld_deduction": withheld_points} if withheld_points else {}
        primary = primary_finding(fs)
        if primary is None:
            if withheld_points:
                leads[path] = {"total_deduction": total, **extra}
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
            "total_deduction": total,
            **extra,
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
        if points <= 0:
            return 0
        acc = 0.0
        for i, d in enumerate(below, 1):
            acc += d
            if acc >= points:
                return i
        return len(below)

    # At or above target there is nothing to reach, but the gross gap still
    # stands while any file is below target.
    # Rounded first, so a gap reported as 0 never needs a file to close it.
    reachable = round(max(net_gap, 0))
    return {
        "target_score": TARGET_SCORE,
        "weighted_gap_points": reachable,
        "weighted_gross_gap_points": round(gross_gap),
        "files_below_target": len(below),
        "files_to_reach_target": _files_for(reachable),
        "files_for_half_gap": _files_for(0.5 * reachable),
    }


def _perf_kpis(
    causes: PerformanceHeadline | None, coverage: PerfCoverage | None
) -> dict[str, Any]:
    """The honest performance headline: the open causes + coverage.

    Says how much of the code the perf pass ran on, so a bare
    ``performance_average`` near 10 is not read as "fast" on thin coverage.
    """
    return {
        "performance_causes": causes.as_dict() if causes is not None else None,
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
    performance_causes: PerformanceHeadline | None = None,
    coverage: PerfCoverage | None = None,
    unanalysed: int = 0,
    deductions: dict[str, float],
) -> dict[str, Any]:
    """*metrics* are scored rows; *unanalysed* counts the files left out of
    them because health has no dialect for their language. *deductions* is the
    worst-first tiebreak ``get_health_metrics`` ranks with."""
    if not metrics:
        return {
            "file_count": 0,
            "average_health": None,
            "band": None,
            # A repository of only unsupported languages is not missing data.
            "analysis_status": NO_DIALECT_STATUS if unanalysed else "unavailable",
            "hotspot_health": None,
            "worst_performer_path": None,
            "worst_performer_score": None,
            "worst_test_path": None,
            "worst_test_score": None,
            "maintainability_average": None,
            "performance_average": None,
            "structure_average": None,
            "history_average": None,
            **_perf_kpis(None, None),
        }
    total_nloc = sum(max(m.nloc, 1) for m in metrics)
    avg = sum(m.score * max(m.nloc, 1) for m in metrics) / total_nloc
    share = analysed_share(len(metrics), unanalysed)
    production, tests = split_tests(metrics)
    return {
        "file_count": len(metrics),
        "average_health": round(avg, 2),
        # Production code alone, weighted the same way; only said while tests
        # are in ``average_health``, so ``scope="production"`` omits it.
        **(
            {"production_average": round(nloc_weighted_score(production), 2)}
            if production and tests
            else {}
        ),
        # ``None`` with no hotspot files, not an empty set averaged to 10.0.
        "hotspot_health": hotspot_health(metrics, hotspot_paths or set()),
        # Weighted vs plain mean: a gap means a few large files hold it down.
        "average_health_weighting": "nloc",
        "average_health_unweighted": round(sum(m.score for m in metrics) / len(metrics), 2),
        # Withheld when too few code files were analysed to grade the repo.
        "band": headline_band(round(avg, 2), share),
        # Only said when some files were left out, as ``unanalysed_file_count`` is.
        **({"analysed_share": share} if unanalysed else {}),
        # The heads of ``worst_files`` and ``test_worst_files``, as REST names them.
        **worst_kpis(metrics, deductions),
        # ``None`` until the pillar is measured.
        "maintainability_average": _avg(metrics, "maintainability_score"),
        "performance_average": _avg(metrics, "performance_score"),
        # The headline's code-shape and history halves, in deduction points.
        "structure_average": _avg(metrics, "structure_deduction"),
        "history_average": _avg(metrics, "history_deduction"),
        # Performance leads with the causes worth fixing now and coverage, not the diluted /10.
        **_perf_kpis(performance_causes, coverage),
    }
