"""Ordering policy: the magnitude facets, the weight tables, and the total order.

Nothing here is a score in the health sense. No value produced by this module
is blended into ``performance_score`` or any other dimension; it is an ordering
key whose weights are frozen policy, not fitted parameters.

This module owns every judgement about how much a group costs and how far a
change to it reaches: the marker, boundary, context, and provenance tables and
the four magnitude facets read off them. Whether a change is *safe* is the
neighbouring module's question, and the only thing this one takes from it is
the actionability state that orders actionable work first.

Every input is already carried on the group. Ranking issues no query of its
own, per opportunity or otherwise.
"""

from __future__ import annotations

from math import log2
from typing import Any

BOUNDARY_POINTS = {"subprocess": 5, "network": 4, "db": 4, "lock": 3, "filesystem": 2}
"""What one crossing of each boundary costs, as an order of magnitude.

A process spawn is milliseconds, a wire round-trip hundreds of microseconds up,
a pooled local query tens, and a filesystem call is usually a page-cache hit.
"A subprocess spawn in a loop is not a stat in a loop" is the whole point.
"""

MULTIPLIER_POINTS = {
    # Superlinear in the input.
    "nested_loop_with_io": 6,
    "nested_loop_quadratic": 6,
    "sql_cartesian_join": 6,
    # One wait every other thread queues behind.
    "blocking_io_under_lock": 5,
    # One boundary crossing per iteration, or one proven to sit on a hot path.
    "io_in_loop": 4,
    "serial_await_in_loop": 4,
    "resource_construction_in_loop": 4,
    "goroutine_in_unbounded_loop": 4,
    "hot_path_sync_io": 4,
    # In-loop CPU or allocation: real, and orders below a round-trip.
    "membership_test_against_list_in_loop": 3,
    "string_concat_in_loop": 3,
    "blocking_sync_in_async": 3,
    "pandas_iterrows_in_loop": 3,
    "pd_concat_in_loop": 3,
    "json_parse_in_loop": 3,
    "array_spread_in_reduce": 3,
    "defer_in_loop": 3,
    "regex_compile_in_loop": 3,
    "list_insert_zero_in_loop": 3,
    # Repeated acquisition with no boundary behind it.
    "lock_in_loop": 2,
}
"""How often the marker proves the cost is paid.

Exhaustive over every detector declaring the performance category, because
both the observation order and the opportunity order read it and a marker
missing from here would silently take the floor on both.
"""

UNKNOWN_MULTIPLIER_POINTS = 1
"""A detector added without a weight under-ranks rather than jumps the queue."""

CROSS_FUNCTION_POINTS = 1
"""A loop and a sink in different functions is the one nobody sees by reading
the loop, so it earns a point that an intra-function hit does not."""

CONTEXT_POINTS = {"production": 3, "tooling": 2, "test": 1, "unknown": 1}
PROVENANCE_POINTS = {"call-site": 3, "direct": 3, "reliable-edge": 2, "name-fallback": 0}

AMPLIFICATION = {
    "nested_loop_with_io": "quadratic",
    "nested_loop_quadratic": "quadratic",
    "hot_path_sync_io": "per_call",
    "blocking_sync_in_async": "per_call",
}
"""Marker to the repetition shape its evidence supports.

Everything with a loop points at the same claim, so only the exceptions are
listed and :func:`amplification` supplies the rest. A marker nobody has
characterised reports ``unknown`` rather than borrowing a neighbour's shape.
"""

ACTIONABILITY_ORDER = {"plan_ready": 0, "advisory": 1, "investigate": 2}
"""Actionable work sorts above evidence, whatever the raw magnitude.

A generic sink accumulates points through sheer volume. Left to the score
alone it buries every group somebody could act on today, which is the wrong
lead for both an agent and a person.
"""

_LEVERAGE_BANDS = ((1, "isolated"), (3, "local"), (9, "shared"))
_CHANGE_RISK_BANDS = ((1, "contained"), (4, "moderate"))


def _band(value: int, bands: tuple[tuple[int, str], ...], beyond: str) -> str:
    for ceiling, label in bands:
        if value <= ceiling:
            return label
    return beyond


def dominant_marker(markers: tuple[str, ...]) -> str:
    """The strongest cost shape in a group, ties broken by name."""
    return min(markers, key=lambda value: (-MULTIPLIER_POINTS.get(value, 1), value))


def weakest_provenance(provenances: set[str]) -> str:
    """The least reliable resolution in a group, ties broken by name.

    A group is only as trustworthy as its worst edge, so this is a ``min`` on
    points where :func:`dominant_marker` is a ``min`` on negated points. They
    read alike and mean the opposite; that is intentional.
    """
    return min(provenances, key=lambda value: (PROVENANCE_POINTS.get(value, 0), value))


def amplification(marker: str) -> str:
    """The repetition shape the evidence supports, never a magnitude estimate."""
    if marker in AMPLIFICATION:
        return AMPLIFICATION[marker]
    return "per_iteration" if marker in MULTIPLIER_POINTS else "unknown"


def exposure(reachable: bool | None) -> str:
    """How closely an entry point reaches this group.

    The graph answers reachable or not for the loop-owning function and stores
    no distance, so there is no nearer or farther to report. Unknown is the
    common answer and stays visible as one.
    """
    if reachable is True:
        return "entry_reachable"
    return "not_entry_reachable" if reachable is False else "unknown"


def leverage(call_sites: int) -> str:
    """How many places one intervention would settle."""
    return _band(call_sites, _LEVERAGE_BANDS, "broad")


def change_risk(affected_files: int) -> str:
    """How far the edit reaches, counted in files holding evidence.

    Structural reach only. Churn and hotspot history live in the serving layer
    and are not read here, so nothing in this module costs a query.
    """
    return _band(affected_files, _CHANGE_RISK_BANDS, "wide")


def observation_rank(marker: str | None, boundary: str | None, cross_function: bool) -> int:
    """Order-of-magnitude key for one raw observation, not one opportunity.

    Findings all carry zero health impact by construction, so without this the
    performance rows in a ranked list came back in file order. Same tables as
    the opportunity order above, deliberately: "which marker costs more" is one
    question, and answering it twice with two sets of numbers is how the two
    surfaces came to disagree about the same marker.
    """
    points = MULTIPLIER_POINTS.get(marker or "", UNKNOWN_MULTIPLIER_POINTS)
    points += BOUNDARY_POINTS.get(boundary or "", 0)
    return points + (CROSS_FUNCTION_POINTS if cross_function else 0)


def rank_factors(
    *,
    marker: str,
    boundary: str | None,
    context: str,
    reachable: bool | None,
    site_count: int,
    provenance: str,
) -> dict[str, int]:
    """The additive rank terms, published verbatim on every opportunity.

    Call-site count is logarithmic and capped: leverage matters, but sixty
    callers is not fifteen times the lead four callers is.
    """
    return {
        "multiplier_shape": MULTIPLIER_POINTS.get(marker, 1),
        "boundary_kind": BOUNDARY_POINTS.get(boundary or "", 0),
        "execution_context": CONTEXT_POINTS.get(context, 1),
        "entry_reachability": 3 if reachable is True else 0,
        "affected_call_sites": min(8, int(log2(site_count + 1) * 2)),
        "provenance": PROVENANCE_POINTS.get(provenance, 0),
    }


def why_ranked(factors: dict[str, int], values: dict[str, Any], limit: int = 3) -> tuple[dict, ...]:
    """The few terms that actually decided this position.

    Structured, not prose: each entry names the factor, the input it read, and
    the points it contributed, so a caller can render it without parsing and a
    reword never churns anything. Zero-point terms explain nothing and are
    dropped, so fewer than ``limit`` entries is a normal answer.
    """
    ranked = sorted(
        ((name, points) for name, points in factors.items() if points),
        key=lambda item: (-item[1], item[0]),
    )
    return tuple(
        {"factor": name, "value": values.get(name), "points": points}
        for name, points in ranked[:limit]
    )


def rank_sort_key(item: Any) -> tuple[int, int, int, str]:
    """A total order: actionable first, then rank, leverage, and id.

    The id tail is what makes it total, so ties never float between runs.
    """
    return (
        ACTIONABILITY_ORDER[item.actionability_state],
        -item.rank_score,
        -item.affected_call_sites_total,
        item.opportunity_id,
    )


__all__ = [
    "ACTIONABILITY_ORDER",
    "AMPLIFICATION",
    "BOUNDARY_POINTS",
    "CONTEXT_POINTS",
    "CROSS_FUNCTION_POINTS",
    "MULTIPLIER_POINTS",
    "PROVENANCE_POINTS",
    "UNKNOWN_MULTIPLIER_POINTS",
    "amplification",
    "change_risk",
    "dominant_marker",
    "exposure",
    "leverage",
    "observation_rank",
    "rank_factors",
    "rank_sort_key",
    "weakest_provenance",
    "why_ranked",
]
