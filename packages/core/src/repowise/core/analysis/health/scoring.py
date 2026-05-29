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

from collections.abc import Iterable

from .biomarkers.base import BiomarkerResult
from .models import HealthFileMetricData, HealthFindingData, Severity

# Per-category max deduction.
CATEGORY_CAPS: dict[str, float] = {
    "organizational": 3.5,
    "structural_complexity": 2.5,
    "test_coverage": 2.0,
    "size_and_complexity": 1.5,
    "duplication": 1.0,
    # Test-quality smells are mild, advisory signals — a small cap keeps a
    # noisy test file from dominating its own health score.
    "test_quality": 0.5,
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
# file scored at the pre-window commit T0 (no HEAD→window leakage), then an
# L2-regularized logistic regression of "received a bug-fix in (T0,T1]" on the
# per-biomarker hits PLUS an explicit NLOC control column — so each weight
# reflects the biomarker's defect lift *beyond file size*. Cross-project
# (leave-one-repo-out) pooled OOF AUC ≈ 0.70. The fit is reproduced by
# `local-stash/calibrate_health_weights.py`; the runtime stays pure-
# deterministic / zero-LLM — only these learned constants ship.
#
# Mapping policy ("balanced"): positive, well-measured predictors are scaled
# into [1.0, 1.8] ∝ coefficient; biomarkers that fired widely but were weak/
# non-predictive at T0 are floored to 0.5 (kept as mild maintainability/parity
# signals, not disabled); biomarkers the benchmark could NOT measure (no
# coverage ingested → untested_hotspot/coverage_gap; test-only assertion smells;
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
    # --- kept at prior: benchmark could not fairly measure these ---
    "untested_hotspot": 1.3,  # benchmark ingests no coverage (has_test_file fallback only)
    "churn_risk": 1.2,  # fired in too few repos to calibrate
    "code_age_volatility": 1.1,  # gate unmet at T0 across the corpus
    # --- floored: fired widely but weak / non-predictive at T0 ---
    "developer_congestion": 0.5,  # was 1.5 — the HEAD-leakage hero; weak under T0
    "low_cohesion": 0.5,
    "brain_method": 0.5,
    "bumpy_road": 0.5,
    "primitive_obsession": 0.5,
    "dry_violation": 0.5,
    "knowledge_loss": 0.4,  # confirmed weak-negative since Phase 1
    # (coverage_gap, hidden_coupling, large_assertion_block,
    #  duplicated_assertion_block default to 1.0 — kept at prior)
    # Governance — additive pass, weights are informational
    "contradictory_decision": 1.0,
    "stale_governance": 0.9,
    "ungoverned_hotspot": 0.7,
}

# Map biomarker name → category. Kept here (single source of truth)
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
    "developer_congestion": "organizational",
    "knowledge_loss": "organizational",
    "hidden_coupling": "organizational",
    "function_hotspot": "organizational",
    "code_age_volatility": "organizational",
    "ownership_risk": "organizational",
    "churn_risk": "organizational",
    "change_entropy": "organizational",
    "co_change_scatter": "organizational",
    "large_assertion_block": "test_quality",
    "duplicated_assertion_block": "test_quality",
    # Governance biomarkers — written by the additive governance pass
    "ungoverned_hotspot": "organizational",
    "stale_governance": "organizational",
    "contradictory_decision": "organizational",
}


def severity_deduction(sev: Severity) -> float:
    return _SEVERITY_DEDUCTION.get(sev, 0.5)


def biomarker_weight(name: str) -> float:
    """Per-biomarker multiplier; 1.0 for unknown biomarkers."""
    return _BIOMARKER_WEIGHT_MULTIPLIER.get(name, 1.0)


def biomarker_category(name: str) -> str:
    """Default to ``size_and_complexity`` for unknown biomarkers."""
    return _BIOMARKER_CATEGORY.get(name, "size_and_complexity")


def score_file(results: Iterable[BiomarkerResult]) -> tuple[float, list[float]]:
    """Aggregate biomarker hits → final score in [1.0, 10.0].

    Returns ``(score, per_result_deductions)`` where the deductions list
    is parallel to *results* and represents each finding's contribution
    AFTER category capping. Use it to populate
    ``HealthFindingData.health_impact`` so the UI can show per-finding
    impact.
    """
    results_list = list(results)
    raw: dict[str, list[tuple[int, float]]] = {}
    for idx, r in enumerate(results_list):
        cat = biomarker_category(r.biomarker_type)
        weighted = severity_deduction(r.severity) * biomarker_weight(r.biomarker_type)
        raw.setdefault(cat, []).append((idx, weighted))

    per_result = [0.0] * len(results_list)
    total = 0.0
    for cat, entries in raw.items():
        cap = CATEGORY_CAPS.get(cat, 1.0)
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


def attach_impacts(
    results: list[BiomarkerResult], deductions: list[float]
) -> list[HealthFindingData]:
    """Lift ``BiomarkerResult`` → ``HealthFindingData`` with impact attached."""
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
            )
        )
    return out


def compute_kpis(
    metrics: list[HealthFileMetricData],
    hotspot_paths: set[str],
) -> dict[str, object]:
    """Repo-level KPIs derived from per-file metrics.

    - ``hotspot_health``: NLOC-weighted average over files in *hotspot_paths*.
    - ``average_health``: NLOC-weighted average over all files.
    - ``worst_performer``: lowest-scoring file + score.
    """
    if not metrics:
        return {
            "hotspot_health": 10.0,
            "average_health": 10.0,
            "worst_performer_path": None,
            "worst_performer_score": None,
            "file_count": 0,
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
    return {
        "hotspot_health": round(_wavg(hotspots), 2),
        "average_health": round(_wavg(metrics), 2),
        "worst_performer_path": worst.file_path,
        "worst_performer_score": round(worst.score, 2),
        "file_count": len(metrics),
    }
