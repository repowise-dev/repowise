"""Repo-local store access: database URL, read sessions, schema reconcile."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def get_db_url_for_repo(repo_path: Path) -> str:
    """Return a database URL for this repo.

    Prefers ``REPOWISE_DB_URL``, then the legacy ``REPOWISE_DATABASE_URL``.
    Otherwise defaults to the repo-local ``<repo>/.repowise/wiki.db``.
    """
    from repowise.core.persistence.database import resolve_db_url

    return resolve_db_url(repo_path)


STALE_INDEX_MESSAGE = (
    "This index was written by an older version of repowise; run `repowise update`."
)


class StaleIndexError(click.ClickException):
    """The store lacks a table or column this version reads, and could not be repaired."""

    def __init__(self) -> None:
        super().__init__(STALE_INDEX_MESSAGE)


@contextlib.asynccontextmanager
async def repo_index_session(root: Path) -> AsyncIterator[tuple[AsyncSession, str] | None]:
    """Open the repo-local store for reading, yielding ``(session, repo_id)`` or ``None``.

    A scoring or scanning command must never fail because the index is absent
    or locked, so every storage error yields ``None`` instead. The schema is
    reconciled only when a catalogue read finds it behind the models: the
    reconcile costs a read command 0.3 s and writes to the store, so a current
    store skips it. A store still behind after it raises :class:`StaleIndexError`,
    since every answer read from it would be missing what it lacks.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.cli import helpers as _facade  # patch point for tests
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import get_repository_by_path
    from repowise.core.persistence.database import (
        get_configured_db_url,
        has_db_store,
        schema_behind,
    )
    from repowise.core.persistence.models import Repository

    # The configured store, which may live outside the repo (REPOWISE_DB_URL).
    if not has_db_store(root):
        yield None
        return
    # The stack keeps the session open across the yield and disposes the engine
    # on the way out, whether the caller left the block or raised inside it.
    async with contextlib.AsyncExitStack() as stack:
        opened: tuple[AsyncSession, str] | None = None
        try:
            url = _facade.get_db_url_for_repo(root)
            engine = create_engine(url)
            stack.push_async_callback(engine.dispose)
            if await schema_behind(engine):
                await _facade.reconcile_schema_best_effort(url)
                if await schema_behind(engine):
                    raise StaleIndexError
            factory = create_session_factory(engine)
            session = await stack.enter_async_context(get_session(factory))
            repo = await get_repository_by_path(session, str(root))
            if repo is None and get_configured_db_url() is None:
                # A moved repo carries its local store but retains the old
                # local_path. Never infer identity in a configured/shared store
                # or when the local store contains more than one repository.
                repos = (await session.scalars(select(Repository).limit(2))).all()
                if len(repos) == 1:
                    repo = repos[0]
            if repo is not None:
                opened = (session, repo.id)
        except (SQLAlchemyError, OSError, LookupError):
            opened = None
        yield opened


async def adopt_moved_repo_best_effort(root: Path) -> bool:
    """Point a moved or copied checkout's only repository row at *root*.

    ``repowise update`` keys its repository row on ``local_path``. In a checkout
    that was moved or copied after ``init`` that key misses, and the update
    would insert a second row, which the MCP server then resolves to the old
    tree. Running this once, before any update write, rewrites the one row so
    every later lookup by path hits it. See
    :func:`repowise.core.persistence.crud.adopt_moved_repository` for the rule.

    Returns True when a row was rewritten. Never raises and never creates a
    store: a missing store, a locked one or a storage error leaves the update
    to behave exactly as it did before.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.cli import helpers as _facade  # patch point for tests
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import adopt_moved_repository, get_repository_by_path
    from repowise.core.persistence.database import has_db_store

    if not has_db_store(root):
        return False
    engine = None
    try:
        engine = create_engine(_facade.get_db_url_for_repo(root))
        factory = create_session_factory(engine)
        async with get_session(factory) as session:
            if await get_repository_by_path(session, str(root)) is not None:
                return False
            return await adopt_moved_repository(session, str(root)) is not None
    except (SQLAlchemyError, OSError):
        return False
    finally:
        if engine is not None:
            await engine.dispose()


#: Busy timeout for the reconcile's own connection. The engine default is 30s,
#: sized for bulk graph-edge writes; inheriting it here is a trap, because a
#: read command opens several engines (``status`` four, ``doctor`` five) and a
#: store locked by a concurrent ``repowise update`` would stall each one in
#: turn — measured at 133s of silence for ``status`` before this was bounded.
#: The reconcile is a best-effort secondary write, so it takes the same lever
#: issue #326 gave the cost tracker: fail fast and be dropped rather than block.
_RECONCILE_BUSY_TIMEOUT_MS = 2000


async def reconcile_schema_best_effort(db_url: str) -> None:
    """Back-fill additive schema drift on the store at *db_url*, best-effort.

    An index built by an older repowise is missing whatever columns the models
    have gained since, and the ORM then fails with a raw ``no such column`` on
    the first query — which for a read command means every read. ``init_db``
    back-fills those columns in place and is idempotent, so the CLI pairs it
    with ``create_engine`` where a command opens a store, the way the MCP
    server, the workspace registry and the FastAPI app already do in their
    lifespans. :func:`repo_index_session` runs it only on a store found behind.

    Opportunistic, not a precondition, which is the part worth having in one
    place. Reconciling needs a write, and a store can be read-only or
    exclusively locked by a concurrent ``repowise update``. Aborting there would
    be a regression twice over: a store already on the current schema needs no
    DDL and would have read fine, and where the drift is real the following
    query fails with the ``no such column`` that the failure shield turns into
    "this index predates the installed repowise, run repowise update" — a
    better answer than whichever write error stopped the repair.

    Takes a URL rather than an engine so the repair runs on its own short-timeout
    connection: the caller's engine keeps the full 30s window for the work it was
    opened to do, and a contended repair gives up in two seconds instead of
    stalling the command behind it.

    Never CREATES a store. ``init_db`` on a fresh path would materialise a
    42-table file, so a read command run in a repo that was never indexed would
    leave a database behind where the user expects "not indexed yet".
    """
    from repowise.core.persistence import create_engine, init_db

    if _missing_sqlite_file(db_url):
        return

    engine = create_engine(db_url, busy_timeout_ms=_RECONCILE_BUSY_TIMEOUT_MS)
    try:
        with contextlib.suppress(Exception):
            await init_db(engine)
    finally:
        with contextlib.suppress(Exception):
            await engine.dispose()


def _missing_sqlite_file(db_url: str) -> bool:
    """True when *db_url* names a SQLite file that does not exist yet.

    Only SQLite is checked: a server-backed URL has no file to create, and the
    caller is not the component that decides whether that database should exist.
    """
    if not db_url.startswith("sqlite"):
        return False
    _, sep, tail = db_url.partition(":///")
    if not sep or not tail or tail.startswith(":memory:"):
        return False
    return not Path(tail).exists()


def db_configured() -> bool:
    """True when ``REPOWISE_DB_URL`` or ``REPOWISE_DATABASE_URL`` is set.

    The DB may still be the repo-local ``wiki.db`` default — the file
    existence check the callers pair this with decides that.
    """
    from repowise.core.persistence import get_configured_db_url

    return get_configured_db_url() is not None
