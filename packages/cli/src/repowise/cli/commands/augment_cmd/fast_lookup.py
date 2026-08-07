"""Read-only stdlib-``sqlite3`` twins of the three hot hook index lookups.

Why this exists rather than the ORM the rest of the codebase uses: in a fresh
hook process, ``import repowise.core.persistence`` costs ~958ms on this
machine, against 17ms to build the engine and 34ms to run the query. The three
call sites that use this module (the flood digest's PageRank ordering,
triage's symbol + PageRank pair, the widened rescue's exact-name lookup) are
plain SELECTs over two tables. They never used the MCP retrieval stack and
already hand-write their own selects, so paying that import buys them nothing.

The zero-result rescue deliberately does **not** use this module: 45% of its
queries fall through to ``FullTextSearch``, which is genuinely shared code
doing real work. No FTS5 is hand-rolled here and no part of the retrieval
stack is replaced.

Everything here is read-only and fails soft. :func:`connect` returns ``None``
whenever the fast path is not provably safe, and every caller falls back to
the ORM path rather than to silence, so schema drift degrades to today's
behaviour instead of losing the surface.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

#: The env vars ``resolve_db_url`` honours, copied from
#: ``repowise.core.persistence.database.DB_ENV_VARS`` rather than imported:
#: importing that module runs the persistence package ``__init__``, which is
#: the second this module exists to avoid. If that tuple ever grows, this one
#: has to grow with it; the cost of missing it is a slow query, not a wrong
#: answer, because an unrecognised URL still lands on the ORM.
_DB_ENV_VARS = ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL")

#: SQLAlchemy compiles ``ilike()`` on SQLite to
#: ``lower(col) LIKE lower(?) ESCAPE '\\'``. The queries below spell that out
#: verbatim so the ported semantics are identical rather than merely similar.
#: ``escape_like`` is the same function as ``persistence.sql.escape_like``,
#: duplicated for the same import reason as above.
_LIKE_ESCAPE = "\\"


def escape_like(value: str) -> str:
    """Escape LIKE metacharacters; twin of ``persistence.sql.escape_like``."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def connect(repo_path: Path) -> sqlite3.Connection | None:
    """Read-only connection to the repo's ``wiki.db``, or None for the ORM.

    ``None`` whenever a DB env var is set: ``resolve_db_url`` honours
    ``REPOWISE_DB_URL`` / ``REPOWISE_DATABASE_URL``, so a hosted or postgres
    setup must not be handed a local file path and silently find nothing. A
    sqlite override lands here too, taking the ORM path rather than growing
    URL parsing for a case no hook has; the ceiling on that shortcut is one
    slow query, and lifting it is a URL parse away.

    Callers must check the file exists first when a missing index means
    "stay silent" rather than "use the ORM": the two entry points differ.
    """
    if any(os.environ.get(name) for name in _DB_ENV_VARS):
        return None
    db_path = repo_path / ".repowise" / "wiki.db"
    if not db_path.exists():
        return None
    try:
        return sqlite3.connect(
            f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=1
        )
    except sqlite3.Error:
        return None


def repo_id(conn: sqlite3.Connection, repo_path: Path) -> str | None:
    """The indexed repository id for *repo_path*, or None if it has no row.

    Matches ``get_repository_by_path``: exact equality against the stored
    ``local_path``, spelled with ``str(repo_path)`` as every writer does.
    """
    row = conn.execute(
        "SELECT id FROM repositories WHERE local_path = ?", (str(repo_path),)
    ).fetchone()
    return row[0] if row else None


def pagerank(
    conn: sqlite3.Connection, repository_id: str, paths: list[str]
) -> dict[str, float]:
    """PageRank by node id for the file nodes among *paths*."""
    if not paths:
        return {}
    placeholders = ",".join("?" * len(paths))
    rows = conn.execute(
        "SELECT node_id, pagerank FROM graph_nodes "
        "WHERE repository_id = ? AND node_type = 'file' "
        f"AND node_id IN ({placeholders})",
        (repository_id, *paths),
    ).fetchall()
    return {node_id: pr or 0.0 for node_id, pr in rows if node_id}


def files_by_basename(
    conn: sqlite3.Connection, repository_id: str, basename: str, limit: int = 5
) -> list[str]:
    """Node ids of indexed files with this basename, newest-schema first.

    Capped rather than exhaustive: the caller only needs to tell one match from
    more-than-one, and the cap keeps a common name like ``__init__.py`` from
    materialising hundreds of rows to be counted and thrown away. The cap is
    above two on purpose — the caller still has to drop rows whose file is no
    longer on disk, and a cap of two lets one stale row hide the single live
    answer behind an apparent tie.

    ``LIKE`` with an explicit ``ESCAPE`` rather than ``GLOB``: a path is data,
    not a pattern, and ``GLOB`` would read a ``[`` in a filename as a character
    class. The exact-equality arm catches a file at the repository root, which
    has no leading separator to match, and is spelled case-insensitively to
    match the ``LIKE`` arm's own default rather than holding a root file to a
    stricter rule than a nested one.

    Filtering non-path nodes is left to the caller: ``node_type = 'file'`` also
    covers resolved external packages, and ``:`` is a legal character in a
    POSIX path, so excluding them in SQL would mean a colon test that can drop
    a real file and manufacture a false unique.
    """
    if not basename:
        return []
    rows = conn.execute(
        "SELECT node_id FROM graph_nodes "
        "WHERE repository_id = ? AND node_type = 'file' "
        "AND (lower(node_id) = lower(?) "
        f"OR node_id LIKE ? ESCAPE '{_LIKE_ESCAPE}') LIMIT ?",
        (repository_id, basename, f"%/{escape_like(basename)}", limit),
    ).fetchall()
    return [node_id for (node_id,) in rows if node_id]


def symbols_matching(
    conn: sqlite3.Connection, repository_id: str, paths: list[str], needle: str
) -> list[tuple[str, str]]:
    """``(file_path, name)`` for symbols in *paths* whose name contains *needle*."""
    if not paths:
        return []
    placeholders = ",".join("?" * len(paths))
    return conn.execute(
        "SELECT file_path, name FROM wiki_symbols "
        f"WHERE repository_id = ? AND file_path IN ({placeholders}) "
        f"AND lower(name) LIKE lower(?) ESCAPE '{_LIKE_ESCAPE}'",
        (repository_id, *paths, f"%{escape_like(needle)}%"),
    ).fetchall()


def symbols_named(
    conn: sqlite3.Connection, repository_id: str, names: list[str], limit: int
) -> list[tuple[str, str, str, int]]:
    """``(name, kind, file_path, start_line)`` for symbols named exactly *names*.

    Case-sensitive, like the ``IN`` it replaces: SQLite compares TEXT with
    BINARY collation unless a column says otherwise, and neither the ORM path
    nor this one asks for NOCASE. The widened rescue depends on that, since it
    generates its case variants explicitly.

    Ordered because the ``LIMIT`` truncates. Unordered, the rows arrive in
    whatever order the chosen index happens to walk, so *which* rows survive
    the cut was the planner's choice — and adding an unrelated index to this
    table silently changed the answer. ``(file_path, name)`` is the order the
    ``uq_wiki_symbol`` autoindex gave for free, its key being
    ``"<path>::<name>"``, so this pins the long-standing result rather than
    picking a new one.
    """
    if not names:
        return []
    placeholders = ",".join("?" * len(names))
    return conn.execute(
        "SELECT name, kind, file_path, start_line FROM wiki_symbols "
        f"WHERE repository_id = ? AND name IN ({placeholders}) "
        "ORDER BY file_path, name LIMIT ?",
        (repository_id, *names, limit),
    ).fetchall()
