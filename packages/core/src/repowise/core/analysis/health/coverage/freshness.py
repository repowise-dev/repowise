"""Does stored coverage describe the code a reader is asking about?

One rule for every surface: coverage is ``current`` when it was measured at
the commit in question, ``stale`` when at another, ``unknown`` when either
commit is missing. Callers differ only in which commit they ask about (the
indexed tree, a change's head).

Uncommitted work has no commit to compare, so it gets the one rule time can
give (:func:`working_tree_freshness`): coverage ingested after the last edit
to every changed file ran against the code on disk.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

FreshnessStatus = Literal["current", "stale", "unknown"]


def coverage_freshness(
    measured_commit: str | None, reference_commit: str | None
) -> FreshnessStatus:
    """Freshness of coverage measured at *measured_commit* for *reference_commit*."""
    if not measured_commit or not reference_commit:
        return "unknown"
    return "current" if measured_commit == reference_commit else "stale"


def working_tree_freshness(
    ingested_at: datetime | None, newest_mtime: float | None
) -> FreshnessStatus:
    """Freshness of coverage ingested at *ingested_at* for files last modified at *newest_mtime*.

    ``current`` when the ingest came after the newest modification of the
    changed files on disk, else ``stale``; ``unknown`` when either time is
    missing. Ceiling: an ingest of an old report after the last edit reads
    current, because the report's own run time is not stored.
    """
    if ingested_at is None or newest_mtime is None:
        return "unknown"
    # SQLite hands back the stored UTC time without its zone.
    at = ingested_at if ingested_at.tzinfo else ingested_at.replace(tzinfo=UTC)
    return "current" if at.timestamp() > newest_mtime else "stale"


def newest_mtime(repo_root: str | Path, paths: Iterable[str]) -> float | None:
    """The latest modification time among *paths* on disk; ``None`` when none exists."""
    times = []
    for path in paths:
        try:
            times.append((Path(repo_root) / path).stat().st_mtime)
        except OSError:
            continue
    return max(times, default=None)
