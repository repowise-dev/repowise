"""Public dataclasses for the cost estimator."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PageTypePlan:
    """Count of pages to generate for a given page type."""

    page_type: str
    count: int
    level: int


@dataclass
class CostRange:
    """Low / median / high cost estimate.

    The median is the best single point estimate. ``low`` and ``high``
    bracket the typical variance observed in real runs (roughly ±25%
    when only heuristics are used; tighter when telemetry calibration
    is available).
    """

    low: float
    median: float
    high: float


@dataclass
class CostEstimate:
    """Estimated cost for a generation run."""

    plans: list[PageTypePlan] = field(default_factory=list)
    total_pages: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    provider_name: str = ""
    model_name: str = ""
    # Range output — median equals ``estimated_cost_usd`` for backwards
    # compatibility with existing callers.
    cost_range: CostRange | None = None
    # True when the estimate was calibrated against historical
    # ``wiki_pages`` rows for this repo (tighter than heuristics alone).
    is_calibrated: bool = False
