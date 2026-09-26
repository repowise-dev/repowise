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

# Files the directive reduces over: ``fix_first`` plus the two in ``then``. Named
# because three separate places have to agree on it — the plan lookup, the lead
# set the directive reads, and the directive itself — and they are far apart.
_DIRECTIVE_CANDIDATES = 3


def _leads_by_file(findings: list[Any]) -> dict[str, dict[str, Any]]:
    """Reduce each file's findings to its dominant cause + pre-clamp magnitude.

    ``primary_biomarker`` / ``primary_reason`` give a low file "the one reason"
    to lead with; ``total_deduction`` (summed ``health_impact``) distinguishes
    two files that both floor at 1.0. Additive — the score itself is untouched.

    The headline prefers the strongest **discrete** finding. A continuous
    biomarker fires on every file carrying its input signal, so on a repo with
    coverage data ``coverage_gradient`` wins the max-impact tiebreak nearly
    everywhere: measured on this repo before the preference, it led 22 of the
    top 50 ``worst_files`` and 14 of the top 50 ``high_leverage_files`` with
    "N% of lines uncovered", which is true and tells a reader nothing about why
    this file rather than any other. The gradient still counts in
    ``total_deduction`` and still leads when it is a file's only finding — it
    just stops crowding out a nameable cause. That selection rule now lives in
    ``analysis.health.models.primary_finding``, so this and the composition
    layer cannot drift about which finding a file leads with.
    """
    by_file: dict[str, list[Any]] = {}
    for f in findings:
        by_file.setdefault(f.file_path, []).append(f)
    leads: dict[str, dict[str, Any]] = {}
    for path, fs in by_file.items():
        primary = primary_finding(fs)
        if primary is None:
            continue
        # The same rule applied to code shape alone. A file can be led by a
        # history marker, and a caller told to fix that has been handed
        # something no edit resolves; ``watch_*`` carries it as context instead.
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

    Every other block here ranks and describes; none of them recommends. That
    gap is why a correct finding can sit at position 12 of an undifferentiated
    list and change nobody's behaviour. Same role as ``get_risk``'s
    ``directive``: lead with the call, keep the evidence underneath.

    Ranked by ``weighted_deficit`` (not ``score``, which floors at 1.0), so
    this names the file that actually moves the repo average.
    """
    if not by_leverage:
        return None
    # The highest-leverage file that has something an edit can remove. A file
    # whose whole deficit is history would otherwise be named ``fix_first``
    # with nothing to fix underneath it, which is the failure this block exists
    # to prevent. Falls back to the top file when no candidate has one, and
    # says so rather than inventing a task.
    top = next(
        (m for m in by_leverage if (leads.get(m.file_path) or {}).get("actionable_biomarker")),
        by_leverage[0],
    )
    recovers = round(max(TARGET_SCORE - top.score, 0.0) * max(top.nloc, 1))
    lead = leads.get(top.file_path) or {}
    # Does anything behind ``plan_via`` actually address the cause named in
    # ``reason``? Plans carry the biomarker that produced them, and several
    # biomarkers have no detector at all — ``coverage_gradient`` above all, which
    # no plan kind can answer because none of them writes tests. Saying so beats
    # routing the caller to plans for a different problem with full confidence.
    # The cause named here has to be one an edit can remove, or the whole block
    # recommends something impossible. History is reported alongside, in its own
    # field, and never as ``reason``.
    lead_biomarker = lead.get("actionable_biomarker")
    available = (plan_biomarkers_by_path or {}).get(top.file_path, set())
    addresses = bool(lead_biomarker) and lead_biomarker in available
    out = {
        "fix_first": top.file_path,
        "reason": lead.get("actionable_reason") or f"scores {round(top.score, 2)}",
        # Points the repo headline recovers if this one file reaches the target,
        # and what share of the total gap that is — the "few files, not the
        # long tail" argument made concrete for a single file. The denominator
        # is the *gross* deficit of all below-target files (not the net gap,
        # which above-target files cushion): per-file shares are then bounded by
        # 100% and sum to 100% by construction (issue #1437).
        "recovers_weighted_deficit_points": recovers,
        "recovers_points": recovers,
        "recovers_points_compatibility": {
            "deprecated": True,
            "replacement": "recovers_weighted_deficit_points",
            "equivalent_value": True,
        },
        "share_of_repo_gap_pct": (round(100.0 * recovers / gap_points, 1) if gap_points else None),
        "then": [m.file_path for m in by_leverage if m.file_path != top.file_path][:2],
        # Projected, not bare. ``include`` adds a block without subtracting the
        # dashboard, and five ranked lists at the default ``limit`` compose: the
        # bare ``include=['refactoring']`` measured 70,776 chars on this repo
        # (refactoring_plans 34%, the other four lists 59%) and simply fails the
        # MCP token cap, so the one call the directive tells an agent to make was
        # the one call it could not complete. ``only`` already exists to fix this
        # (it gates the work as well as the payload); the directive just has to
        # ask for it. Same block, ~24k chars, no dashboard restated.
        "plan_via": "get_health(include=['refactoring'], only=['refactoring_plans'])",
        "plan_addresses_reason": addresses,
    }
    # Context, not a task. These move with the repository's history and no edit
    # to this file settles them.
    if lead.get("watch_biomarker"):
        out["watch"] = {
            "biomarker": lead["watch_biomarker"],
            "reason": lead.get("watch_reason"),
            "note": "History-derived. Read it as context; there is nothing here to fix.",
        }
    if not lead_biomarker:
        # Nothing in the repository has a code-shape lead, so the honest answer
        # is that the deficit is history and no edit here settles it.
        out["next_action"] = (
            "No file's leading cause is code shape; the deficit on this one is "
            "history. Read watch for what is moving and leave it alone."
        )
        return out
    # Only speak when there is a named cause to speak about (the early return
    # above). With no lead the ``reason`` already falls back to the bare score,
    # and a note reading "No stored plan addresses None" would be worse than
    # silence.
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
        # Deliberately "target X, Y" rather than "the plans target only X, Y":
        # plans with an empty ``source_biomarker`` are counted in ``n_plans``
        # but cannot be named, so an exhaustive phrasing would be a claim
        # this read cannot support.
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

    Answers what the bare KPI cannot: the NLOC-weighted average is held down by a
    *few large low-scoring files*, not the long tail. Two gaps are computed and
    kept deliberately distinct:

    - ``weighted_gap_points`` — the **net** points the average needs
      (``8.0 * total_nloc - Σ score*nloc``). Files already above the target
      cushion it, so this is smaller than the gross every-file-at-target
      deficit and is the number that matches the goal "move the average".
      ``files_to_reach_target`` is the punchline: lift the worst-deficit N files
      to 8.0 and the headline crosses 8.0. This can be 0 or negative on a
      repo that is mostly at target (the average is already above 8.0).
    - ``weighted_gross_gap_points`` — the **gross** deficit,
      ``Σ max(8.0 - score, 0) * nloc`` over files below 8.0. This is the
      denominator ``share_of_repo_gap_pct`` uses: it is positive whenever any
      file is below target (unlike the net gap), so a share is meaningful even
      when the average is already at target, and per-file shares sum to exactly
      100% by construction. The net gap is not used there precisely because
      above-target files cushion it — one large low file could then read as closing
      more than the whole remaining gap (issue #1437).

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

    # ``files_to_reach_target`` needs the net gap to mean "the headline crosses
    # 8.0". When the net gap is <= 0 (average already at target) there is nothing
    # to reach, so those fields are 0 — but the gross gap is still reported
    # because per-file shares are meaningful whenever any file is below target.
    reachable = max(net_gap, 0)
    return {
        "target_score": TARGET_SCORE,
        # Net weighted points the average must recover to reach 8.0.
        "weighted_gap_points": round(max(net_gap, 0)),
        # Gross deficit of all below-target files: the share_of_repo_gap_pct
        # denominator (see docstring for why it is not the net gap).
        "weighted_gross_gap_points": round(gross_gap),
        "files_below_target": len(below),
        # The reframe: lift this many worst-deficit files to 8.0 and the weighted
        # average reaches 8.0; half that gap needs even fewer.
        "files_to_reach_target": _files_for(reachable),
        "files_for_half_gap": _files_for(0.5 * reachable),
    }


def _perf_kpis(performance_findings: int, coverage: PerfCoverage | None) -> dict[str, Any]:
    """The honest performance headline: finding count + density + coverage.

    Leads with *how many* findings and over *how much* of the code the perf pass
    ran, so an agent never reads a bare ``performance_average`` of ~10 as "fast"
    when the real story is "we could only analyze 3% of this repo".
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
        # ``None`` when the repo has no hotspot files, which is a real answer:
        # the alternative is averaging an empty set to a perfect 10.0 and
        # reporting it as a score, the same fabricated-10.0 problem the comment
        # above objects to for non-code rows.
        "hotspot_health": hotspot_health(metrics, hotspot_paths or set()),
        # NLOC-weighted (``average_health``) vs plain file mean. When these
        # diverge, a few large low-scoring files are holding the headline down —
        # the weighted number is what the dashboard/badge surface, and the gap
        # between the two is the signal to chase big files, not the long tail.
        "average_health_weighting": "nloc",
        "average_health_unweighted": round(sum(m.score for m in metrics) / len(metrics), 2),
        "band": band_for(round(avg, 2)),
        "worst_performer_path": worst.file_path,
        "worst_performer_score": round(worst.score, 2),
        # Maintainability + performance pillar headlines alongside the
        # defect-backed average. Each is ``None`` until its pillar is measured.
        "maintainability_average": _avg(metrics, "maintainability_score"),
        "performance_average": _avg(metrics, "performance_score"),
        # The headline's two halves, in deduction points, so a caller can see
        # whether code shape or history is holding the number down.
        "structure_average": _avg(metrics, "structure_deduction"),
        "history_average": _avg(metrics, "history_deduction"),
        # Performance leads with count + density + coverage, not the diluted /10.
        **_perf_kpis(performance_findings, coverage),
    }
