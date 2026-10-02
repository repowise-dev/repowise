"""Does stored coverage describe the code a reader is asking about?

One rule for every surface: coverage is ``current`` when it was measured at
the commit in question, ``stale`` when at another, ``unknown`` when either
commit is missing. Callers differ only in which commit they ask about (the
indexed tree, a change's head).
"""

from __future__ import annotations

from typing import Literal

FreshnessStatus = Literal["current", "stale", "unknown"]


def coverage_freshness(
    measured_commit: str | None, reference_commit: str | None
) -> FreshnessStatus:
    """Freshness of coverage measured at *measured_commit* for *reference_commit*."""
    if not measured_commit or not reference_commit:
        return "unknown"
    return "current" if measured_commit == reference_commit else "stale"
