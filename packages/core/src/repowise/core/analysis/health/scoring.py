"""Per-file score aggregation + repo-level KPIs.

Each file starts at 10.0. Biomarker findings deduct; deductions are
capped per category so no single category can drive the score below the
cap. Final score is clamped to [1.0, 10.0].

The recalibrated category caps (plan §3.1):

    organizational        -> -3.5   # was -1.0 (process-aware signals)
    structural_complexity -> -2.5   # was -3.5
    test_coverage         -> -2.0
    size_and_complexity   -> -1.5   # was -2.0
    duplication           -> -1.0   # was -1.5

Per-biomarker weight multipliers (plan §3.2 Option A) are applied to
the per-finding raw deduction *before* category capping, so the strongest
empirical predictors are no longer suppressed by uniform severity values.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .biomarkers.base import BiomarkerResult
from .models import HealthFileMetricData, HealthFindingData, Severity

# Per-category max deduction.
CATEGORY_CAPS: dict[str, float] = {
    "organizational": 3.5,
    "structural_complexity": 2.5,
    "test_coverage": 2.0,
    # Continuous coverage-gradient deduction (scales with the uncovered
    # fraction). Kept in its OWN capped category, separate from the binary
    # ``test_coverage`` gates, so the additive, monotonic coverage signal is
    # bounded on its own terms and never squeezed by - or squeezes - the
    # has-tests / hotspot gates. Calibrated offline: a per-file deduction of
    # 4.0 x uncovered_fraction (this cap binding at ≥50% uncovered) recovers
    # +0.043 corpus AUC [95% CI +0.023, +0.061] on the covered subset,
    # Popt-neutral, and is exactly zero where coverage was never ingested.
    "test_coverage_gradient": 2.0,
    "size_and_complexity": 1.5,
    "duplication": 1.0,
    # Test-quality smells are mild, advisory signals - a small cap keeps a
    # noisy test file from dominating its own health score.
    "test_quality": 0.5,
    # Error-handling anti-patterns (swallowed catch / bare except / unsafe
    # unwrap / discarded Go error) are an advisory maintainability signal,
    # not a calibrated defect predictor (AUC-neutral on the 21-repo T0
    # benchmark; size-orthogonal, least redundant signal tested). Own
    # capped category so the bounded deduction can never squeeze - or be
    # squeezed by - the predictive categories.
    "error_handling": 0.5,
}

# Per-biomarker deduction by severity. The scorer caps the per-category
# total at the value in ``CATEGORY_CAPS``.
_SEVERITY_DEDUCTION: dict[Severity, float] = {
    Severity.LOW: 0.3,
    Severity.MEDIUM: 0.7,
    Severity.HIGH: 1.2,
    Severity.CRITICAL: 2.0,
}

# Per-biomarker weight multiplier. Applied to the severity deduction BEFORE
# category capping, so stronger empirical predictors deduct more without
# re-tuning the severity table. Unknown biomarkers fall back to 1.0.
#
# CALIBRATED OFFLINE (2026-05-29) against a 13-repo, 5-language defect corpus
# (Python/TS/JS/Rust/Go; 830 files, 216 bug-fix-bearing). Methodology: each
# file scored at the pre-window commit T0 (no HEAD->window leakage), then an
# L2-regularized logistic regression of "received a bug-fix in (T0,T1]" on the
# per-biomarker hits PLUS an explicit NLOC control column - so each weight
# reflects the biomarker's defect lift *beyond file size*. Cross-project
# (leave-one-repo-out) pooled OOF AUC ~ 0.70. The fit is reproduced by
# `local-stash/calibrate_health_weights.py`; the runtime stays pure-
# deterministic / zero-LLM - only these learned constants ship.
#
# Mapping policy ("balanced"): positive, well-measured predictors are scaled
# into [1.0, 1.8] ∝ coefficient; biomarkers that fired widely but were weak/
# non-predictive at T0 are floored to 0.5 (kept as mild maintainability/parity
# signals, not disabled); biomarkers the benchmark could NOT measure (no
# coverage ingested -> untested_hotspot/coverage_gap; test-only assertion smells;
# too-rare churn_risk/hidden_coupling; the gate-bound code_age_volatility) keep
# their prior weight. Top calibrated predictors: co_change_scatter (1.8),
# change_entropy (1.51), ownership_risk (1.38), nested_complexity (1.34).
#
# Governance biomarkers (contradictory_decision, stale_governance,
# ungoverned_hotspot) are written by the additive governance pass after the
# per-file score pass, so their weights are documentation-only.
_BIOMARKER_WEIGHT_MULTIPLIER: dict[str, float] = {
    # --- calibrated predictors (positive lift beyond size) ---
    "co_change_scatter": 1.8,
    "change_entropy": 1.51,
    "ownership_risk": 1.38,
    "nested_complexity": 1.34,
    "complex_conditional": 1.33,
    "large_method": 1.25,
    "complex_method": 1.21,
    "function_hotspot": 1.16,
    "god_class": 1.13,
    # Prior-defect history (recent bug-fix count). NEUTRAL weight by design, not
    # a boost: on the 13-repo corpus its calibrated coefficient is ~+0.02 (no lift
    # beyond size + the existing process signals - it correlates +0.59 with
    # change_entropy, +0.38 with churn) and the effort-aware Popt gain is +0.01
    # with bootstrap CIs straddling zero. So it ships as an interpretable,
    # leakage-free finding ("bug-fixed N times recently" - directly actionable,
    # and it uniquely flags a handful of files the other signals miss), NOT as a
    # calibrated predictor. See local-stash/{calibrate_health_weights,
    # measure_prior_defect,diagnose_prior_defect}.py + the progress doc Phase 8.5.
    "prior_defect": 1.0,
    # --- kept at prior: benchmark could not fairly measure these ---
    "untested_hotspot": 1.3,  # benchmark ingests no coverage (has_test_file fallback only)
    "churn_risk": 1.2,  # fired in too few repos to calibrate
    "code_age_volatility": 1.1,  # gate unmet at T0 across the corpus
    # --- floored: fired widely but weak / non-predictive at T0 ---
    "developer_congestion": 0.5,  # was 1.5 - the HEAD-leakage hero; weak under T0
    "low_cohesion": 0.5,
    "brain_method": 0.5,
    "bumpy_road": 0.5,
    "primitive_obsession": 0.5,
    "dry_violation": 0.5,
    "knowledge_loss": 0.4,  # confirmed weak-negative since Phase 1
    # Error-handling anti-patterns: maintainability flag, NOT a fitted
    # predictor - deliberately excluded from the calibration roster (same
    # treatment as governance biomarkers). Floored weight + LOW severity
    # (0.3 x 0.5 = 0.15/finding) + the 0.5 category cap keep the impact
    # bounded at half a point per file regardless of hit count.
    "error_handling": 0.5,
    # (coverage_gap, hidden_coupling, large_assertion_block,
    #  duplicated_assertion_block default to 1.0 - kept at prior)
    # Governance - additive pass, weights are informational
    "contradictory_decision": 1.0,
    "stale_governance": 0.9,
    "ungoverned_hotspot": 0.7,
}

# Map biomarker name -> category. Kept here (single source of truth)
# rather than on each biomarker class because some biomarkers naturally
# span categories and we may want to retune without re-deploying.
_BIOMARKER_CATEGORY: dict[str, str] = {
    "brain_method": "structural_complexity",
    "low_cohesion": "structural_complexity",
    "god_class": "structural_complexity",
    "nested_complexity": "structural_complexity",
    "bumpy_road": "structural_complexity",
    "complex_conditional": "structural_complexity",
    "complex_method": "size_and_complexity",
    "large_method": "size_and_complexity",
    "primitive_obsession": "size_and_complexity",
    "dry_violation": "duplication",
    "untested_hotspot": "test_coverage",
    "coverage_gap": "test_coverage",
    "coverage_gradient": "test_coverage_gradient",
    "developer_congestion": "organizational",
    "knowledge_loss": "organizational",
    "hidden_coupling": "organizational",
    "function_hotspot": "organizational",
    "code_age_volatility": "organizational",
    "ownership_risk": "organizational",
    "churn_risk": "organizational",
    "change_entropy": "organizational",
    "co_change_scatter": "organizational",
    "prior_defect": "organizational",
    "large_assertion_block": "test_quality",
    "duplicated_assertion_block": "test_quality",
    "error_handling": "error_handling",
    # Governance biomarkers - written by the additive governance pass
    "ungoverned_hotspot": "organizational",
    "stale_governance": "organizational",
    "contradictory_decision": "organizational",
}


# ---------------------------------------------------------------------------
# Per-dimension scoring (defect / maintainability / performance)
# ---------------------------------------------------------------------------
#
# The single surfaced health score is, and remains, the DEFECT score: today's
# exact weights / categories / caps (the tables above), unchanged. Splitting the
# score into dimensions is purely additive: ``maintainability`` and
# ``performance`` are independent, independently-capped signals derived from the
# SAME biomarker stream, and they NEVER feed back into ``defect``.
#
# The load-bearing guarantee (locked by ``tests/unit/health/test_scoring_dimensions``)
# is that ``score_file(results)["defect"]`` reproduces the pre-split single
# score byte-for-byte for any input. If that drifts, the split is wrong.

DIMENSIONS: tuple[str, ...] = ("defect", "maintainability", "performance")

# Which dimensions each biomarker's deduction feeds. Biomarkers not listed here
# contribute to ``defect`` only - the historical behaviour, since every
# biomarker has always counted toward the single score. The maintainability
# smells the defect calibration floored to 0.5 (because they don't predict bugs)
# get their full weight back in ``maintainability``; the structural smells are
# genuine defect predictors AND core maintainability signals, so they count
# toward both.
_BIOMARKER_DIMENSIONS: dict[str, set[str]] = {
    # Floored-in-defect maintainability smells -> full weight in maintainability.
    "low_cohesion": {"defect", "maintainability"},
    "brain_method": {"defect", "maintainability"},
    "primitive_obsession": {"defect", "maintainability"},
    "dry_violation": {"defect", "maintainability"},
    "error_handling": {"defect", "maintainability"},
    # Structural smells: calibrated defect predictors that are ALSO core
    # maintainability signals - they contribute to both dimensions.
    "god_class": {"defect", "maintainability"},
    "large_method": {"defect", "maintainability"},
    "nested_complexity": {"defect", "maintainability"},
    # Performance-risk detectors contribute to ``performance`` ONLY. They are
    # NOT defect predictors and must never move the surfaced (defect) score -
    # that exclusion is what keeps the defect golden guarantee intact.
    "io_in_loop": {"performance"},
    "string_concat_in_loop": {"performance"},
    "blocking_sync_in_async": {"performance"},
    # Phase 7a loop markers - performance-only, same as the originals.
    "resource_construction_in_loop": {"performance"},
    "lock_in_loop": {"performance"},
    "serial_await_in_loop": {"performance"},
    "membership_test_against_list_in_loop": {"performance"},
    # Phase 7b centrality-gated moat markers - performance-only.
    "nested_loop_with_io": {"performance"},
    "nested_loop_quadratic": {"performance"},
    "hot_path_sync_io": {"performance"},
    "blocking_io_under_lock": {"performance"},
}

# Maintainability per-biomarker weight multipliers. Expert-set by definition -
# the defect calibration does not apply to a non-defect signal. The smells the
# defect score floors to 0.5 deduct at FULL weight (1.0) here; the structural
# duals stay at 1.0 too. Tuned only against the maintainability cap budget
# below, never against the defect corpus. Unknown biomarkers fall back to 1.0.
_MAINTAINABILITY_WEIGHT_MULTIPLIER: dict[str, float] = {
    "low_cohesion": 1.0,
    "brain_method": 1.0,
    "primitive_obsession": 1.0,
    "dry_violation": 1.0,
    "error_handling": 1.0,
    "god_class": 1.0,
    "large_method": 1.0,
    "nested_complexity": 1.0,
}

# Maintainability category per biomarker - an OWN table, independent of the
# defect category map, so the two dimensions can be retuned separately.
_MAINTAINABILITY_CATEGORY: dict[str, str] = {
    "brain_method": "structural_complexity",
    "low_cohesion": "structural_complexity",
    "god_class": "structural_complexity",
    "nested_complexity": "structural_complexity",
    "large_method": "structural_complexity",
    "primitive_obsession": "size_and_complexity",
    "dry_violation": "duplication",
    "error_handling": "error_handling",
}

# Maintainability per-category caps. Bounded so no single category dominates the
# maintainability score, mirroring the defect cap discipline but on the
# maintainability dimension's own budget.
_MAINTAINABILITY_CATEGORY_CAPS: dict[str, float] = {
    "structural_complexity": 4.0,
    "size_and_complexity": 2.0,
    "duplication": 2.0,
    "error_handling": 2.0,
}

# A finding's single "home" dimension, used for display and per-pillar
# filtering. Biomarkers that exist ONLY as maintainability signals home there;
# the structural duals and every calibrated predictor home to ``defect`` (their
# primary, calibrated role). Multi-dimension membership for scoring lives in
# ``_BIOMARKER_DIMENSIONS`` - this label is just the finding's primary bucket.
_MAINTAINABILITY_HOME: frozenset[str] = frozenset(
    {"low_cohesion", "brain_method", "primitive_obsession", "dry_violation", "error_handling"}
)


# ---------------------------------------------------------------------------
# Performance dimension (PR3). Shipped at a small, ADVISORY weight - the whole
# pillar is bounded by a single 1.0 category cap, so even a file riddled with
# perf hits loses at most one health point on this dimension. Promotion to a
# co-equal weight waits on PR4's cross-function precision study.
# ---------------------------------------------------------------------------

# Per-biomarker weight on the performance dimension. ``io_in_loop`` is the
# gate-cleared core (measured 79% precision, Phase 0) and carries full weight;
# ``string_concat_in_loop`` and ``blocking_sync_in_async`` ride the same pass
# at a reduced advisory weight pending their own spot-check (see
# MARKER_BACKLOG.md). Unknown biomarkers fall back to 1.0.
_PERFORMANCE_WEIGHT_MULTIPLIER: dict[str, float] = {
    "io_in_loop": 1.0,
    "blocking_sync_in_async": 0.7,
    "string_concat_in_loop": 0.5,
    # Phase 7a markers. Ship at advisory weight pending each one's Phase-0 gate
    # (MARKER_BACKLOG.md); promote to full weight where corpus precision >= 70%.
    # resource_construction is the highest-confidence (classified constructor),
    # serial_await the lowest (cannot prove iteration independence).
    "resource_construction_in_loop": 0.7,
    "lock_in_loop": 0.5,
    # PROMOTED 0.5 -> 0.7 (Phase-7c): 100% precision across corpora (7a 22/22 +
    # headroom Python 12/12 = 34/34; list-vs-set gate holds). Clears the 70% bar.
    "membership_test_against_list_in_loop": 0.7,
    "serial_await_in_loop": 0.4,
    # Phase 7b markers. Ship at advisory weight pending each one's Phase-0 gate
    # (MARKER_BACKLOG.md / PHASE7B_LABELS.md). nested_loop_with_io rides with
    # io_in_loop (nesting-confident); blocking_io_under_lock is high-confidence
    # by construction (a sink under a held lock); the centrality-gated pair are
    # advisory (the gate is precision, but the algorithmic cost is context-bound).
    "nested_loop_with_io": 0.5,
    "blocking_io_under_lock": 0.6,
    "hot_path_sync_io": 0.5,
    "nested_loop_quadratic": 0.4,
}

# All perf biomarkers share one ``performance`` category, so the single cap
# below bounds the entire dimension's deduction.
_PERFORMANCE_CATEGORY: dict[str, str] = {
    "io_in_loop": "performance",
    "string_concat_in_loop": "performance",
    "blocking_sync_in_async": "performance",
    "resource_construction_in_loop": "performance",
    "lock_in_loop": "performance",
    "serial_await_in_loop": "performance",
    "membership_test_against_list_in_loop": "performance",
    "nested_loop_with_io": "performance",
    "nested_loop_quadratic": "performance",
    "hot_path_sync_io": "performance",
    "blocking_io_under_lock": "performance",
}

# One bounded performance category cap. ~1.0 keeps performance advisory.
_PERFORMANCE_CATEGORY_CAPS: dict[str, float] = {
    "performance": 1.0,
}

# Perf biomarkers home to ``performance`` for display / per-pillar filtering.
_PERFORMANCE_HOME: frozenset[str] = frozenset(
    {
        "io_in_loop",
        "string_concat_in_loop",
        "blocking_sync_in_async",
        "resource_construction_in_loop",
        "lock_in_loop",
        "serial_await_in_loop",
        "membership_test_against_list_in_loop",
        "nested_loop_with_io",
        "nested_loop_quadratic",
        "hot_path_sync_io",
        "blocking_io_under_lock",
    }
)


def severity_deduction(sev: Severity) -> float:
    return _SEVERITY_DEDUCTION.get(sev, 0.5)


def biomarker_weight(name: str) -> float:
    """Per-biomarker multiplier; 1.0 for unknown biomarkers."""
    return _BIOMARKER_WEIGHT_MULTIPLIER.get(name, 1.0)


def biomarker_category(name: str) -> str:
    """Default to ``size_and_complexity`` for unknown biomarkers."""
    return _BIOMARKER_CATEGORY.get(name, "size_and_complexity")


def dimensions_for(name: str) -> set[str]:
    """Dimensions a biomarker's deduction contributes to.

    Unlisted biomarkers default to ``{"defect"}`` (the historical single score).
    Performance-only detectors map to ``{"performance"}`` and are deliberately
    excluded from ``defect`` - that exclusion is what keeps the defect golden
    guarantee intact.
    """
    return _BIOMARKER_DIMENSIONS.get(name, {"defect"})


def biomarker_dimension(name: str) -> str:
    """The finding's single 'home' dimension for display / per-pillar filtering."""
    if name in _PERFORMANCE_HOME:
        return "performance"
    if name in _MAINTAINABILITY_HOME:
        return "maintainability"
    return "defect"


def maintainability_weight(name: str) -> float:
    """Maintainability multiplier; 1.0 for unknown biomarkers."""
    return _MAINTAINABILITY_WEIGHT_MULTIPLIER.get(name, 1.0)


def maintainability_category(name: str) -> str:
    """Default to ``size_and_complexity`` for unknown biomarkers."""
    return _MAINTAINABILITY_CATEGORY.get(name, "size_and_complexity")


def performance_weight(name: str) -> float:
    """Performance multiplier; 1.0 for unknown biomarkers."""
    return _PERFORMANCE_WEIGHT_MULTIPLIER.get(name, 1.0)


def performance_category(name: str) -> str:
    """Default to ``performance`` for unknown biomarkers (single capped category)."""
    return _PERFORMANCE_CATEGORY.get(name, "performance")


def _score_dimension(
    results_list: list[BiomarkerResult],
    weight_fn: Callable[[str], float],
    category_fn: Callable[[str], str],
    caps: dict[str, float],
) -> tuple[float, list[float]]:
    """Aggregate one dimension's deductions -> ``(score, per_result_deductions)``.

    The single, shared scoring kernel: weight each finding, accumulate per
    category, cap each category, clamp to ``[1.0, 10.0]``. Every dimension runs
    the identical algorithm against its own weight / category / cap tables.
    """
    raw: dict[str, list[tuple[int, float]]] = {}
    for idx, r in enumerate(results_list):
        cat = category_fn(r.biomarker_type)
        # A continuous ``deduction`` override (e.g. coverage scaled by the
        # uncovered fraction) takes the place of the discrete severity table;
        # both paths are then weighted and category-capped identically, so the
        # per-finding ``health_impact`` stays linear and attributable.
        base = r.deduction if r.deduction is not None else severity_deduction(r.severity)
        weighted = base * weight_fn(r.biomarker_type)
        raw.setdefault(cat, []).append((idx, weighted))

    per_result = [0.0] * len(results_list)
    total = 0.0
    for cat, entries in raw.items():
        cap = caps.get(cat, 1.0)
        cat_sum = sum(d for _, d in entries)
        if cat_sum <= cap:
            for idx, d in entries:
                per_result[idx] = d
            total += cat_sum
        else:
            # Scale down proportionally so the cap is respected.
            scale = cap / cat_sum if cat_sum > 0 else 0.0
            for idx, d in entries:
                per_result[idx] = d * scale
            total += cap

    score = max(1.0, min(10.0, 10.0 - total))
    return score, per_result


def score_file(results: Iterable[BiomarkerResult]) -> tuple[dict[str, float | None], list[float]]:
    """Aggregate biomarker hits into per-dimension scores in ``[1.0, 10.0]``.

    Returns ``(scores, defect_deductions)`` where:

    - ``scores`` maps each dimension in ``DIMENSIONS`` to its score.
      ``scores["defect"]`` is the historical single score - byte-for-byte
      identical to the pre-split ``score_file`` (the load-bearing guarantee).
      ``scores["performance"]`` is now measured (PR3): a file with no perf
      findings scores 10.0; the perf detectors deduct under a single bounded
      ``performance`` cap. It is still capped low (advisory) and never blends
      into ``defect``.
    - ``defect_deductions`` is each finding's contribution to the DEFECT score
      after category capping, parallel to *results*. It populates
      ``HealthFindingData.health_impact`` - a defect-pillar quantity - so the
      surfaced per-finding impact numbers are unchanged.
    """
    results_list = list(results)

    # DEFECT: only biomarkers whose dimensions include ``defect`` deduct. EVERY
    # historical biomarker does (``dimensions_for`` defaults to ``{"defect"}``),
    # so this reproduces the pre-split single score byte-for-byte; the new
    # performance-only detectors are excluded and never move the surfaced score.
    # ``defect_deductions`` stays parallel to the FULL ``results_list`` (perf
    # findings get 0.0 defect impact) so ``attach_impacts`` can zip 1:1.
    defect_idx = [
        i for i, r in enumerate(results_list) if "defect" in dimensions_for(r.biomarker_type)
    ]
    defect_results = [results_list[i] for i in defect_idx]
    defect_score, defect_sub = _score_dimension(
        defect_results, biomarker_weight, biomarker_category, CATEGORY_CAPS
    )
    defect_deductions = [0.0] * len(results_list)
    for sub_i, orig_i in enumerate(defect_idx):
        defect_deductions[orig_i] = defect_sub[sub_i]

    maint_results = [
        r for r in results_list if "maintainability" in dimensions_for(r.biomarker_type)
    ]
    maint_score, _ = _score_dimension(
        maint_results,
        maintainability_weight,
        maintainability_category,
        _MAINTAINABILITY_CATEGORY_CAPS,
    )

    # PERFORMANCE: now that the detectors are registered, every file is measured
    # - a clean file scores 10.0 (no perf findings), not ``None``.
    perf_results = [r for r in results_list if "performance" in dimensions_for(r.biomarker_type)]
    perf_score, _ = _score_dimension(
        perf_results,
        performance_weight,
        performance_category,
        _PERFORMANCE_CATEGORY_CAPS,
    )

    scores: dict[str, float | None] = {
        "defect": defect_score,
        "maintainability": maint_score,
        "performance": perf_score,
    }
    return scores, defect_deductions


def attach_impacts(
    results: list[BiomarkerResult], deductions: list[float]
) -> list[HealthFindingData]:
    """Lift ``BiomarkerResult`` -> ``HealthFindingData`` with impact attached."""
    out: list[HealthFindingData] = []
    for r, d in zip(results, deductions, strict=True):
        out.append(
            HealthFindingData(
                biomarker_type=r.biomarker_type,
                severity=r.severity,
                file_path="",  # filled by engine
                function_name=r.function_name,
                line_start=r.line_start,
                line_end=r.line_end,
                details=r.details,
                health_impact=round(d, 3),
                reason=r.reason,
                dimension=biomarker_dimension(r.biomarker_type),
            )
        )
    return out


def _wavg_attr(rows: list[HealthFileMetricData], attr: str) -> float | None:
    """NLOC-weighted average of one metric attribute, skipping ``None`` values.

    Returns ``None`` when no row carries the attribute (e.g. a repo whose
    ``maintainability_score`` predates the split) so the KPI reads "not measured"
    rather than a misleading perfect 10.0.
    """
    scored = [r for r in rows if getattr(r, attr, None) is not None]
    if not scored:
        return None
    total_w = sum(max(r.nloc, 1) for r in scored)
    if total_w == 0:
        return sum(getattr(r, attr) for r in scored) / len(scored)
    return sum(getattr(r, attr) * max(r.nloc, 1) for r in scored) / total_w


def compute_kpis(
    metrics: list[HealthFileMetricData],
    hotspot_paths: set[str],
) -> dict[str, object]:
    """Repo-level KPIs derived from per-file metrics.

    - ``hotspot_health``: NLOC-weighted average over files in *hotspot_paths*.
    - ``average_health``: NLOC-weighted average over all files.
    - ``worst_performer``: lowest-scoring file + score.
    - ``maintainability_average`` / ``maintainability_hotspot``: the same two
      NLOC-weighted averages over the per-file ``maintainability_score``, so the
      maintainability pillar surfaces a repo headline alongside the defect one.
      ``None`` when no file carries a maintainability score.
    - ``performance_average`` / ``performance_hotspot``: the same two
      NLOC-weighted averages over the per-file ``performance_score`` (static
      performance RISK), so the performance pillar surfaces a repo headline too.
      ``None`` when no file carries a performance score.
    """
    if not metrics:
        return {
            "hotspot_health": 10.0,
            "average_health": 10.0,
            "worst_performer_path": None,
            "worst_performer_score": None,
            "file_count": 0,
            "maintainability_average": None,
            "maintainability_hotspot": None,
            "performance_average": None,
            "performance_hotspot": None,
        }

    def _wavg(rows: list[HealthFileMetricData]) -> float:
        if not rows:
            return 10.0
        total_w = sum(max(r.nloc, 1) for r in rows)
        if total_w == 0:
            return sum(r.score for r in rows) / len(rows)
        return sum(r.score * max(r.nloc, 1) for r in rows) / total_w

    hotspots = [m for m in metrics if m.file_path in hotspot_paths]
    worst = min(metrics, key=lambda m: m.score)
    maint_avg = _wavg_attr(metrics, "maintainability_score")
    maint_hotspot = _wavg_attr(hotspots, "maintainability_score")
    perf_avg = _wavg_attr(metrics, "performance_score")
    perf_hotspot = _wavg_attr(hotspots, "performance_score")
    return {
        "hotspot_health": round(_wavg(hotspots), 2),
        "average_health": round(_wavg(metrics), 2),
        "worst_performer_path": worst.file_path,
        "worst_performer_score": round(worst.score, 2),
        "file_count": len(metrics),
        "maintainability_average": round(maint_avg, 2) if maint_avg is not None else None,
        "maintainability_hotspot": round(maint_hotspot, 2) if maint_hotspot is not None else None,
        "performance_average": round(perf_avg, 2) if perf_avg is not None else None,
        "performance_hotspot": round(perf_hotspot, 2) if perf_hotspot is not None else None,
    }
