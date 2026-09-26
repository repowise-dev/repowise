"""Headline figures and index freshness for the multi-repo dashboard.

Backs ``GET /api/repos/summary``: grouped per-repository aggregates read in a
fixed number of queries, plus the indexed-commit versus live-HEAD comparison.
"""

from __future__ import annotations

import contextlib
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import (
    DeadCodeFinding,
    GitMetadata,
    GraphNode,
    HealthSnapshot,
    Page,
)
from repowise.server.mcp_server._meta import read_live_head, resolve_indexed_commit
from repowise.server.schemas import RepoResponse

# One grouped query's answer: ``(repository_id, figures)`` per row.
_Figures = list[tuple[str, dict[str, Any]]]


def _fresh_case(column: Any, value: Any) -> Any:
    """Portable conditional count: ``FILTER (WHERE ...)`` needs SQLite 3.30+."""
    return func.coalesce(func.sum(case((column == value, 1), else_=0)), 0)


async def _file_figures(session: AsyncSession) -> _Figures:
    # `graph_nodes` also holds symbol rows, so every count is scoped to files.
    result = await session.execute(
        select(
            GraphNode.repository_id,
            func.count(GraphNode.id),
            func.coalesce(func.sum(GraphNode.symbol_count), 0),
            _fresh_case(GraphNode.is_entry_point, True),
        )
        .where(GraphNode.node_type == "file")
        .group_by(GraphNode.repository_id)
    )
    return [
        (
            repo_id,
            {
                "file_count": int(files or 0),
                "symbol_count": int(symbols or 0),
                "entry_point_count": int(entries or 0),
            },
        )
        for repo_id, files, symbols, entries in result.all()
    ]


async def _page_figures(session: AsyncSession) -> _Figures:
    # Documentation pages and the fresh subset. Never selects `content`.
    result = await session.execute(
        select(
            Page.repository_id,
            func.count(Page.id),
            _fresh_case(Page.freshness_status, "fresh"),
        ).group_by(Page.repository_id)
    )
    return [
        (repo_id, {"doc_page_count": int(pages or 0), "doc_fresh_page_count": int(fresh or 0)})
        for repo_id, pages, fresh in result.all()
    ]


async def _dead_export_figures(session: AsyncSession) -> _Figures:
    # Open unused exports: the one dead-code figure the dashboard quotes.
    result = await session.execute(
        select(DeadCodeFinding.repository_id, func.count(DeadCodeFinding.id))
        .where(
            DeadCodeFinding.kind == "unused_export",
            DeadCodeFinding.status == "open",
        )
        .group_by(DeadCodeFinding.repository_id)
    )
    return [(repo_id, {"dead_export_count": int(dead or 0)}) for repo_id, dead in result.all()]


async def _hotspot_figures(session: AsyncSession) -> _Figures:
    # Hotspots, and the tracked-file denominator they are meaningful against.
    result = await session.execute(
        select(
            GitMetadata.repository_id,
            func.count(GitMetadata.id),
            _fresh_case(GitMetadata.is_hotspot, True),
        ).group_by(GitMetadata.repository_id)
    )
    return [
        (repo_id, {"tracked_file_count": int(tracked or 0), "hotspot_count": int(hotspots or 0)})
        for repo_id, tracked, hotspots in result.all()
    ]


def _rounded(score: Any) -> float | None:
    return round(float(score), 2) if score is not None else None


async def _health_figures(session: AsyncSession) -> _Figures:
    # Latest snapshot per repo, scalar columns only: a row's per-file JSON is
    # large. Reduced in Python because retention keeps tens of rows per repo and
    # window syntax differs across the two backends.
    result = await session.execute(
        select(
            HealthSnapshot.repository_id,
            HealthSnapshot.taken_at,
            HealthSnapshot.average_health,
            HealthSnapshot.hotspot_health,
        ).order_by(HealthSnapshot.taken_at.asc(), HealthSnapshot.id.asc())
    )
    # Ascending order means the last write per repo wins.
    return [
        (
            repo_id,
            {
                "average_health": _rounded(average),
                "hotspot_health": _rounded(hotspot),
                "health_taken_at": taken_at,
            },
        )
        for repo_id, taken_at, average, hotspot in result.all()
    ]


_SECTIONS = (
    _file_figures,
    _page_figures,
    _dead_export_figures,
    _hotspot_figures,
    _health_figures,
)


async def _summary_rows_for(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Headline figures for every repo in one database: one grouped query per section.

    A missing table (never analysed, older store) zeroes that section instead of
    failing the dashboard, the same contract as ``routers/stats.py``.
    """
    out: dict[str, dict[str, Any]] = {}
    for section in _SECTIONS:
        with contextlib.suppress(SQLAlchemyError):
            for repo_id, figures in await section(session):
                out.setdefault(repo_id, {}).update(figures)
    return out


def _short(sha: str | None) -> str | None:
    return sha[:12] if sha else None


def _freshness_for(repo: RepoResponse) -> tuple[str | None, str | None, bool | None]:
    """(indexed commit, live HEAD, is the index behind) for one repo.

    Plain file reads, no git subprocess, so it is cheap per repo. ``index_behind``
    is ``None`` when either side is unknown, so "current" and "could not tell"
    stay distinct.
    """
    if not repo.local_path:
        return None, None, None
    indexed = resolve_indexed_commit(repo.head_commit, repo.local_path)
    live = read_live_head(repo.local_path)
    behind = indexed != live if indexed and live else None
    return _short(indexed), _short(live), behind
