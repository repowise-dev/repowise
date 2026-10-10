"""The one cross-kind order of eligible work: tier, value, confidence, effort.

Reads any unit with ``id``, ``kind``, ``tier``, ``value``, ``score``,
``confidence``, ``effort`` and ``may_lead``. Imports nothing from the
surfaces, so each of them can read the rank tables from here.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal, Protocol, TypeVar, get_args

from ..effort import EFFORT_ORDER

Tier = Literal["now", "next", "later"]
TIERS: tuple[str, ...] = get_args(Tier)
#: Tiers a unit is due in: worth scheduling now, not just worth listing.
DUE_TIERS = frozenset({"now", "next"})

TIER_RANK = {t: i for i, t in enumerate(TIERS)}
LEVEL_RANK = {"high": 2, "medium": 1, "low": 0}
EFFORT_RANK = {e: i for i, e in enumerate(EFFORT_ORDER)}

#: In the first HEAD places no kind takes more than HEAD_PER_KIND, unless the
#: other kinds have nothing above the later tier.
HEAD = 5
HEAD_PER_KIND = 3


class Ranked(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def kind(self) -> str: ...
    @property
    def tier(self) -> str: ...
    @property
    def value(self) -> int: ...
    @property
    def score(self) -> float: ...
    @property
    def confidence(self) -> str: ...
    @property
    def effort(self) -> str: ...
    @property
    def may_lead(self) -> bool: ...


R = TypeVar("R", bound=Ranked)


def _key(u: Ranked) -> tuple[bool, int, int, int, int, float, str]:
    return (
        u.tier == "later",
        -u.value,
        TIER_RANK[u.tier],
        -LEVEL_RANK.get(u.confidence, 0),
        EFFORT_RANK.get(u.effort, 1),
        -u.score,
        u.id,
    )


def order(units: list[R]) -> list[R]:
    """``units`` in queue order: by :func:`_key`, the head spread across kinds,
    and a unit that may not lead behind the first that may."""
    ranked = sorted(units, key=_key)
    head: list[R] = []
    taken: Counter[str] = Counter()
    while ranked and len(head) < HEAD:
        pick = ranked[0]
        if taken[pick.kind] >= HEAD_PER_KIND:
            pick = next(
                (
                    u
                    for u in ranked
                    if u.tier != "later"
                    and u.kind != pick.kind
                    and taken[u.kind] < HEAD_PER_KIND
                ),
                pick,
            )
        ranked.remove(pick)
        head.append(pick)
        taken[pick.kind] += 1
    out = head + ranked
    first = next((i for i, u in enumerate(out) if u.may_lead), 0)
    if first:
        out.insert(0, out.pop(first))
    return out


__all__ = [
    "DUE_TIERS",
    "EFFORT_RANK",
    "HEAD",
    "HEAD_PER_KIND",
    "LEVEL_RANK",
    "TIERS",
    "TIER_RANK",
    "Ranked",
    "Tier",
    "order",
]
