"""The workspace store: ``.repowise-workspace/workspace.db``.

A derived SQLite file holding the cross-repo rows a workspace query needs in
one lookup: which repo has a path or a symbol, each repo's components, recent
change summaries, health and index freshness. Each repo's own ``wiki.db`` stays
the source of truth; every row here is rebuilt from it, so the file can be
deleted at any time. A missing, corrupt or older-schema file is rebuilt.

A repo's rows are refreshed when its stamp moves. The stamp is the repo's
``.repowise/state.json`` (written by every index run) plus whether ``wiki.db``
exists, so serving reads never invalidate it. Refreshes happen after a
workspace update (:func:`repowise.core.workspace.update.run_cross_repo_hooks`)
and lazily when a reader finds a stamp moved.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repowise.core.sqlite_pragmas import apply_sqlite_pragmas

from .config import WORKSPACE_DATA_DIR

_log = logging.getLogger("repowise.workspace.store")

STORE_FILENAME = "workspace.db"
#: Bump on any table change; a file with another version is rebuilt.
SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MS = 5000
#: Change summaries kept per repo, newest first. Ceiling: older changes stay
#: in the repo's own ``changes`` table; raise this when a reader needs more.
CHANGES_PER_REPO = 200

_SCHEMA = """
CREATE TABLE repos (
    alias TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    stamp TEXT NOT NULL,
    indexed INTEGER NOT NULL,
    indexed_commit TEXT,
    index_updated_at TEXT,
    refreshed_at TEXT NOT NULL,
    health_json TEXT,
    error TEXT
);
CREATE TABLE files (
    alias TEXT NOT NULL,
    path TEXT NOT NULL,
    PRIMARY KEY (path, alias)
) WITHOUT ROWID;
CREATE INDEX ix_files_alias ON files (alias);
CREATE TABLE symbols (
    alias TEXT NOT NULL,
    symbol_id TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    file_path TEXT NOT NULL
);
CREATE INDEX ix_symbols_name ON symbols (name);
CREATE INDEX ix_symbols_qualified ON symbols (qualified_name);
CREATE INDEX ix_symbols_alias ON symbols (alias);
CREATE TABLE components (
    alias TEXT NOT NULL,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    owner TEXT,
    system TEXT,
    criticality TEXT,
    tags_json TEXT NOT NULL,
    file_count INTEGER NOT NULL,
    PRIMARY KEY (alias, name)
);
CREATE TABLE changes (
    alias TEXT NOT NULL,
    change_id TEXT NOT NULL,
    merged_at TEXT,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    files_changed INTEGER NOT NULL,
    lines_added INTEGER NOT NULL,
    lines_deleted INTEGER NOT NULL,
    provenance TEXT,
    PRIMARY KEY (alias, change_id)
);
"""
_TABLES = ("files", "symbols", "components", "changes")


def store_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / WORKSPACE_DATA_DIR / STORE_FILENAME


def repo_stamp(repo_path: Path) -> str:
    """What must move for a repo's rows to be re-read."""
    from .registry import repo_db_path

    try:
        st = (repo_path / ".repowise" / "state.json").stat()
        state = f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        state = "-"
    return f"{state}|{int(repo_db_path(repo_path).is_file())}"


@dataclass
class RepoRows:
    """Everything the store holds for one repo, read from its ``wiki.db``."""

    indexed: bool = False
    indexed_commit: str | None = None
    index_updated_at: str | None = None
    health: dict[str, Any] | None = None
    files: list[str] = field(default_factory=list)
    symbols: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    components: list[tuple[str, str, str | None, str | None, str | None, str, int]] = field(
        default_factory=list
    )
    changes: list[tuple[str, str | None, str, str, int, int, int, str | None]] = field(
        default_factory=list
    )
    error: str | None = None


class WorkspaceStore:
    """One open connection to the workspace store; the caller closes it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = self._open()
        except sqlite3.DatabaseError as exc:
            if not _corrupt(exc):
                raise
            _log.warning("Workspace store at %s is unreadable; rebuilding it", path)
            discard(path)
            self._conn = self._open()

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None)
        try:
            apply_sqlite_pragmas(conn, _BUSY_TIMEOUT_MS)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                with _transaction(conn):
                    for (table,) in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall():
                        conn.execute(f'DROP TABLE "{table}"')
                    for statement in _SCHEMA.split(";"):
                        if statement.strip():
                            conn.execute(statement)
                    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        except BaseException:
            conn.close()
            raise
        return conn

    def close(self) -> None:
        self._conn.close()

    def stamps(self) -> dict[str, tuple[str, str]]:
        """``alias -> (path, stamp)`` for every repo the store holds."""
        rows = self._conn.execute("SELECT alias, path, stamp FROM repos")
        return {alias: (path, stamp) for alias, path, stamp in rows}

    def replace_repo(self, alias: str, path: str, stamp: str, rows: RepoRows) -> None:
        """Swap one repo's rows for *rows* in one transaction."""
        conn = self._conn
        with _transaction(conn):
            self._delete(alias)
            conn.execute(
                "INSERT INTO repos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    alias,
                    path,
                    stamp,
                    int(rows.indexed),
                    rows.indexed_commit,
                    rows.index_updated_at,
                    datetime.now(UTC).isoformat(),
                    json.dumps(rows.health) if rows.health is not None else None,
                    rows.error,
                ),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO files VALUES (?, ?)", ((alias, f) for f in rows.files)
            )
            conn.executemany(
                "INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?)", ((alias, *s) for s in rows.symbols)
            )
            conn.executemany(
                "INSERT OR IGNORE INTO components VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ((alias, *c) for c in rows.components),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO changes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ((alias, *c) for c in rows.changes),
            )

    def drop_repos(self, aliases: Iterable[str]) -> None:
        with _transaction(self._conn):
            for alias in aliases:
                self._delete(alias)

    def _delete(self, alias: str) -> None:
        for table in (*_TABLES, "repos"):
            self._conn.execute(f"DELETE FROM {table} WHERE alias = ?", (alias,))


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _corrupt(exc: sqlite3.DatabaseError) -> bool:
    """Not a database, a malformed one, or one missing its tables. Locks and
    integrity errors are ``DatabaseError`` subclasses too and say nothing about
    the file."""
    if isinstance(exc, sqlite3.OperationalError):
        return str(exc).startswith("no such table")
    return type(exc) is sqlite3.DatabaseError


def discard(path: Path) -> None:
    """Delete the store file and its WAL sidecars; the next open rebuilds it."""
    for suffix in ("", "-wal", "-shm"):
        with suppress(OSError):
            Path(f"{path}{suffix}").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Reading a repo's index
# ---------------------------------------------------------------------------


async def read_repo_rows(alias: str, repo_path: Path, services: list[tuple[str, str]]) -> RepoRows:
    """Read one repo's store rows from its ``wiki.db``; never raises."""
    from .registry import open_repo_db, repo_db_path

    if not repo_db_path(repo_path).is_file():
        return RepoRows()
    try:
        engine, session_factory = await open_repo_db(repo_path)
    except Exception as exc:
        _log.warning("Workspace store: could not open the index of %r", alias, exc_info=True)
        return RepoRows(error=f"{type(exc).__name__}: {exc}"[:200])
    try:
        async with session_factory() as session:
            return await _read(session, repo_path, services)
    except Exception as exc:
        _log.warning("Workspace store: could not read the index of %r", alias, exc_info=True)
        return RepoRows(error=f"{type(exc).__name__}: {exc}"[:200])
    finally:
        await engine.dispose()


async def _read(session: Any, repo_path: Path, services: list[tuple[str, str]]) -> RepoRows:
    from sqlalchemy import select

    from repowise.core.components.store import load_repo_components
    from repowise.core.persistence.models import Change, GraphNode, HealthSnapshot, WikiSymbol

    from .repo_index import checkout_repository
    from .update import resolve_indexed_commit

    repo = await checkout_repository(session, repo_path)
    if repo is None:
        return RepoRows()
    rows = RepoRows(
        indexed=True,
        indexed_commit=resolve_indexed_commit(repo_path, repo.head_commit).sha,
        index_updated_at=repo.updated_at.isoformat() if repo.updated_at else None,
    )
    rows.files = list(
        (
            await session.execute(
                select(GraphNode.node_id).where(
                    GraphNode.repository_id == repo.id, GraphNode.node_type == "file"
                )
            )
        ).scalars()
    )
    rows.symbols = [
        tuple(r)
        for r in await session.execute(
            select(
                WikiSymbol.symbol_id,
                WikiSymbol.name,
                WikiSymbol.qualified_name,
                WikiSymbol.kind,
                WikiSymbol.file_path,
            ).where(WikiSymbol.repository_id == repo.id)
        )
    ]
    resolved = (
        await load_repo_components(session, repo.id, repo_path, services=services)
    ).resolved
    rows.components = [
        (
            c.name,
            c.source,
            c.owner,
            c.system,
            c.criticality,
            json.dumps(list(c.tags)),
            len(resolved.files.get(c.name, ())),
        )
        for c in resolved.components
    ]
    rows.changes = [
        (
            c.change_id,
            c.merged_at.isoformat() if c.merged_at else None,
            c.title,
            c.category,
            c.files_changed,
            c.lines_added,
            c.lines_deleted,
            c.provenance,
        )
        for c in (
            await session.execute(
                select(Change)
                .where(Change.repository_id == repo.id)
                .order_by(Change.merged_at.desc(), Change.change_id)
                .limit(CHANGES_PER_REPO)
            )
        ).scalars()
    ]
    snap = (
        await session.execute(
            select(HealthSnapshot)
            .where(HealthSnapshot.repository_id == repo.id)
            .order_by(HealthSnapshot.taken_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if snap is not None:
        rows.health = {
            "taken_at": snap.taken_at.isoformat() if snap.taken_at else None,
            "average_health": snap.average_health,
            "hotspot_health": snap.hotspot_health,
            "maintainability_average": snap.maintainability_average,
        }
    return rows


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


async def refresh_workspace_store(ws_config: Any, workspace_root: Path) -> Path | None:
    """Bring the store up to date with every repo's index; the store's path, or
    None when it cannot be written. Only repos whose stamp moved are re-read."""
    from repowise.core.components.store import services_from_system_graph

    from .system_graph import load_system_graph

    path = store_path(workspace_root)
    try:
        store = WorkspaceStore(path)
    except (OSError, sqlite3.Error):
        _log.warning("Workspace store at %s cannot be opened", path, exc_info=True)
        return None
    try:
        known = store.stamps()
        wanted: dict[str, tuple[Path, str, str]] = {}
        for entry in ws_config.repos:
            repo_path = (Path(workspace_root) / entry.path).resolve()
            wanted[entry.alias] = (repo_path, str(repo_path), repo_stamp(repo_path))
        store.drop_repos(a for a in known if a not in wanted)
        stale = [a for a, (_p, key, stamp) in wanted.items() if known.get(a) != (key, stamp)]
        if stale:
            graph = load_system_graph(workspace_root)
            graph_dict = graph.to_dict() if graph else None
            for alias in stale:
                repo_path, key, stamp = wanted[alias]
                rows = await read_repo_rows(
                    alias, repo_path, services_from_system_graph(graph_dict, alias)
                )
                store.replace_repo(alias, key, stamp, rows)
    except sqlite3.Error as exc:
        _log.warning("Workspace store at %s failed mid-refresh", path, exc_info=True)
        store.close()
        if isinstance(exc, sqlite3.DatabaseError) and _corrupt(exc):
            discard(path)  # rebuilt on the next refresh rather than served
        return None
    store.close()
    return path


def lookup_symbols(
    path: Path, names: Iterable[str]
) -> dict[str, list[tuple[str, str, str]]] | None:
    """``name -> [(alias, symbol_id, file_path)]`` for the symbols whose name or
    qualified name is exactly *name*; None when the store cannot be read (a
    corrupt file is discarded so the next refresh rebuilds it)."""
    out: dict[str, list[tuple[str, str, str]]] = {}
    try:
        uri = f"{Path(path).resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            for name in dict.fromkeys(names):
                rows = conn.execute(
                    "SELECT alias, symbol_id, file_path FROM symbols "
                    "WHERE name = ?1 OR qualified_name = ?1 ORDER BY alias, symbol_id",
                    (name,),
                ).fetchall()
                if rows:
                    out[name] = [(a, s, f) for a, s, f in rows]
    except sqlite3.Error as exc:
        if isinstance(exc, sqlite3.DatabaseError) and _corrupt(exc):
            _log.warning("Workspace store at %s is unreadable; discarding it", path)
            discard(path)
        return None
    return out
