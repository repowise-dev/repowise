"""Query parameters the commit and change history surfaces share, as dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Query

from repowise.core.analysis.history import DELTA_FILTERS
from repowise.core.persistence import crud

_DELTA_PATTERN = f"^({'|'.join(DELTA_FILTERS)})$"


def utc(moment: datetime | None) -> datetime | None:
    """*moment* with UTC assumed when it names no zone."""
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


@dataclass(frozen=True)
class HistoryParams:
    author: str | None
    area: str | None
    since: datetime | None
    until: datetime | None
    delta: str | None
    touches_hotspot: bool

    def commit_filter(self) -> crud.CommitFilter:
        return crud.CommitFilter(
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
