"""Cost estimation for the repowise generation pipeline.

Public API kept stable for existing callers:

- :func:`build_generation_plan` — given parsed_files + graph + config,
  returns a list of :class:`PageTypePlan`. Internally delegates to
  :func:`repowise.core.generation.select_pages` so the estimator and
  the actual generator never disagree.
- :func:`estimate_cost` — given plans + model, returns a
  :class:`CostEstimate` with a median + low/high range. Optionally
  calibrated against ``.repowise/db.sqlite`` telemetry.
- :func:`lookup_cost` — per-1K rates for a model, ``None`` when unpriced;
  the server prices the active model with it.
"""

from .approx import approximate_generation_plan
from .estimator import STRUCTURAL_PAGE_TYPES, estimate_cost
from .plans import build_generation_plan
from .pricing import lookup_cost
from .types import CostEstimate, CostRange, PageTypePlan

__all__ = [
    "STRUCTURAL_PAGE_TYPES",
    "CostEstimate",
    "CostRange",
    "PageTypePlan",
    "approximate_generation_plan",
    "build_generation_plan",
    "estimate_cost",
    "lookup_cost",
]
