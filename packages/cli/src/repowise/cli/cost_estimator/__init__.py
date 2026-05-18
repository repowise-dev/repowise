"""Cost estimation for the repowise generation pipeline.

Public API kept stable for existing callers:

- :func:`build_generation_plan` — given parsed_files + graph + config,
  returns a list of :class:`PageTypePlan`. Internally delegates to
  :func:`repowise.core.generation.select_pages` so the estimator and
  the actual generator never disagree.
- :func:`estimate_cost` — given plans + model, returns a
  :class:`CostEstimate` with a median + low/high range. Optionally
  calibrated against ``.repowise/db.sqlite`` telemetry.
- :func:`_lookup_cost` — retained for the provider unit tests.
"""

from .coverage import (
    DEFAULT_COVERAGE_OPTIONS,
    RECOMMENDED_COVERAGE,
    CoverageOption,
    compute_coverage_options,
)
from .estimator import estimate_cost
from .plans import build_generation_plan
from .pricing import _lookup_cost
from .types import CostEstimate, CostRange, PageTypePlan

__all__ = [
    "CostEstimate",
    "CostRange",
    "CoverageOption",
    "DEFAULT_COVERAGE_OPTIONS",
    "PageTypePlan",
    "RECOMMENDED_COVERAGE",
    "_lookup_cost",
    "build_generation_plan",
    "compute_coverage_options",
    "estimate_cost",
]
