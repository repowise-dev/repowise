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
}

# Per-biomarker deduction by severity. The scorer caps the per-category
# total at the value in ``CATEGORY_CAPS``.
_SEVERITY_DEDUCTION: dict[Severity, float] = {
    Severity.LOW: 0.3,
    Severity.MEDIUM: 0.7,
    Severity.HIGH: 1.2,
    Severity.CRITICAL: 2.0,
}

# Per-biomarker weight multiplier (plan §3.2 Option A). Applied to the
# severity deduction BEFORE category capping. Lets stronger empirical
# predictors deduct more without re-tuning the severity table itself.
# Unknown biomarkers fall back to 1.0.
_BIOMARKER_WEIGHT_MULTIPLIER: dict[str, float] = {
    "developer_congestion": 1.5,
    "untested_hotspot": 1.3,
    "function_hotspot": 1.2,
    "hidden_coupling": 1.0,
    "knowledge_loss": 0.4,
}

# Map biomarker name → category. Kept here (single source of truth)
# rather than on each biomarker class because some biomarkers naturally
# span categories and we may want to retune without re-deploying.
_BIOMARKER_CATEGORY: dict[str, str] = {
    "brain_method": "structural_complexity",
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
