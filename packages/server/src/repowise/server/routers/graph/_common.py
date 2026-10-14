"""Shared dependencies and small helpers for the /api/graph sub-routers."""

from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.models import Repository
from repowise.server.deps import get_db_session


async def with_repo(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
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
