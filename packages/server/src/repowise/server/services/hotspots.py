"""The hotspot list: one ranked, searchable, filterable page plus its facets.

Rank is the file's place by temporal hotspot score within the scope (the held
origins left out), so it stays the same whatever the reader filters, searches or
sorts by. The facets count the whole searched set in one aggregate and are kept
per index state, so a page turn costs the page query alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Integer, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement, Select

from repowise.core.persistence.index_cache import IndexStampCache
from repowise.core.persistence.models import GitMetadata, HealthFileMetric

__all__ = [
    "FILTERS",
    "SORTS",
    "HotspotListing",
    "HotspotQuery",
    "accelerating_clause",
    "clear_hotspot_cache",
    "is_accelerating",
    "list_hotspots",
    "origin_clause",
]

HotspotFilter = Literal["all", "bus_factor_1", "accelerating", "bug_magnet"]
FILTERS: tuple[str, ...] = ("all", "bus_factor_1", "accelerating", "bug_magnet")
SORTS: tuple[str, ...] = ("score", "commits", "fixes", "churn")

#: The trailing window the commit counts describe, and its recent slice.
WINDOW_DAYS = 90
RECENT_DAYS = 30

#: Facets per (database, repository, index stamp, held set, search).
_facets = IndexStampCache(64)


def clear_hotspot_cache() -> None:
    _facets.clear()


def is_accelerating(commits_30d: int | None, commits_90d: int | None, age_days: int | None) -> bool:
    """A file changing faster in the last 30 days than over its last 90.

    Only a file older than the window has a pace to speed up from: a file
    created this quarter has all its commits recent by construction, which is
    newness, not acceleration. :func:`accelerating_clause` is the same rule in SQL.
    """
    if (age_days or 0) <= WINDOW_DAYS:
        return False
    return (commits_30d or 0) * (WINDOW_DAYS // RECENT_DAYS) > (commits_90d or 0)


def accelerating_clause(g: Any = GitMetadata) -> ColumnElement[bool]:
    """:func:`is_accelerating` as a ``where`` clause over git rows."""
    return and_(
        func.coalesce(g.age_days, 0) > WINDOW_DAYS,
        func.coalesce(g.commit_count_30d, 0) * (WINDOW_DAYS // RECENT_DAYS)
        > func.coalesce(g.commit_count_90d, 0),
    )


def _bug_magnet_clause(g: Any = GitMetadata) -> ColumnElement[bool]:
    # The flag only counts with an age beside it (consumers drop it otherwise).
    return and_(
        g.bug_magnet.is_(True),
        g.last_fix_at.is_not(None),
        func.coalesce(g.prior_defect_count, 0) > 0,
    )


def _bus_factor_1_clause(g: Any = GitMetadata) -> ColumnElement[bool]:
    # Exactly one: zero means the file has no attributable history, not one owner.
    return g.bus_factor == 1


_FILTER_CLAUSES = {
    "bus_factor_1": _bus_factor_1_clause,
    "accelerating": accelerating_clause,
    "bug_magnet": _bug_magnet_clause,
}


def origin_clause(h: Any = HealthFileMetric) -> ColumnElement[str]:
    """:func:`repowise.core.analysis.finding_registry.stored_origin` in SQL, over
    an outer-joined health row (a file with no health row reads ``production``)."""
    return case(
        (and_(h.code_origin.is_not(None), h.code_origin != ""), h.code_origin),
        (h.is_test.is_(True), "test"),
        else_="production",
    )


def _sort_columns(g: Any) -> dict[str, ColumnElement[Any]]:
    return {
        "score": g.temporal_hotspot_score,
        "commits": g.commit_count_90d,
        "fixes": g.prior_defect_count,
        "churn": g.churn_percentile,
    }


def _scope(repo_id: str, held: frozenset[str]) -> Select[Any]:
    """Hotspot rows outside the held origins, each with its rank by score."""
    g = GitMetadata
    rank = func.row_number().over(
        order_by=(g.temporal_hotspot_score.desc().nulls_last(), g.churn_percentile.desc(), g.file_path)
    )
    stmt = select(g.id.label("id"), rank.label("rank")).where(
        g.repository_id == repo_id, g.is_hotspot.is_(True)
    )
    if held:
        h = HealthFileMetric
        stmt = stmt.outerjoin(
            h, and_(h.repository_id == g.repository_id, h.file_path == g.file_path)
        ).where(origin_clause(h).not_in(sorted(held)))
    return stmt


def _search_clause(q: str) -> ColumnElement[bool]:
    needle = q.lower()
    return or_(
        func.lower(GitMetadata.file_path).contains(needle, autoescape=True),
        func.lower(func.coalesce(GitMetadata.primary_owner_name, "")).contains(needle, autoescape=True),
    )


async def _git_stamp(session: AsyncSession, repo_id: str) -> tuple[Any, ...]:
    """What moves when git rows are re-mined without a health pass: the index
    stamp watches health rows, and the facets read git rows."""
    g = GitMetadata
    stmt = select(func.count(), func.max(g.updated_at)).where(g.repository_id == repo_id)
    return tuple((await session.execute(stmt)).one())


@dataclass(frozen=True)
class HotspotQuery:
    """One page of the list: which rows (scope, search, filter), in what order."""

    limit: int = 50
    offset: int = 0
    q: str = ""
    filter: str = "all"
    sort: str = "score"
    descending: bool = True
    held: frozenset[str] = frozenset()


@dataclass
class HotspotListing:
    rows: list[tuple[GitMetadata, int]]
    total: int
    facets: dict[str, int]


async def _count_facets(session: AsyncSession, base: Select[Any]) -> dict[str, int]:
    """Every facet over the searched set in one aggregate."""

    def count_of(clause: ColumnElement[bool]) -> Any:
        return func.coalesce(func.sum(case((clause, 1), else_=0)), 0).cast(Integer)

    row = (
        await session.execute(
            base.with_only_columns(
                func.count(),
                count_of(_bus_factor_1_clause()),
                count_of(accelerating_clause()),
                count_of(_bug_magnet_clause()),
                count_of(func.coalesce(GitMetadata.prior_defect_count, 0) > 0),
            ).order_by(None)
        )
    ).one()
    keys = ("total", "bus_factor_1", "accelerating", "bug_magnet", "with_fixes")
    return dict(zip(keys, map(int, row), strict=True))


async def list_hotspots(session: AsyncSession, repo_id: str, query: HotspotQuery) -> HotspotListing:
    scope = _scope(repo_id, query.held).subquery()
    g = GitMetadata
    base = select(g, scope.c.rank).join(scope, scope.c.id == g.id)
    q = query.q.strip()
    if q:
        base = base.where(_search_clause(q))

    stamp = await _git_stamp(session, repo_id)
    key = await _facets.key(session, repo_id, stamp, tuple(sorted(query.held)), q)
    facets = _facets.get(key) if key in _facets else _facets.remember(key, await _count_facets(session, base))

    narrow = _FILTER_CLAUSES.get(query.filter)
    listing = base if narrow is None else base.where(narrow())
    column = _sort_columns(g)[query.sort]
    ordered = column.desc() if query.descending else column.asc()
    page = listing.order_by(ordered.nulls_last(), scope.c.rank).limit(query.limit).offset(query.offset)
    rows = [(r[0], int(r[1])) for r in (await session.execute(page)).all()]
    total = facets["total"] if narrow is None else facets[query.filter]
    return HotspotListing(rows=rows, total=total, facets=facets)
