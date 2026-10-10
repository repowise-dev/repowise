"""Ordering policy: the magnitude facets, the weight tables, and the total order.

Nothing here is a score in the health sense. No value produced by this module
is blended into ``performance_score`` or any other dimension; it is an ordering
key whose weights are frozen policy, not fitted parameters.

This module owns every judgement about how much a group costs and how far a
change to it reaches: the marker, boundary, context, and provenance tables and
the four magnitude facets read off them. Whether a change is *safe* is the
neighbouring module's question, and the only thing this one takes from it is
the actionability state that breaks a tie in value.

Every input is already carried on the group. Ranking issues no query of its
own, per opportunity or otherwise.
"""

from __future__ import annotations

from collections.abc import Mapping
from math import log2
from typing import Any

from ..rank_common import top_factors, weakest
from ..rows import detail_map, field
from ..worth import cost_proof, lead_reason
from .actionability import EXPECTED_REASONS

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
    "unbounded_read_reduced_in_memory": 4,
    # One round trip per iteration, same shape as io_in_loop.
    "lazy_load_in_loop": 4,
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

NON_LEADING_MARKERS = frozenset({"lazy_load_in_loop"})
"""Markers that rank normally but never lead the performance directive.

``lazy_load_in_loop`` measured 81% on held-out Django (47/58, 2026-09-26), inside the
80-90% tier whose pre-registered rule is "advisory, never leads". Remove a marker once a
held-out sample puts it at 90% or above, or list the ORM that cleared it below.
"""

LEADING_ORMS = {"lazy_load_in_loop": frozenset({"django"})}
"""ORMs a non-leading marker may lead for, read off each finding's ``details["orm"]``.

Django cleared the bar on a fresh held-out sample after narrowing (29/32 = 90.6%,
Wilson LB 75.8%); SQLAlchemy has fewer than 30 labels and stays non-leading.
"""


def may_lead(marker: str, orms: set[Any], facets: Mapping[str, Any]) -> bool:
    """Whether a group of *marker* with members on *orms* may lead the directive.

    Its cost has to be proven to matter (:func:`worth.lead_reason`), and every
    member has to be on an ORM that cleared the bar: one unmeasured member is
    enough to keep the group from leading.
    """
    if lead_reason(marker, facets) is not None:
        return False
    if marker not in NON_LEADING_MARKERS:
        return True
    return bool(orms) and orms <= LEADING_ORMS.get(marker, frozenset())

UNKNOWN_MULTIPLIER_POINTS = 1
"""A detector added without a weight under-ranks rather than jumps the queue."""

CROSS_FUNCTION_POINTS = 1
"""A loop and a sink in different functions is the one nobody sees by reading
the loop, so it earns a point that an intra-function hit does not."""

CONTEXT_POINTS = {"production": 3, "tooling": 2, "test": 1, "unknown": 1}
MAGNITUDE_POINTS = {"grows_with_data": 2, "n/a": 1, "bounded": 0, "unknown": 0}
"""A loop over a query result is paid again as the data grows; a retry loop or a
constant-width slice is not. Unknown earns nothing, so a guess never outranks a fact."""
PROVENANCE_POINTS = {"call-site": 3, "direct": 3, "reliable-edge": 2, "name-fallback": 0}

AMPLIFICATION = {
    "nested_loop_with_io": "quadratic",
    "nested_loop_quadratic": "quadratic",
    "hot_path_sync_io": "per_call",
    "blocking_sync_in_async": "per_call",
    # Runs once (not per iteration); its cost scales with the unbounded row
    # count the read transfers and decodes, not with a loop trip count.
    "unbounded_read_reduced_in_memory": "per_call",
}
"""Marker to the repetition shape its evidence supports.

Everything with a loop points at the same claim, so only the exceptions are
listed and :func:`amplification` supplies the rest. A marker nobody has
characterised reports ``unknown`` rather than borrowing a neighbour's shape.
"""

ACTIONABILITY_ORDER = {"plan_ready": 0, "advisory": 1, "investigate": 2, "expected": 3}
"""The tie-break between groups of equal value: the more actionable one first.

Value leads. Sorting actionability first put a low-value plan above a costly
cause nobody had a plan for yet, so the queue's order said "easy" where it
should say "worth it". A plan-ready group is still never buried by an equal
one, and the default queue leaves out what has no strategy at all, which is
what kept a generic sink's volume from crowding out actionable work.
"""

DEFAULT_QUEUE_CONTEXTS = frozenset({"production"})
DEFAULT_QUEUE_STATES = frozenset({"plan_ready", "advisory"})
DEFAULT_QUEUE_PROOFS = frozenset({"proven"})
"""What the queue holds when a caller names no filter: production work with a
strategy whose cost is measured.

Test, tooling and unclassified code, ``expected`` repetition, causes with no
supported strategy (``investigate``, whose plan state is always ``no_safe_plan``)
and causes whose loop nobody measured (``worth.cost_proof``) are true and stay one
filter away, but none of them is work to schedule. Each is counted by
:func:`default_queue_exclusion` so leaving it out is never silent.
"""

DEFAULT_QUEUE_EXCLUSIONS = (
    "test",
    "tooling",
    "unknown",
    *EXPECTED_REASONS,
    "expected",
    "no_strategy",
    "unmeasured_cost",
)
"""Every reason the default queue leaves a cause out, in the order it is checked."""

_LEVERAGE_BANDS = ((1, "isolated"), (3, "local"), (9, "shared"))
_CHANGE_RISK_BANDS = ((1, "contained"), (4, "moderate"))


def band(value: int, bands: tuple[tuple[int, str], ...], beyond: str) -> str:
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
    return weakest(provenances, PROVENANCE_POINTS)


def amplification(marker: str) -> str:
    """The repetition shape the evidence supports, never a magnitude estimate."""
    if marker in AMPLIFICATION:
        return AMPLIFICATION[marker]
    return "per_iteration" if marker in MULTIPLIER_POINTS else "unknown"


def loop_magnitude(marker: str, details: list[dict[str, Any]]) -> str:
    """Whether the loop's trip count grows with data, read off every member.

    One member proven to grow is enough to say the cause grows; ``bounded``
    needs every member. Markers that are not paid per iteration have no loop
    to measure.
    """
    if amplification(marker) not in {"per_iteration", "quadratic"}:
        return "n/a"
    values = {detail.get("loop_magnitude", "unknown") for detail in details}
    if "grows_with_data" in values:
        return "grows_with_data"
    return "bounded" if values == {"bounded"} else "unknown"


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
    return band(call_sites, _LEVERAGE_BANDS, "broad")


def change_risk(affected_files: int) -> str:
    """How far the edit reaches, counted in files holding evidence.

    Structural reach only. Churn and hotspot history live in the serving layer
    and are not read here, so nothing in this module costs a query.
    """
    return band(affected_files, _CHANGE_RISK_BANDS, "wide")


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
    magnitude: str,
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
        "loop_magnitude": MAGNITUDE_POINTS[magnitude],
    }


def why_ranked(factors: dict[str, int], values: dict[str, Any], limit: int = 3) -> tuple[dict, ...]:
    """The few terms that actually decided this position.

    Structured, not prose: each entry names the factor, the input it read, and
    the points it contributed, so a caller can render it without parsing and a
    reword never churns anything. Zero-point terms explain nothing and are
    dropped, so fewer than ``limit`` entries is a normal answer.
    """
    return tuple(
        {"factor": name, "value": values.get(name), "points": points}
        for name, points in top_factors(factors, limit)
    )


def strategy_exclusion(
    item: Any, contexts: frozenset[str] = DEFAULT_QUEUE_CONTEXTS
) -> str | None:
    """Why *item* is no work to schedule, by context and state, or ``None``.

    Reads an opportunity, an ORM row or a plain row. *contexts* widens it for a
    caller that asked for more (Fix first's ``scope="all"`` keeps test code);
    the state rule never moves. Fix first reads this rather than the default
    queue because it keeps an unproven cause, as a ``later`` item.
    """
    context = field(item, "execution_context")
    if context not in contexts:
        return context
    state = field(item, "actionability_state")
    if state in DEFAULT_QUEUE_STATES:
        return None
    if state != "expected":
        return "no_strategy"
    reason = field(item, "actionability_reason") or detail_map(item).get("actionability_reason")
    return reason if reason in EXPECTED_REASONS else "expected"


def default_queue_exclusion(item: Any) -> str | None:
    """Why the default queue leaves *item* out, or ``None`` when it is queued.

    One reason per cause, context first and proof last, so the counts of every
    reason and the queue add up to the whole.
    """
    reason = strategy_exclusion(item)
    if reason is None and cost_proof(item) not in DEFAULT_QUEUE_PROOFS:
        return "unmeasured_cost"
    return reason


def default_queue_counts(items: list[Any]) -> dict[str, Any]:
    """The default queue's size and what it leaves out, by reason."""
    excluded = dict.fromkeys(DEFAULT_QUEUE_EXCLUSIONS, 0)
    queued = 0
    for item in items:
        reason = default_queue_exclusion(item)
        if reason is None:
            queued += 1
        else:
            excluded[reason] = excluded.get(reason, 0) + 1
    return {"total": queued, "excluded": excluded}


def rank_sort_key(item: Any) -> tuple[int, int, int, str]:
    """A total order: value first, then actionability, leverage, and id.

    The id tail is what makes it total, so ties never float between runs.
    """
    return (
        -item.rank_score,
        ACTIONABILITY_ORDER[item.actionability_state],
        -item.affected_call_sites_total,
        item.opportunity_id,
    )


__all__ = [
    "ACTIONABILITY_ORDER",
    "AMPLIFICATION",
    "BOUNDARY_POINTS",
    "CONTEXT_POINTS",
    "CROSS_FUNCTION_POINTS",
    "DEFAULT_QUEUE_CONTEXTS",
    "DEFAULT_QUEUE_EXCLUSIONS",
    "DEFAULT_QUEUE_PROOFS",
    "DEFAULT_QUEUE_STATES",
    "LEADING_ORMS",
    "MAGNITUDE_POINTS",
    "MULTIPLIER_POINTS",
    "NON_LEADING_MARKERS",
    "PROVENANCE_POINTS",
    "UNKNOWN_MULTIPLIER_POINTS",
    "amplification",
    "band",
    "change_risk",
    "default_queue_counts",
    "default_queue_exclusion",
    "dominant_marker",
    "exposure",
    "leverage",
    "loop_magnitude",
    "may_lead",
    "observation_rank",
    "rank_factors",
    "rank_sort_key",
    "strategy_exclusion",
    "weakest_provenance",
    "why_ranked",
]
