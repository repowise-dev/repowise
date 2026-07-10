"""Backward-compatible re-export of :mod:`repowise.core.cost_estimator`.

The estimator moved to core so the server's pre-flight endpoint can share
the same pricing, heuristics, and calibration as the CLI cost gate. This
shim keeps the historical ``repowise.cli.cost_estimator`` import path
working for existing callers.
"""

from repowise.core.cost_estimator import (
    DEFAULT_COVERAGE_OPTIONS,
    RECOMMENDED_COVERAGE,
    CostEstimate,
    CostRange,
    CoverageOption,
    PageTypePlan,
    _lookup_cost,
    build_generation_plan,
    compute_coverage_options,
    estimate_cost,
)

__all__ = [
    "DEFAULT_COVERAGE_OPTIONS",
    "RECOMMENDED_COVERAGE",
    "CostEstimate",
    "CostRange",
    "CoverageOption",
    "PageTypePlan",
    "_lookup_cost",
    "build_generation_plan",
    "compute_coverage_options",
    "estimate_cost",
]
