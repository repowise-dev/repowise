"""Query parameters the commit and change history surfaces share, as dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Query

from repowise.core.analysis.history import DELTA_FILTERS
from repowise.core.persistence import crud

_DELTA_PATTERN = f"^({'|'.join(DELTA_FILTERS)})$"


def utc(moment: datetime | None) -> datetime | None:
    """*moment* in UTC, assumed UTC when it names no zone. Converted rather than
    kept as given: SQLite stores wall-clock UTC and drops a bound value's zone."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


@dataclass(frozen=True)
class HistoryParams:
    author: str | None
    area: str | None
    since: datetime | None
    until: datetime | None
    delta: str | None
    touches_hotspot: bool

    def commit_filter(self, **feed: Any) -> crud.CommitFilter:
        """The commit filter, plus the caller's own fields (the feed's
        *authorship* / *kind* / *high_cut*, the current analyzer *stamp*)."""
        return crud.CommitFilter(
            **feed,
            author=self.author,
            area=self.area,
            since=self.since,
            until=self.until,
            delta=self.delta,
            touches_hotspot=self.touches_hotspot,
        )


def history_params(
    author: str | None = Query(None, description="Author email (any case) or exact name"),
    area: str | None = Query(None, description="A directory or file path; keeps work touching it"),
    since: datetime | None = Query(None, description="ISO 8601, inclusive"),
    until: datetime | None = Query(None, description="ISO 8601, exclusive"),
    delta: str | None = Query(
        None,
        pattern=_DELTA_PATTERN,
        description=(
            "What it did to health: improved | worsened (each includes mixed) | mixed | "
            "neutral. Never-scanned work matches none."
        ),
    ),
    touches_hotspot: bool = Query(False, description="Only work that touched a hotspot file"),
) -> HistoryParams:
    return HistoryParams(author, area, utc(since), utc(until), delta, touches_hotspot)
