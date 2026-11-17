"""The decision lanes a keyless index skipped, as ``generate`` and ``update --full`` run them.

Every helper takes ``None`` for "nothing pending" so a caller needs no branch of
its own around the lanes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from repowise.cli.helpers import console
from repowise.core.decision_lanes import (
    DecisionLaneOutcome,
    DecisionLanePlan,
    lanes_label,
    run_decision_lanes,
)

logger = structlog.get_logger(__name__)


def with_lane_plan(cost_plans: list[Any], plan: DecisionLanePlan | None) -> list[Any]:
    """*cost_plans* plus the decision calls, so one estimate prices both."""
    return [*cost_plans, plan.cost_plan()] if plan else list(cost_plans)


def lane_estimate(plan: DecisionLanePlan, provider: Any) -> Any:
    """The plan's estimate, priced for *provider* (unpriced when it is ``None``)."""
    from repowise.core.cost_estimator import estimate_cost

    if provider is None:
        return estimate_cost([plan.cost_plan()], "none", "")
    return estimate_cost([plan.cost_plan()], provider.provider_name, provider.model_name)


def print_lane_estimate(plan: DecisionLanePlan | None, provider: Any) -> None:
    """``Decisions from PRs, history and comments: about N tokens, $X.``"""
    if plan is None:
        return
    est = lane_estimate(plan, provider)
    tokens = est.estimated_input_tokens + est.estimated_output_tokens
    # Unpriced without a model; the caller says so once for the whole run.
    price = "" if provider is None else f", ${est.estimated_cost_usd:.2f}"
    calls = f"{plan.calls} model call{'s' if plan.calls != 1 else ''}"
    console.print(
        f"Decisions from {lanes_label(plan.lanes)}: about [bold]{tokens:,}[/bold] tokens "
        f"({calls}){price}."
    )


def still_pending_after(outcome: DecisionLaneOutcome | None) -> tuple[str, ...] | None:
    """Lanes left after a run, or ``None`` when no lanes ran (leave state alone)."""
    return outcome.still_pending if outcome is not None else None


async def run_and_report_lanes(
    session_factory: Any,
    repo_id: str,
    repo_path: Path,
    plan: DecisionLanePlan | None,
    *,
    provider: Any,
    inputs: Any,
    vector_store: Any = None,
    cost_tracker: Any = None,
) -> DecisionLaneOutcome | None:
    """Run the planned lanes on *provider*'s tracker and print what they found.

    ``None`` when nothing was planned.
    """
    if plan is None:
        return None
    if cost_tracker is not None:
        provider._cost_tracker = cost_tracker
    label = lanes_label(plan.lanes)
    console.print(f"Reading decisions from {label}...")
    try:
        outcome = await run_decision_lanes(
            session_factory,
            repo_id,
            repo_path,
            plan,
            provider=provider,
            inputs=inputs,
            vector_store=vector_store,
        )
    except Exception as exc:
        # Broad on purpose: the pages are already written, so any store or model
        # failure here must leave the lanes pending, not lose the run's state write.
        logger.warning("decision_lanes_failed", error=str(exc), exc_info=True)
        console.print(f"[yellow]Decision extraction failed ({exc}).[/yellow]")
        outcome = DecisionLaneOutcome(found=0, still_pending=plan.lanes)
    if cost_tracker is not None:
        await cost_tracker.flush()
    console.print(f"Decisions from {label}: [bold]{outcome.found}[/bold] found.")
    if outcome.still_pending:
        console.print(
            f"[yellow]Decisions from {lanes_label(outcome.still_pending)} failed.[/yellow] "
            "The next model run retries them."
        )
    return outcome
