"""The order a decision review queue is worked in: what most needs a person first.

One ordering, shared by every surface that pages a review lane, so the local
server and the hosted dashboard put the same record at the top. Pure: the
caller reads the signals from whatever store it has and hands them in.

Most urgent first:

1. The acceptance contract would take the record as it stands. A reviewer can
   act on it from the row, and one that cannot be accepted still sorts below
   instead of leaving the queue, because dismissing it is also a review.
2. It names files that are churn hotspots. A wrong rule on a file that changes
   every week costs the most, because agents edit that file most often. Capped,
   so a record that names twenty hotspots because it names everything does not
   outrank a precise one.
3. Its scope has moved since it was recorded (``staleness_score``). Evidence
   the code has walked away from is the claim most likely to be wrong now.
4. Confidence, then newest, break the remaining ties.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

T = TypeVar("T")

#: Hotspot files past this count no longer raise a record's place in the queue.
#: Past a few, the count measures how broad a record is, not how risky.
HOT_FILE_CAP = 3


@dataclass(frozen=True)
class ReviewSignals:
    """What the queue order reads about one record."""

    #: The acceptance contract has no blocker for it.
    acceptable: bool
    #: How many of its named files are churn hotspots.
    hot_files: int
    #: Share of its scope that changed since it was recorded, 0..1.
    staleness: float
    confidence: float
    #: A datetime or an ISO string; ``None`` sorts oldest.
    created_at: datetime | str | None


def _recency(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return value or ""


def review_queue_order(rows: Iterable[T], signals: Callable[[T], ReviewSignals]) -> list[T]:
    """*rows* most urgent first, by the rule in this module's docstring.

    Two stable passes instead of one composite key: the last tiebreak is a
    descending date, and an ISO string has no numeric negation.
    """
    pairs = [(row, signals(row)) for row in rows]
    pairs.sort(key=lambda p: _recency(p[1].created_at), reverse=True)
    pairs.sort(
        key=lambda p: (
            not p[1].acceptable,
            -min(p[1].hot_files, HOT_FILE_CAP),
            -(p[1].staleness or 0.0),
            -(p[1].confidence or 0.0),
        )
    )
    return [row for row, _ in pairs]
