"""How much a unit of work is worth, and the tier it earns.

The value band, 0 to :data:`VALUE_MAX`, is code shape plus reach: a file in the
top fifth by importers adds one step, which never lifts a unit into a higher
tier (:func:`tier` reads the value without it). Inside a band, :func:`worth`
orders by complexity removed times reach; history (a file that changes often)
only re-orders units there.
"""

from __future__ import annotations

from collections.abc import Mapping
from math import log2
from typing import Any

from ...execution_roles import COLD_ROLES, LEADING_ROLES
from ..rows import field
from ..worth import LOW_PRIORITY_LABEL, SIZE_CCN, execution_role, lead_reason, magnitude
from .order import LEVEL_RANK

#: Credited health gain cut points for value 1, 2 and 3.
GAIN_CUTS = (0.5, 1.5, 3.0)
#: A critical finding or a brain method is at least this size. Problem size
#: (:func:`size_value`) reads ``worth.SIZE_*`` because health credit is
#: calibrated per finding and saturates: a function far past every bar would
#: rank with a tidy one on gain alone.
SIZE_SEVERE = 2
#: The highest value any unit reaches.
VALUE_MAX = 4
#: One health point recoverable counts as this much complexity removed (the
#: first CCN bar), so a plan with no measured CCN removal still has a worth.
GAIN_CCN = SIZE_CCN[0]
#: A performance cause counts as a function at the worth bar (CCN 40), times
#: the reach of the call sites one fix settles.
PERF_CCN = SIZE_CCN[1]
#: On a file in the top fifth by churn. History re-orders inside a band only.
CHURN_WEIGHT = 1.25


def gain_value(gain: float, central: bool) -> int:
    return min(3, sum(gain >= cut for cut in GAIN_CUTS) + int(central))


def size_value(shape: Mapping[str, int], central: bool) -> int:
    """How big the problem is, 0 to 4; a widely imported file counts one more."""
    base = max(magnitude(shape), SIZE_SEVERE if shape.get("severe") else 0)
    return min(VALUE_MAX, base + int(central and base > 0))


def shape_value(gain: float, shape: Mapping[str, int], cloned: bool, *, central: bool) -> int:
    """A refactoring's or a finding's value: the larger of the gain and the
    problem size, one step more when a duplicate sits in the same function:
    raters accepted that shape almost every time."""
    value = max(gain_value(gain, central), size_value(shape, central))
    return min(VALUE_MAX, value + 1) if cloned else value


def removed(ccn: int, gain: float) -> float:
    """Complexity a unit removes: the CCN it takes out, or its health gain
    counted at :data:`GAIN_CCN` per point, whichever is larger."""
    return max(float(ccn), gain * GAIN_CCN)


def reach(dependents: int | None) -> float:
    """How far a change reaches: 1 plus log2 of one more than its importers."""
    return 1 + log2(1 + max(dependents or 0, 0))


def worth(removed_ccn: float, dependents: int | None, churning: bool) -> float:
    """The order inside a value band: complexity removed times :func:`reach`,
    :data:`CHURN_WEIGHT` more on a file that changes often. Unbounded on
    purpose: the band saturates, so a CCN 963 loop and a CCN 150 wizard share
    it, and only this tells them apart."""
    return removed_ccn * reach(dependents) * (CHURN_WEIGHT if churning else 1.0)


#: The band a production database or network call in a loop that grows with
#: the data earns, by the role running it. Once per request or message is the
#: costliest work Fix first holds, so it shares the top band with the largest
#: functions; a scheduled job's is one step below. With no role evidence
#: (``unknown``) it is valued as any proven cause.
_ROLE_VALUE = {"scheduled_job": 3, **dict.fromkeys(LEADING_ROLES, VALUE_MAX)}


def perf_value(row: Any, facets: Mapping[str, Any]) -> int:
    """A performance cause's value, read off the role running its loop
    (``execution_roles``): request or message, then a scheduled job, then no
    evidence; a cold role (startup, CLI, UI, tooling, tests) never leads."""
    production = field(row, "execution_context") == "production"
    role = execution_role(row)
    if (
        production
        and role in _ROLE_VALUE
        and facets.get("loop_magnitude") == "grows_with_data"
        and field(row, "boundary_kind") in ("db", "network")
    ):
        return _ROLE_VALUE[role]
    reason = lead_reason(field(row, "biomarker_type"), facets)
    value = 2 if production and reason is None and role not in COLD_ROLES else 1
    # No traffic data: a loop of unknown size that no request, message or job
    # runs is most often an admin or maintenance path, where an N+1 is cheap.
    if reason == "unmeasured_cost" and role not in _ROLE_VALUE:
        value -= 1
    return value


def perf_worth(call_sites: int, churning: bool) -> float:
    """A performance cause's :func:`worth`: :data:`PERF_CCN`, reaching as far
    as the call sites one fix settles."""
    return worth(PERF_CCN, call_sites - 1, churning)


def perf_confidence(facets: Mapping[str, Any]) -> str:
    """How sure the strategy is; ``low`` when it was not recorded."""
    level = facets.get("actionability_confidence") or "low"
    return level if level in LEVEL_RANK else "low"


def perf_ready(row: Any, plan: Mapping[str, Any]) -> bool:
    """Whether a cause's fix is safe to start: a ready or proven strategy,
    or a stored plan whose every step is mechanical."""
    if field(row, "actionability_state") == "plan_ready" or field(row, "fix_safety") == "proven":
        return True
    steps = plan.get("steps") or []
    return bool(steps) and all(s.get("applicability") == "mechanical" for s in steps)


def tier(value: int, confidence: str, ready: bool, low: str | None) -> tuple[str, str]:
    """The tier and its reason, from a value that leaves the hot-file bonus
    out: history orders items within a tier but never lifts one. A problem
    the shape rule calls lower priority (``worth.low_priority``) is ``later``
    whatever its value."""
    if low is not None:
        return "later", LOW_PRIORITY_LABEL[low]
    if value >= 2 and LEVEL_RANK.get(confidence, 0) >= 1 and ready:
        return "now", "worth doing, and the plan is safe to start"
    if value >= 2:
        return "next", "worth doing; the fix needs judgment"
    return "later", "smaller payoff"


__all__ = [
    "CHURN_WEIGHT",
    "GAIN_CCN",
    "GAIN_CUTS",
    "PERF_CCN",
    "SIZE_SEVERE",
    "VALUE_MAX",
    "gain_value",
    "perf_confidence",
    "perf_ready",
    "perf_value",
    "perf_worth",
    "reach",
    "removed",
    "shape_value",
    "size_value",
    "tier",
    "worth",
]
