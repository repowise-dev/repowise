"""What stored query counts say about a call site: grows, flat, or only ran.

Counts are exact, so they may confirm or contradict a static N+1 finding where
timing may not. One statement at one call site (a ``statement_fp``) is read
across the scopes (tests or requests) that issued it:

- ``grows``: two scopes that ran over different numbers of items, and the
  larger one issued more statements. The cost grows with the data: the
  finding is confirmed.
- ``flat``: scopes that ran over at least two different numbers of items all
  issued the same number of statements. Counter-evidence: the count does not
  follow the input.
- ``ran``: anything else, including scopes that did not say how many items
  they ran over. Reported as "ran with k statements at n items"; it confirms
  nothing and contradicts nothing.

The caller passes one statement's scopes from one run and one scope kind, so
two sizes are one measurement. A site with several statements takes its
strongest reading: ``grows`` over ``flat`` over ``ran``; among ``ran``
readings, the most statements.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations

#: Readings, strongest first.
VERDICTS = ("grows", "flat", "ran")


@dataclass(frozen=True)
class QueryReading:
    """One call site's reading, and the largest count behind a ``ran``."""

    verdict: str
    statements: int
    items: int | None


def read_statement(points: Iterable[tuple[int | None, int]]) -> QueryReading:
    """The reading of one statement from its ``(items, statements)`` per scope."""
    seen = list(points)
    sized = sorted({(n, k) for n, k in seen if n is not None})
    pairs = [(a, b) for a, b in combinations(sized, 2) if a[0] < b[0]]
    most = max(seen, key=lambda p: (p[1], p[0] or 0))
    if any(a[1] < b[1] for a, b in pairs):
        return QueryReading("grows", most[1], most[0])
    if pairs and len({k for _n, k in sized}) == 1:
        return QueryReading("flat", most[1], most[0])
    return QueryReading("ran", most[1], most[0])


def strongest(readings: Iterable[QueryReading]) -> QueryReading | None:
    """The strongest of *readings*, or ``None`` when there are none."""
    return min(
        readings,
        key=lambda r: (VERDICTS.index(r.verdict), -r.statements, -(r.items or 0)),
        default=None,
    )


__all__ = ["VERDICTS", "QueryReading", "read_statement", "strongest"]
