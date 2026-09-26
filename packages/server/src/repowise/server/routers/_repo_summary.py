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


def _fresh_case(column: Any, value: Any) -> Any:
    """Portable conditional count. ``count(...) FILTER (WHERE ...)`` needs
    SQLite 3.30+ and this project ships no version floor, so every conditional
    count in the codebase is a ``sum(case(...))`` — see ``routers/git.py``."""
    return func.coalesce(func.sum(case((column == value, 1), else_=0)), 0)


async def _summary_rows_for(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Headline figures for every repo in one database, five queries total.

    Grouped by ``repository_id`` rather than filtered per repo: the route this
    replaces ran six queries *per repository* for the stats alone, and
    ``/git-summary`` hydrated every ``git_metadata`` row (one per file, ~3.5k on
    this repo) to produce two integers.

    A table that does not exist yet — a repo registered but never analysed, an
    older store — degrades that section to zero rather than 500-ing the whole
    dashboard, which is the same contract ``routers/stats.py`` documents.
    """
    out: dict[str, dict[str, Any]] = {}

    def row_for(repo_id: str) -> dict[str, Any]:
        return out.setdefault(repo_id, {})

    # Files, symbols and entry points. `graph_nodes` holds symbol rows in the
    # same table, so every count here is scoped to `node_type == "file"`; the
    # unscoped count is what makes /stats report 38,813 "files" for 3,600.
    with contextlib.suppress(SQLAlchemyError):
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
        for repo_id, files, symbols, entries in result.all():
            row_for(repo_id).update(
                file_count=int(files or 0),
                symbol_count=int(symbols or 0),
                entry_point_count=int(entries or 0),
            )

    # Documentation pages and the fresh subset. Never selects `content`.
    with contextlib.suppress(SQLAlchemyError):
        result = await session.execute(
            select(
                Page.repository_id,
                func.count(Page.id),
                _fresh_case(Page.freshness_status, "fresh"),
            ).group_by(Page.repository_id)
        )
        for repo_id, pages, fresh in result.all():
            row_for(repo_id).update(
                doc_page_count=int(pages or 0),
                doc_fresh_page_count=int(fresh or 0),
            )

    # Open unused exports — the one dead-code figure the dashboard quotes.
    with contextlib.suppress(SQLAlchemyError):
        result = await session.execute(
            select(DeadCodeFinding.repository_id, func.count(DeadCodeFinding.id))
            .where(
                DeadCodeFinding.kind == "unused_export",
                DeadCodeFinding.status == "open",
            )
            .group_by(DeadCodeFinding.repository_id)
        )
        for repo_id, dead in result.all():
            row_for(repo_id).update(dead_export_count=int(dead or 0))

    # Hotspots, and the tracked-file denominator they are meaningful against.
    with contextlib.suppress(SQLAlchemyError):
        result = await session.execute(
            select(
                GitMetadata.repository_id,
                func.count(GitMetadata.id),
                _fresh_case(GitMetadata.is_hotspot, True),
            ).group_by(GitMetadata.repository_id)
        )
        for repo_id, tracked, hotspots in result.all():
            row_for(repo_id).update(
                tracked_file_count=int(tracked or 0),
                hotspot_count=int(hotspots or 0),
            )

    # Latest health snapshot per repo. Three scalar columns only: a snapshot
    # row carries `per_file_scores_json`, ~186 KB apiece, and selecting the
    # entity would pull the whole retained history's worth of it for two
    # floats (see crud.get_health_snapshot_headline's docstring). Reduced in
    # Python rather than with a window function, because retention bounds the
    # row count to tens per repo and window syntax is not uniform across the
    # two supported backends.
    with contextlib.suppress(SQLAlchemyError):
        result = await session.execute(
            select(
                HealthSnapshot.repository_id,
                HealthSnapshot.taken_at,
                HealthSnapshot.average_health,
                HealthSnapshot.hotspot_health,
            ).order_by(HealthSnapshot.taken_at.asc(), HealthSnapshot.id.asc())
        )
        for repo_id, taken_at, average, hotspot in result.all():
            # Ascending order means the last write per repo wins.
            row_for(repo_id).update(
                average_health=round(float(average), 2) if average is not None else None,
                hotspot_health=round(float(hotspot), 2) if hotspot is not None else None,
                health_taken_at=taken_at,
            )

    return out


def _freshness_for(repo: RepoResponse) -> tuple[str | None, str | None, bool | None]:
    """(indexed commit, live HEAD, is the index behind) for one repo.

    Both reads are plain file I/O — `read_live_head` parses `.git/HEAD` and
    follows at most one ref rather than spawning git — so this stays cheap
    enough to run per repo on a page load. Returns ``None`` for
    ``index_behind`` when either side is unavailable, so "current" and
    "could not tell" never collapse into the same answer.
    """
    if not repo.local_path:
        return None, None, None
    indexed = resolve_indexed_commit(repo.head_commit, repo.local_path)
    live = read_live_head(repo.local_path)
    if not indexed or not live:
        return (indexed[:12] if indexed else None), (live[:12] if live else None), None
    return indexed[:12], live[:12], indexed != live
