"""A repository's local checkout, for routes that run git against it."""

from __future__ import annotations

import os
import subprocess

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.models import Repository
from repowise.server.deps import get_db_session


async def resolve_local_repo(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> Repository:
    """Resolve a repository with a usable local checkout, or raise 404."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None or not repo.local_path or not os.path.isdir(repo.local_path):
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


def revision_exists(repo_path: str, rev: str) -> bool:
    # Reject option-shaped input outright; git refuses ref names starting
    # with "-", so this loses no legitimate revision and keeps user input
    # from ever being parsed as a git flag here or downstream.
    if not rev or rev.startswith("-"):
        return False
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
