"""Repo-local database session helpers shared across CLI commands.

Collapses the ``get_db_url_for_repo → create_engine → init_db →
create_session_factory → upsert_repository`` dance that several CLI commands
repeat before doing per-repo database work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


async def open_repo_db(
    repo_path: Path,
    *,
    repo_name: str | None = None,
    init: bool = True,
) -> tuple[Any, Any, str]:
    """Open the repo-local database and ensure the repository row exists.

    Returns ``(engine, session_factory, repo_id)``. The engine is **not**
    disposed — the caller owns its lifetime (cost trackers keep it open for the
    duration of generation; persistence paths dispose it when done).
    """
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    if init:
        await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo = await upsert_repository(
            session, name=repo_name or repo_path.name, local_path=str(repo_path)
        )
        repo_id = repo.id
    return engine, sf, repo_id
