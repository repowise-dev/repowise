"""Async database engine and session factory for repowise.

Supports two backends:
- SQLite (default): sqlite+aiosqlite:///path/to/file.db
- PostgreSQL:       postgresql+asyncpg://user:pass@host/dbname

Call get_db_url() to normalise raw URLs (adds the async driver prefix).
Call create_engine() to create an AsyncEngine.
Call init_db() once at startup to create all tables and the FTS index.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool
from sqlalchemy.schema import CreateIndex
from sqlalchemy.sql import text

from .models import Base

log = structlog.get_logger(__name__)

# SQLite tuning. WAL allows concurrent readers while a writer is active and
# turns the "database is locked" failure mode into a polite block, while the
# busy_timeout gives that block a bounded retry window before giving up.
# Foreign keys are off by default in SQLite for legacy reasons; we want them on
# everywhere so our FK-driven cascades behave the same in tests and production.
# 30s gives heavy bulk writes (e.g. persisting tens of thousands of graph
# edges in one transaction) enough headroom to finish before a concurrent
# progress-callback write raises "database is locked". 5s was too tight
# for large repos. SQLite blocks (doesn't busy-loop) so this is cheap.
_SQLITE_BUSY_TIMEOUT_MS = 30000

def _sqlite_pragmas(busy_timeout_ms: int) -> tuple[tuple[str, str], ...]:
    """Return the pragma list to apply to a SQLite connection.

    ``busy_timeout`` leads so that every pragma and statement after it on this
    connection inherits the retry window, rather than the window arriving only
    once the connection is most of the way set up.
    """
    return (
        ("busy_timeout", str(busy_timeout_ms)),
        ("journal_mode", "WAL"),
        ("synchronous", "NORMAL"),
        ("foreign_keys", "ON"),
    )


def _make_pragma_listener(busy_timeout_ms: int):
    """Build a ``connect`` event listener that applies our SQLite pragmas.

    The listener is a closure so the ``busy_timeout`` can be tuned per engine.
    Most engines use the default 30s headroom (needed for bulk graph-edge
    writes), but short-lived best-effort writers — e.g. the cost tracker's
    secondary engine — pass a small timeout so a contended write fails fast and
    is dropped instead of stalling the primary writer for the full window
    (issue #326).
    """

    # Log the first failed switch per engine, not every connection: this engine
    # uses NullPool, so it reconnects per checkout and a store that cannot take
    # WAL would otherwise emit a warning per query. Behaviour does not depend on
    # this flag - every connection still attempts the switch.
    warned = False

    def _set_journal_mode_wal(cursor: object) -> None:
        """Re-issue the WAL switch, but never let it fail the connection.

        The re-issue is defensive: WAL persists in the file, so a store this
        repowise created is already in WAL and the pragma is a no-op that
        cannot contend. It exists only for a store written by an older
        repowise, by ``alembic``, or on a filesystem that refused the first
        switch - and there it can legitimately fail:

        * a concurrent writer holds the brief exclusive lock the transition
          needs, and SQLite does NOT route that lock through the busy handler,
          so it returns SQLITE_BUSY immediately however large ``busy_timeout``
          is;
        * the store or its directory is read-only, giving "attempt to write a
          readonly database".

        None of those stop the connection being useful. The store keeps the
        journal mode it already has and every query still runs, so the failure
        is swallowed here and left to surface on a statement that actually
        needs the write. What shipped before raised out of the ``connect``
        event and took out the whole connection at open time, including reads
        that would have succeeded.

        Deliberately not retried. The listener runs on the event-loop thread
        under SQLAlchemy's greenlet bridge, so sleeping here blocks the loop -
        and when the writer is another task in this same process, that stops it
        reaching the COMMIT the retry is waiting on. The next connection tries
        again at no cost instead.
        """
        nonlocal warned
        try:
            # journal_mode returns the new mode and must be queried, not
            # assigned, because in :memory: databases it silently
            # downgrades to MEMORY.
            cursor.execute("PRAGMA journal_mode=WAL")  # type: ignore[attr-defined]
        except sqlite3.OperationalError as exc:
            if not warned:
                warned = True
                log.warning(
                    "sqlite: could not switch the store to WAL (%s). Continuing "
                    "in its current journal mode; concurrent reads may block on "
                    "writes until a later connection switches it.",
                    exc,
                )

    def _apply_sqlite_pragmas(dbapi_connection: object, _connection_record: object) -> None:
        """Apply busy_timeout, WAL, and FK pragmas on every new SQLite connection.

        Registered as a ``connect`` event listener so it runs once per physical
        connection, including the first one opened after the engine is created and
        every reconnect afterward. WAL is a database-level setting that persists in
        the file, but we re-issue it defensively in case the file was created by an
        older repowise version, by ``alembic``, or by a third-party tool that left
        journal_mode at the default ``delete``.
        """
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            for name, value in _sqlite_pragmas(busy_timeout_ms):
                if name == "journal_mode":
                    _set_journal_mode_wal(cursor)
                    continue
                cursor.execute(f"PRAGMA {name}={value}")
        finally:
            cursor.close()

    return _apply_sqlite_pragmas


__all__ = [
    "AsyncEngine",
    "AsyncSession",
    "async_sessionmaker",
    "create_engine",
    "create_session_factory",
    "get_configured_db_url",
    "get_db_url",
    "get_repo_db_path",
    "get_session",
    "init_db",
    "resolve_db_url",
]

DB_FILENAME = "wiki.db"
REPOWISE_DIRNAME = ".repowise"
DB_ENV_VARS = ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL")


def get_repo_db_path(repo_path: str | Path) -> Path:
    """Return the repo-local database path ``<repo>/.repowise/wiki.db``."""
    return Path(repo_path).resolve() / REPOWISE_DIRNAME / DB_FILENAME


def _default_db_url(repo_path: str | Path | None = None) -> str:
    """Return the default SQLite URL for a repo-local or global wiki database.

    Always creates the parent directory to prevent sqlite crashes.
    """
    if repo_path is not None:
        db_path = get_repo_db_path(repo_path)
    else:
        db_path = Path.home() / REPOWISE_DIRNAME / DB_FILENAME
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{db_path.as_posix()}"


def get_db_url(raw_url: str | None = None) -> str:
    """Normalise a database URL to include the async driver prefix.

    - ``sqlite:///...``      → ``sqlite+aiosqlite:///...``
    - ``postgresql://...``   → ``postgresql+asyncpg://...``
    - ``postgresql+psycopg://...`` → unchanged (explicit driver wins)
    - Already async-prefixed URLs are returned as-is.
    - ``None`` → global default: ``~/.repowise/wiki.db``
    """
    if raw_url is None:
        return _default_db_url()

    url = raw_url.strip()

    if url.startswith("sqlite://") and "aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return url.replace("://", "+asyncpg://", 1)

    return url


def get_configured_db_url() -> str | None:
    """Return the configured DB URL from supported env vars, if present."""
    for env_name in DB_ENV_VARS:
        env_url = os.environ.get(env_name)
        if env_url:
            return get_db_url(env_url)
    return None


def resolve_db_url(repo_path: str | Path | None = None) -> str:
    """Resolve the active DB URL from env vars or the default filesystem path.

    Resolution order:
    1. ``REPOWISE_DB_URL``
    2. ``REPOWISE_DATABASE_URL`` (legacy compatibility)
    3. ``<repo>/.repowise/wiki.db`` when *repo_path* is provided
    4. ``~/.repowise/wiki.db`` otherwise
    """
    configured = get_configured_db_url()
    if configured is not None:
        return configured
    return _default_db_url(repo_path)


def create_engine(
    url: str | None = None,
    *,
    echo: bool = False,
    # StaticPool is required for :memory: SQLite so all connections share the same DB.
    # Pass use_static_pool=True explicitly when creating in-memory test engines.
    use_static_pool: bool = False,
    busy_timeout_ms: int | None = None,
) -> AsyncEngine:
    """Create an AsyncEngine for the given database URL.

    Args:
        url:             Raw or async-prefixed database URL.  Defaults to SQLite.
        echo:            Log all SQL statements (useful for debugging).
        use_static_pool: Force StaticPool (required for in-memory SQLite tests).
        busy_timeout_ms: Override the SQLite ``busy_timeout`` (milliseconds) for
                         connections from this engine. Defaults to 30s. Pass a
                         small value for best-effort secondary writers that must
                         never stall the primary writer (issue #326). Ignored
                         for non-SQLite backends.
    """
    db_url = get_db_url(url)
    is_sqlite = db_url.startswith("sqlite")

    kwargs: dict = {"echo": echo}

    if is_sqlite:
        # SQLite requires check_same_thread=False for multi-threaded async use
        kwargs["connect_args"] = {"check_same_thread": False}
        if use_static_pool or ":memory:" in db_url:
            # StaticPool: all connect() calls return the same connection.
            # Mandatory for in-memory SQLite — without it each call gets a fresh DB.
            kwargs["poolclass"] = StaticPool
        else:
            kwargs["poolclass"] = NullPool
    else:
        # PostgreSQL — asyncpg handles its own connection pool
        kwargs["pool_pre_ping"] = True

    engine = create_async_engine(db_url, **kwargs)
    if is_sqlite:
        # The ``connect`` event fires for the underlying DBAPI connection, so we
        # listen on the sync engine that backs the AsyncEngine.
        timeout = busy_timeout_ms if busy_timeout_ms is not None else _SQLITE_BUSY_TIMEOUT_MS
        event.listen(engine.sync_engine, "connect", _make_pragma_listener(timeout))
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return an async_sessionmaker bound to *engine*.

    expire_on_commit=False: prevents SQLAlchemy from expiring attributes after
    commit, which would require a sync lazy-load (impossible in async context).
    """
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Async context manager that yields a session and handles commit/rollback."""
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _column_default_sql(column: object) -> str | None:
    """Return a SQL literal/expression suitable for an ADD COLUMN DEFAULT.

    Prefers ``server_default`` (the DDL-level default that the migration
    chain writes); falls back to a static Python ``default=`` value so that
    a model that only declares a Python-side default still gets back-filled
    correctly on legacy tables. Callable Python defaults can't be rendered
    to DDL and are skipped — caller handles the no-default branch.
    """
    server_default = getattr(column, "server_default", None)
    if server_default is not None:
        arg = getattr(server_default, "arg", server_default)
        # TextClause / FetchedValue / func.now() — pass through their string
        # representation so dialect-native constructs survive.
        if isinstance(arg, str):
            escaped = arg.replace("'", "''")
            return f"'{escaped}'"
        return str(arg)

    py_default = getattr(column, "default", None)
    if py_default is not None:
        arg = getattr(py_default, "arg", None)
        if arg is not None and not callable(arg):
            if isinstance(arg, bool):
                return "1" if arg else "0"
            if isinstance(arg, (int, float)):
                return str(arg)
            if isinstance(arg, str):
                escaped = arg.replace("'", "''")
                return f"'{escaped}'"
    return None


def _add_column_ddl(column: object, dialect: object) -> str:
    """Render the column-definition fragment for ``ALTER TABLE ADD COLUMN``.

    We build this by hand instead of using ``CreateColumn(column).compile()``
    because the latter doesn't emit a DEFAULT clause for Python-side
    ``default=`` values — and a NOT NULL column without a DDL default cannot
    be back-filled onto a populated table. Synthesizing the default from the
    Python default here means the reconciler works even on models whose
    server_default was forgotten (a common drift between model and migration).
    """
    parts = [
        f'"{column.name}"',  # type: ignore[attr-defined]
        column.type.compile(dialect=dialect),  # type: ignore[attr-defined]
    ]
    default_sql = _column_default_sql(column)
    if default_sql is not None:
        parts.append(f"DEFAULT {default_sql}")
    if not column.nullable:  # type: ignore[attr-defined]
        parts.append("NOT NULL")
    return " ".join(parts)


def _reconcile_schema(connection: object) -> None:
    """Bring an existing database up to ``Base.metadata`` (additive only).

    ``Base.metadata.create_all`` creates tables that are missing but never
    touches tables that already exist — so a user upgrading repowise across
    a release that *added a column* to an existing table will see runtime
    failures like ``no such column: decision_records.verification`` until
    the column is added by hand.

    This function closes that gap generically. It walks every table in
    ``Base.metadata`` and, for each table that already exists in the
    database, issues additive DDL for any **columns** or **indexes**
    declared on the model but missing in the live schema. The reconciler
    is driven entirely by the model definition, so any future migration
    that follows the additive-only convention is picked up automatically
    on the next ``init_db`` call — no per-migration code required here.

    Limitations (intentional — these need explicit migrations):
      * column **removals**, **renames**, or **type changes** are NOT
        reconciled (SQLite can't ALTER COLUMN safely anyway);
      * **constraint changes** (UNIQUE, CHECK, FK) on existing columns
        are NOT reconciled;
      * Postgres extensions / functions (e.g. pgvector) are NOT created
        here — those still belong in Alembic migrations.

    Uses a sync connection (via ``run_sync``) so the SQLAlchemy DDL compilers
    work directly.

    **Not atomic on SQLite, deliberately convergent instead.** ``init_db``
    opens a transactional block, but pysqlite does not begin a transaction for
    DDL, so every ``ALTER TABLE`` and ``CREATE INDEX`` here autocommits as it
    runs. A statement that fails therefore cannot be rolled back, and aborting
    the loop would strand every table ordered after the victim in
    ``Base.metadata`` on that call and on every later one: a permanently
    half-migrated store. So a failed statement is recorded and the walk
    continues, and the first failure is re-raised once the walk is done. Two
    properties follow, both wanted:

      * calling again makes progress, because the tables after the victim are
        reached on the first call rather than never;
      * the caller still sees the error, so a write path fails as loudly as it
        did before. The read paths pair this with
        ``reconcile_schema_best_effort``, which swallows it on purpose.

    On a backend with transactional DDL (PostgreSQL) the first failure poisons
    the transaction, so continuing is pointless and the error is raised at
    once, which keeps that backend's existing all-or-nothing behaviour.
    """
    inspector = inspect(connection)
    db_tables = set(inspector.get_table_names())
    dialect = connection.dialect  # type: ignore[attr-defined]
    # Only SQLite autocommits DDL, which is what makes partial progress real
    # rather than something a rollback undoes on the way out.
    continue_past_failure = dialect.name == "sqlite"  # type: ignore[attr-defined]
    failures: list[tuple[str, Exception]] = []

    def _run(what: str, build: Callable[[], object]) -> None:
        # ``build`` renders the statement as well as running it, because
        # compiling a column's type can fail on its own and that failure has
        # to strand no more than compiling it successfully and failing to
        # execute it would.
        try:
            connection.execute(build())  # type: ignore[attr-defined]
        except Exception as exc:  # re-raised below, once the walk is done
            if not continue_past_failure:
                raise
            log.warning(
                "schema_reconcile_statement_failed",
                statement=what,
                error=str(exc),
            )
            failures.append((what, exc))

    for table in Base.metadata.tables.values():
        if table.name not in db_tables:
            # ``create_all`` (already run by init_db) creates missing
            # tables with the full current schema, so we never need to
            # reconcile a brand-new table column-by-column.
            continue

        # --- Columns ---------------------------------------------------
        # Render the column DDL fragment exactly as SQLAlchemy would emit
        # it inside CREATE TABLE — preserves type, server_default, and
        # nullability. We deliberately do NOT enforce FK constraints on
        # back-filled columns: SQLite can't add an enforced FK after the
        # fact, and write-time enforcement is sufficient for our purposes.
        db_cols = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in db_cols:
                continue
            _run(
                f"{table.name}.{column.name}",
                lambda table=table, column=column: text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN '
                    f"{_add_column_ddl(column, dialect)}"
                ),
            )

        # --- Indexes ---------------------------------------------------
        # Only model-declared indexes (i.e. ``Index(...)`` on the table
        # or ``index=True`` on a column) are reconciled. Indexes created
        # by hand in Alembic migrations live on tables we own, so they
        # show up here too once the model is updated to declare them.
        db_indexes = {idx["name"] for idx in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name in db_indexes:
                continue
            _run(
                f"{table.name}:{index.name}",
                lambda index=index: CreateIndex(index),
            )

    if failures:
        what = ", ".join(name for name, _ in failures)
        log.warning(
            "schema_reconcile_incomplete",
            failed=len(failures),
            statements=what,
        )
        raise failures[0][1]


async def init_db(engine: AsyncEngine) -> None:
    """Create all SQLAlchemy tables and the FTS index for the given engine.

    Also reconciles additive schema drift on legacy databases — a user who
    indexed a repo with an older repowise and then upgrades will have any
    missing columns/indexes back-filled in place rather than hitting a
    cryptic ``no such column`` error from the ORM.

    Safe to call on an already-initialised database (idempotent).
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_reconcile_schema)

        # SQLite-only: create FTS5 virtual table for full-text search.
        # PostgreSQL uses a GIN index added by the Alembic migration.
        #
        # The statement comes from ``search.py`` rather than being written out
        # again here. A second copy of the DDL is how a widened index ends up
        # only half applied: fresh stores would be born on the old column set
        # and then be rebuilt by ``FullTextSearch.ensure_index`` on first use.
        if engine.dialect.name == "sqlite":
            from repowise.core.persistence.search import PAGE_FTS_DDL

            await conn.execute(text(PAGE_FTS_DDL))
