"""Ordering policy: the weight tables and the total order over opportunities.

Nothing here is a score in the health sense: no value produced by this module
is blended into ``performance_score`` or any other dimension. It is an ordering
key, and its weights are frozen product policy rather than fitted parameters.

The same multiplier table answers two questions, which is why it lives in one
place: how much a cost shape contributes to rank, and which of a group's several
shapes is the one worth naming.
"""

from __future__ import annotations

from math import log2
from typing import Any

BOUNDARY_POINTS = {"subprocess": 5, "network": 4, "db": 4, "lock": 3, "filesystem": 2}
MULTIPLIER_POINTS = {
    "nested_loop_with_io": 6,
    "nested_loop_quadratic": 6,
    "blocking_io_under_lock": 5,
    "io_in_loop": 4,
    "serial_await_in_loop": 4,
    "resource_construction_in_loop": 4,
    "membership_test_against_list_in_loop": 3,
    "string_concat_in_loop": 3,
    "lock_in_loop": 2,
}
CONTEXT_POINTS = {"production": 3, "tooling": 2, "test": 1}
PROVENANCE_POINTS = {"call-site": 3, "direct": 3, "reliable-edge": 2, "name-fallback": 0}


def dominant_marker(markers: tuple[str, ...]) -> str:
    """The strongest cost shape in a group, ties broken by name."""
    return min(markers, key=lambda value: (-MULTIPLIER_POINTS.get(value, 1), value))


def weakest_provenance(provenances: set[str]) -> str:
    """The least reliable resolution in a group, ties broken by name.

    A group is only as trustworthy as its worst edge, so this is a ``min`` on
    points where :func:`dominant_marker` is a ``min`` on negated points. The two
    read alike and mean the opposite; that is intentional, not a slip.
    """
    return min(provenances, key=lambda value: (PROVENANCE_POINTS.get(value, 0), value))


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

    Call-site count is logarithmic and capped: leverage matters, but a group of
    sixty callers is not fifteen times the lead a group of four is.
    """
    return {
        "multiplier_shape": MULTIPLIER_POINTS.get(marker, 1),
        "boundary_kind": BOUNDARY_POINTS.get(boundary or "", 0),
        "execution_context": CONTEXT_POINTS[context],
        "entry_reachability": 3 if reachable is True else 0,
        "affected_call_sites": min(8, int(log2(site_count + 1) * 2)),
        "provenance": PROVENANCE_POINTS.get(provenance, 0),
    }


def rank_sort_key(item: Any) -> tuple[int, int, str]:
    """A total order: rank, then leverage, then id so ties never float."""
    return (-item.rank_score, -item.affected_call_sites_total, item.opportunity_id)


__all__ = [
    "BOUNDARY_POINTS",
    "CONTEXT_POINTS",
    "MULTIPLIER_POINTS",
    "PROVENANCE_POINTS",
    "dominant_marker",
    "rank_factors",
    "rank_sort_key",
    "weakest_provenance",
]
