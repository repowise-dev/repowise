"""Opportunities that observe the same lines, and which fix leads among them.

One awaited query in a loop is both ``io_in_loop`` and ``serial_await_in_loop``.
They are one problem with two remedies, so each names the other, and a weaker
remedy is queued directly after the stronger one instead of wherever its own
actionability would put it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any, TypeVar

T = TypeVar("T")

STRATEGY_PREFERENCE = ("batch_or_prefetch_io", "parallelize_independent_awaits")
"""Earlier wins: batching removes round-trips, parallelizing only overlaps them."""


def _strategy(item: Any) -> str | None:
    return item.fix.strategy if item.fix else None


def _preference(item: Any) -> int | None:
    strategy = _strategy(item)
    return STRATEGY_PREFERENCE.index(strategy) if strategy in STRATEGY_PREFERENCE else None


def _relation(own: int | None, other: int | None) -> str:
    if own is None or other is None or own == other:
        return "same_site"
    return "preferred" if other < own else "alternative"


def link_siblings(
    items: list[T], sites: dict[str, set[Any]], sort_key: Callable[[T], Any]
) -> list[T]:
    """Attach ``siblings`` to every item sharing a site, and order the queue.

    *sites* maps each opportunity id to every site its members observed, not
    only the capped evidence, so a cause is never missed for being row nine.
    """
    by_site: dict[Any, set[str]] = defaultdict(set)
    for opportunity_id, members in sites.items():
        for site in members:
            by_site[site].add(opportunity_id)
    by_id = {item.opportunity_id: item for item in items}
    ordered = sorted(items, key=sort_key)
    position = {item.opportunity_id: index for index, item in enumerate(ordered)}

    linked: dict[str, T] = {}
    leader: dict[str, str] = {}
    for item in ordered:
        oid = item.opportunity_id
        others = sorted(
            {o for site in sites.get(oid, ()) for o in by_site[site]} - {oid},
            key=position.__getitem__,
        )
        own = _preference(item)
        siblings = tuple(
            {
                "opportunity_id": other,
                "biomarker_type": by_id[other].biomarker_type,
                "strategy": _strategy(by_id[other]),
                "relation": _relation(own, _preference(by_id[other])),
            }
            for other in others
        )
        linked[oid] = replace(item, siblings=siblings) if siblings else item
        preferred = next((s["opportunity_id"] for s in siblings if s["relation"] == "preferred"), None)
        if preferred is not None:
            leader[oid] = preferred
    return _queue([linked[item.opportunity_id] for item in ordered], leader)


def _queue(ordered: Iterable[T], leader: dict[str, str]) -> list[T]:
    """Rank order, with each superseded item moved to just after its leader."""
    followers: dict[str, list[T]] = defaultdict(list)
    for item in ordered:
        if item.opportunity_id in leader:
            followers[leader[item.opportunity_id]].append(item)
    out: list[T] = []
    placed: set[str] = set()

    def place(item: T) -> None:
        if item.opportunity_id in placed:
            return
        placed.add(item.opportunity_id)
        out.append(item)
        for follower in followers.get(item.opportunity_id, ()):
            place(follower)

    for item in ordered:
        if item.opportunity_id not in leader:
            place(item)
    for item in ordered:
        # A leader cycle cannot form (preference is a strict order), but never
        # drop an item if one did.
        place(item)
    return out


__all__ = ["STRATEGY_PREFERENCE", "link_siblings"]
