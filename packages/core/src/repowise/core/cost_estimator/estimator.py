"""Cost orchestration — combines plans + heuristics/telemetry + pricing."""

from __future__ import annotations

from pathlib import Path

from .calibration import load_telemetry_averages
from .heuristics import HEURISTIC_VARIANCE, heuristic_tokens
from .pricing import _lookup_cost
from .types import CostEstimate, CostRange, PageTypePlan


def _tokens_per_page(
    page_type: str,
    *,
    telemetry: dict[str, tuple[float, float]],
) -> tuple[float, float]:
    """Return ``(input, output)`` tokens for *page_type*.

    Prefers telemetry averages when available — those reflect *this
    repo's* actual prompt sizes. Falls back to heuristics for fresh
    repos and unknown page types.
    """
    if page_type in telemetry:
        return telemetry[page_type]
    return heuristic_tokens(page_type)


def estimate_cost(
    plans: list[PageTypePlan],
    provider_name: str,
    model_name: str,
    *,
    repo_path: Path | str | None = None,
) -> CostEstimate:
    """Estimate token counts and USD cost from a generation plan.

    When ``repo_path`` points to a repo with prior generation telemetry
    in ``.repowise/db.sqlite``, the estimate is calibrated against the
    actual averages from that repo. Otherwise the static heuristics in
    :mod:`heuristics` are used and a wider variance bracket is reported.
    """
    telemetry: dict[str, tuple[float, float]] = {}
    if repo_path is not None:
        telemetry = load_telemetry_averages(repo_path)
    is_calibrated = bool(telemetry)

    total_pages = sum(p.count for p in plans)
    total_input = 0.0
    total_output = 0.0

    for plan in plans:
        inp, out = _tokens_per_page(plan.page_type, telemetry=telemetry)
        total_input += inp * plan.count
        total_output += out * plan.count

    input_rate, output_rate = _lookup_cost(model_name)
    median_cost = (total_input / 1000) * input_rate + (total_output / 1000) * output_rate

    # Tighter variance when telemetry calibrated us; wider for cold-start.
    variance = 0.10 if is_calibrated else HEURISTIC_VARIANCE
    cost_range = CostRange(
        low=median_cost * (1.0 - variance),
        median=median_cost,
        high=median_cost * (1.0 + variance),
    )

    return CostEstimate(
        plans=plans,
        total_pages=total_pages,
        estimated_input_tokens=int(total_input),
        estimated_output_tokens=int(total_output),
        estimated_cost_usd=median_cost,
        provider_name=provider_name,
        model_name=model_name,
        cost_range=cost_range,
        is_calibrated=is_calibrated,
    )
