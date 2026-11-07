"""Repo-local database session helpers shared across CLI commands.

Collapses the ``get_db_url_for_repo → create_engine → init_db →
create_session_factory → upsert_repository`` dance that several CLI commands
repeat before doing per-repo database work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def unopenable_store_error(url: str, exc: BaseException):
    """A CLI error naming the recovery for a store that would not open.

    The root group renders a ``ClickException`` as ``Error: <message>`` with
    no traceback, so a corrupt store fails with its recovery instead of a
    stack trace. The ``reason`` slug keeps the failure classifiable.
    """
    from repowise.cli.errors import reasoned_error

    db_path = url.split(":///", 1)[-1] if ":///" in url else url
    return reasoned_error(
        f"Cannot open the index at {db_path}: {exc}. "
        "Remove wiki.db, wiki.db-wal and wiki.db-shm, "
        "then run repowise init to rebuild it.",
        reason="store-unreadable",
    )


async def open_repo_db(
    repo_path: Path,
    *,
    repo_name: str | None = None,
    init: bool = True,
    busy_timeout_ms: int | None = None,
) -> tuple[Any, Any, str]:
    """Open the repo-local database and ensure the repository row exists.

    Returns ``(engine, session_factory, repo_id)``. The engine is **not**
    disposed — the caller owns its lifetime (cost trackers keep it open for the
    duration of generation; persistence paths dispose it when done).

    ``busy_timeout_ms`` overrides the SQLite ``busy_timeout`` for this engine —
    used by best-effort secondary writers (the cost tracker) that must fail
    fast under contention rather than stall the primary writer (issue #326).
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
    engine = create_engine(url, busy_timeout_ms=busy_timeout_ms)
    try:
        if init:
            await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(
                session, name=repo_name or repo_path.name, local_path=str(repo_path)
            )
            repo_id = repo.id
    except Exception as exc:
        # The caller never receives the engine on this path, so it cannot
        # dispose it: a store that cannot be opened used to surface here as
        # a traceback while the undisposed engine's aiosqlite worker thread
        # kept the command alive. Dispose first, then fail with the recovery.
        await engine.dispose()
        import sqlite3

        from sqlalchemy.exc import DatabaseError

        if isinstance(exc, (DatabaseError, sqlite3.Error)):
            raise unopenable_store_error(url, exc) from exc
        raise
    return engine, sf, repo_id
