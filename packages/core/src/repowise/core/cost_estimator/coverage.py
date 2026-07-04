"""Coverage-option enumeration for the init UI.

The init flow asks the user to pick how thoroughly to document the
repo. This module computes the per-coverage page counts + cost
estimates that the UI renders, by calling :func:`build_generation_plan`
once per offered percentage.

Cheap — no LLM calls, just the deterministic selection function.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .estimator import estimate_cost
from .plans import build_generation_plan
from .types import CostEstimate, PageTypePlan


# Coverage percentages presented in the init UI. The 20% slot is the
# recommended default — fewer than 10% loses key concepts; more than
# 50% starts paying for diminishing-return file pages.
DEFAULT_COVERAGE_OPTIONS: tuple[float, ...] = (0.10, 0.15, 0.20, 0.30, 0.40, 0.50)
RECOMMENDED_COVERAGE: float = 0.20


@dataclass
class CoverageOption:
    """One row in the init-flow coverage table."""

    pct: float
    plans: list[PageTypePlan]
    estimate: CostEstimate
    is_recommended: bool

    def page_count_for(self, page_type: str) -> int:
        for plan in self.plans:
            if plan.page_type == page_type:
                return plan.count
        return 0


def compute_coverage_options(
    *,
    parsed_files: list[Any],
    graph_builder: Any,
    base_config: Any,
    provider_name: str,
    model_name: str,
    repo_path: Path | str | None = None,
    skip_tests: bool = False,
    skip_infra: bool = False,
    kg_modules: list[dict] | None = None,
    percentages: tuple[float, ...] = DEFAULT_COVERAGE_OPTIONS,
    recommended: float = RECOMMENDED_COVERAGE,
) -> list[CoverageOption]:
    """Return one :class:`CoverageOption` per requested percentage.

    ``base_config`` is cloned per percentage so this never mutates the
    caller's object.
    """
    from dataclasses import replace

    options: list[CoverageOption] = []
    for pct in percentages:
        cfg = replace(base_config, coverage_pct=pct, max_pages_pct=pct)
        plans = build_generation_plan(
            parsed_files, graph_builder, cfg, skip_tests, skip_infra,
            kg_modules=kg_modules,
        )
        est = estimate_cost(
            plans,
            provider_name,
            model_name,
            repo_path=repo_path,
        )
        options.append(
            CoverageOption(
                pct=pct,
                plans=plans,
                estimate=est,
                is_recommended=abs(pct - recommended) < 1e-9,
            )
        )
    return options
