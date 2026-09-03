"""How much a composed opportunity is worth, and in what order they land.

Plan-level rank (:mod:`.recommendations`) answers "which single detector output
is worth the most". This module answers a different question over a different
unit: given everything one file needs done, how does that file compare to the
next one. The two are kept apart because merging them is what let a file's
popularity stand in for a plan's value, and because a surface still needs the
plan order for compatibility.

Four things move an opportunity up, and they are the four the product promised:

- it addresses the file's own primary diagnosed problem, rather than something
  incidental that happens to live there;
- it recovers real health, summed over its steps, or carries a detector-native
  structural gain where no health deduction exists;
- a larger share of its steps is mechanical, so more of it can be handed off;
- it costs less and risks less - with the blast radius charged exactly once,
  over the union of what the steps touch, never per step.

The arithmetic that both ranks share - the benefit-over-cost shape and the
surface/confidence risk - lives in :mod:`.recommendations` with the plan rank;
what differs, and belongs here, is what counts as benefit and what counts as
uplift for a whole step set.

Nothing here is a health score, and no value it produces is blended into one.
It is an ordering key whose weights are frozen policy. The performance layer's
``perf/opportunity_rank.py`` is the same role for a different domain and shares
no code with this one.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .models import CONFIDENCE_LEVELS, RefactoringSuggestion
from .recommendations import EFFORT_COST, detector_native_benefit, priority_score
from .recommendations import surface_confidence_risk as _surface_confidence_risk

# Weight of the two qualities that separate opportunities of equal size: doing
# the thing the file is actually sick with, and being handed off rather than
# reasoned about. Deliberately of the same order as each other, and both
# smaller than the benefit they multiply.
PRIMARY_PROBLEM_WEIGHT = 0.5
MECHANICAL_SHARE_WEIGHT = 0.5

# Worst first, off the package's one confidence vocabulary rather than a
# second hand-written tuple that could drift from it.
_WORST_FIRST = tuple(reversed(CONFIDENCE_LEVELS))


def weakest_confidence(steps: Sequence[RefactoringSuggestion]) -> str:
    """The least certain step's confidence: a set is only as sound as its worst.

    Always one of :data:`.models.CONFIDENCE_LEVELS`. A label no detector should
    emit - a legacy row, a corrupted string - is reported as the weakest known
    level rather than passed through: the risk table has no entry for it, and
    its fallback sits *below* ``low``, so letting it through would have made
    adding an uninterpretable step reduce an opportunity's risk.
    """
    weakest = _WORST_FIRST[0]
    if not steps:
        return weakest
    return max(
        (step.confidence if step.confidence in _WORST_FIRST else weakest for step in steps),
        key=_WORST_FIRST.index,
    )


def rank_factors(
    *,
    benefit: float,
    addresses_primary_problem: bool,
    mechanical_share: float,
    cost: float,
    risk: float,
) -> dict[str, float]:
    """The published components of the score, each rounded once, here."""
    return {
        "benefit": round(benefit, 4),
        "primary_problem": round(PRIMARY_PROBLEM_WEIGHT if addresses_primary_problem else 0.0, 4),
        "mechanical_share": round(MECHANICAL_SHARE_WEIGHT * mechanical_share, 4),
        "cost": round(cost, 4),
        "risk": round(risk, 4),
    }


def rank_score(factors: dict[str, float]) -> float:
    """Benefit scaled by what makes it attractive, divided by what it costs.

    Benefit multiplies rather than offsets, exactly as at plan level: a step set
    with no evidence of a gain scores zero and cannot be lifted above one that
    recovers health by being mechanical or by sitting on a popular file.
    """
    return round(
        priority_score(
            benefit=factors["benefit"],
            uplift=factors["primary_problem"] + factors["mechanical_share"],
            cost=factors["cost"],
            risk=factors["risk"],
        ),
        4,
    )


def step_cost(steps: Sequence[RefactoringSuggestion]) -> float:
    """Total work. Doing five things is more work than doing one."""
    return sum(EFFORT_COST.get(step.effort_bucket, 3.0) for step in steps)


def opportunity_risk(steps: Sequence[RefactoringSuggestion], *, surface: int) -> float:
    """Blast radius once, over the union *surface*, plus the weakest confidence.

    *surface* is the count of files the whole set touches beyond the host, which
    the composer already computed as a union. Charging it per step would bill a
    file once for every plan that mentions it, which is the double-count R1
    removed one layer down.
    """
    return _surface_confidence_risk(surface, weakest_confidence(steps))


def opportunity_benefit(steps: Sequence[RefactoringSuggestion]) -> float:
    """Recoverable health across the set, or the detector-native gain instead."""
    return sum(detector_native_benefit(step) for step in steps)


def why_ranked(factors: dict[str, float], *, limit: int = 3) -> list[dict[str, Any]]:
    """The factors that actually moved this one, largest first. Structured."""
    ordered = sorted(
        ((name, value) for name, value in factors.items() if value),
        key=lambda item: (-abs(item[1]), item[0]),
    )
    return [{"factor": name, "value": value} for name, value in ordered[:limit]]


def rank_sort_key(opportunity: Any) -> tuple[float, str, str]:
    """A total order. The id tail is what stops ties floating between runs."""
    return (-opportunity.rank_score, opportunity.file_path, opportunity.opportunity_id)


__all__ = [
    "MECHANICAL_SHARE_WEIGHT",
    "PRIMARY_PROBLEM_WEIGHT",
    "opportunity_benefit",
    "opportunity_risk",
    "rank_factors",
    "rank_score",
    "rank_sort_key",
    "step_cost",
    "weakest_confidence",
    "why_ranked",
]
