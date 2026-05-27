"""Shared dependencies and small helpers for the /api/graph sub-routers."""

from __future__ import annotations

import json

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.models import GraphEdge, Page, Repository
from repowise.server.deps import get_db_session
from repowise.server.schemas import GraphEdgeResponse


async def with_repo(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Repository:
    """Resolve the repository or raise 404.

    Used as a FastAPI dependency so every graph endpoint shares the same
    fetch-then-404 guard instead of repeating it inline. FastAPI caches the
    ``get_db_session`` dependency within a request, so the endpoint body sees
    the same session instance.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


def _escape_like(s: str) -> str:
    """Escape special characters for SQL LIKE patterns."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_imported_names(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def _edge_response(e: GraphEdge) -> GraphEdgeResponse:
    """Build a GraphEdgeResponse from a GraphEdge ORM row."""
    return GraphEdgeResponse(
        source=e.source_node_id,
        target=e.target_node_id,
        imported_names=_parse_imported_names(e.imported_names_json),
    )


async def _get_documented_paths(session: AsyncSession, repo_id: str) -> set[str]:
    """Return the set of node_ids (file paths) that have a wiki page."""
    result = await session.execute(select(Page.target_path).where(Page.repository_id == repo_id))
    return {row.target_path for row in result.all() if row.target_path}
