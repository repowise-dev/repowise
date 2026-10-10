"""How much a unit of work is worth, 0 to :data:`VALUE_MAX`, and the tier it earns.

Code shape sets the value; a hot file adds one step, and history never lifts a
unit into a higher tier (:func:`tier` reads the value without that step).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..rows import field
from ..worth import LOW_PRIORITY_LABEL, lead_reason, magnitude
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


def gain_value(gain: float, hot: bool) -> int:
    return min(3, sum(gain >= cut for cut in GAIN_CUTS) + int(hot))


def size_value(shape: Mapping[str, int], hot: bool) -> int:
    """How big the problem is, 0 to 4; a hot file counts one more."""
    base = max(magnitude(shape), SIZE_SEVERE if shape.get("severe") else 0)
    return min(VALUE_MAX, base + int(hot and base > 0))


def shape_value(gain: float, shape: Mapping[str, int], cloned: bool, *, hot: bool) -> int:
    """A refactoring's or a finding's value: the larger of the gain and the
    problem size, one step more when a duplicate sits in the same function:
    raters accepted that shape almost every time."""
    value = max(gain_value(gain, hot), size_value(shape, hot))
    return min(VALUE_MAX, value + 1) if cloned else value


def perf_value(row: Any, facets: Mapping[str, Any]) -> int:
    """A performance cause's value. A production, entry-reachable database or
    network call in a loop that grows with the data is the costliest kind of
    work Fix first holds, so it shares the top band with the largest
    functions; capped below it, it never reached a top ten that size fills."""
    production = field(row, "execution_context") == "production"
    reachable = facets.get("exposure") == "entry_reachable"
    reason = lead_reason(field(row, "biomarker_type"), facets)
    if (
        production
        and reachable
        and facets.get("loop_magnitude") == "grows_with_data"
        and field(row, "boundary_kind") in ("db", "network")
    ):
        return VALUE_MAX
    value = 2 if production and reason is None else 1
    # No traffic data: a loop of unknown size that no entry point reaches is
    # most often an admin or maintenance path, where an N+1 is cheap.
    if reason == "unmeasured_cost" and not reachable:
        value -= 1
    return value


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
    "GAIN_CUTS",
    "SIZE_SEVERE",
    "VALUE_MAX",
    "gain_value",
    "perf_value",
    "shape_value",
    "size_value",
    "tier",
]
