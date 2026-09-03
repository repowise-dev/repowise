"""Public interpretation helpers for health output."""

from __future__ import annotations

from typing import Any


def weighted_deficit_contract() -> dict[str, Any]:
    """Describe the uncalibrated weighted-deficit unit without changing it."""
    return {
        "unit": "health_score_points_x_nloc",
        "numerator": "max(8.0 - file_score, 0.0) * max(nloc, 1)",
        "denominator": "gap_analysis.weighted_gross_gap_points",
        "scale": {"minimum": 0, "maximum": None, "normalized": False},
        "direction": (
            "higher means the file contributes more to the eligible population's "
            "gross deficit from the Healthy threshold"
        ),
        "interpretation": (
            "a deterministic triage weight, not a probability, percentage, "
            "normalized score, or guaranteed improvement"
        ),
    }


def percentile_contract() -> dict[str, Any]:
    """Describe the common stored and public health-percentile scales."""
    return {
        "stored_scale": "ratio_0_to_1",
        "public_raw_scale": "percentile_rank_0_to_100",
        "direction": "higher means ranked above more files in the eligible stored population",
        "population": (
            "the eligible repository files used by the stored analysis for that signal"
        ),
        "freshness": "stored analysis; not recomputed by get_health",
    }


def format_top_percentile(percentile: float, population: str) -> str:
    """Render a stored 0-1 rank without ever claiming ``top 0%``."""
    bounded = min(max(float(percentile), 0.0), 1.0)
    upper_tail = (1.0 - bounded) * 100.0
    if upper_tail < 0.1:
        rank = "top <0.1%"
    elif upper_tail < 10.0:
        rendered = f"{upper_tail:.1f}".rstrip("0").rstrip(".")
        rank = f"top {rendered}%"
    else:
        rank = f"top {upper_tail:.0f}%"
    return f"{rank} among {population}"


def health_semantics_contract() -> dict[str, Any]:
    """Compact shared legend retained by every health recipe."""
    return {
        "weighted_deficit_points": weighted_deficit_contract(),
        "percentiles": percentile_contract(),
    }


__all__ = [
    "format_top_percentile",
    "health_semantics_contract",
    "percentile_contract",
    "weighted_deficit_contract",
]
