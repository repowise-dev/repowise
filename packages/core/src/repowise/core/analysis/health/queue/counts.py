"""The one count vocabulary every queue surface reports, and a unit's stored judgement.

Each unit (findings, performance causes, refactoring plans, Fix first items)
counts at five levels: ``inventory`` every open unit; ``in_scope`` those whose
code is in scope (not test, tooling, vendored, generated or docs); ``eligible``
those a default queue holds; ``due`` eligible units in a tier worth scheduling
now; ``shown`` what the response carries. ``excluded`` names every reason a
unit is out, so the levels add up.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .eligibility import SCOPE_EXCLUSIONS, Verdict, perf_low_priority, perf_queue_verdict
from .order import DUE_TIERS
from .value import perf_confidence, perf_ready, perf_value, tier

#: Counted for a row stored before units were judged; the next index judges it.
NOT_JUDGED = "not_judged"


@dataclass(frozen=True, slots=True)
class Judgement:
    """A unit's verdict as stored on its row: value and tier only when eligible."""

    reason: str | None = None
    value: int | None = None
    tier: str | None = None

    @classmethod
    def of(cls, verdict: Verdict, value: int | None = None, tier: str | None = None) -> Judgement:
        if not verdict.eligible:
            return cls(verdict.reason)
        return cls(None, value, tier)

    def columns(self) -> dict[str, Any]:
        """The ``queue_*`` columns this judgement writes."""
        return {
            "queue_eligible": self.reason is None,
            "queue_reason": self.reason,
            "queue_value": self.value,
            "queue_tier": self.tier,
        }


def perf_judgement(row: Any, facets: Mapping[str, Any], plan: Mapping[str, Any]) -> Judgement:
    """A cause's place in the performance default queue, valued and tiered as
    Fix first values and tiers it when the cause leads its item."""
    verdict = perf_queue_verdict(row)
    if not verdict.eligible:
        return Judgement(verdict.reason)
    value = perf_value(row, facets)
    level, _why = tier(value, perf_confidence(facets), perf_ready(row, plan), perf_low_priority(row))
    return Judgement(None, value, level)


@dataclass(frozen=True, slots=True)
class QueueCounts:
    """One unit's counts at the five levels, and the reasons for what is out."""

    inventory: int = 0
    in_scope: int = 0
    eligible: int = 0
    due: int = 0
    shown: int = 0
    excluded: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "inventory": self.inventory,
            "in_scope": self.in_scope,
            "eligible": self.eligible,
            "due": self.due,
            "shown": self.shown,
            "excluded": dict(self.excluded),
        }


def queue_counts(groups: Iterable[tuple[str | None, str | None, int]], shown: int) -> QueueCounts:
    """Fold ``(reason, tier, n)`` groups (``reason`` ``None`` for an eligible
    unit) into the counts; ``shown`` is the caller's."""
    eligible = due = 0
    excluded: dict[str, int] = {}
    for reason, level, n in groups:
        if reason is None:
            eligible += n
            due += n if level in DUE_TIERS else 0
        else:
            excluded[reason] = excluded.get(reason, 0) + n
    return counts_of(eligible, due, excluded, shown)


def counts_of(eligible: int, due: int, excluded: Mapping[str, int], shown: int) -> QueueCounts:
    """The counts from what a queue holds and what it left out, by reason."""
    kept = {reason: n for reason, n in excluded.items() if n}
    inventory = eligible + sum(kept.values())
    out_of_scope = sum(n for reason, n in kept.items() if reason in SCOPE_EXCLUSIONS)
    return QueueCounts(
        inventory=inventory,
        in_scope=inventory - out_of_scope,
        eligible=eligible,
        due=due,
        shown=shown,
        excluded=dict(sorted(kept.items(), key=lambda i: (-i[1], i[0]))),
    )


__all__ = [
    "NOT_JUDGED",
    "Judgement",
    "QueueCounts",
    "counts_of",
    "perf_judgement",
    "queue_counts",
]
