"""``wiki_symbols`` must be seekable by ``(repository_id, file_path)``.

The unique constraint's implicit index is keyed on ``symbol_id``, so a lookup
by file could only seek on ``repository_id`` and filter the rest in memory —
every symbol the repo has, to return the handful belonging to one file.

The index is declared on the model *and* shipped as Alembic 0046. Local stores
come from ``init_db`` and never run Alembic; hosted only ever runs Alembic. This
pins the model half, which is what ``init_db`` builds from.
"""

from __future__ import annotations

import sqlite3

from sqlalchemy import text

from repowise.cli.commands.augment_cmd.fast_lookup import symbols_named
from repowise.core.persistence.models import WikiSymbol

_INDEX_NAME = "ix_wiki_symbols_repo_path"


def test_index_is_declared_on_the_model() -> None:
    by_name = {ix.name: ix for ix in WikiSymbol.__table__.indexes}
    assert _INDEX_NAME in by_name, "init_db-created stores would lack the index"
    assert [c.name for c in by_name[_INDEX_NAME].columns] == ["repository_id", "file_path"]


async def test_a_file_scoped_lookup_seeks_instead_of_scanning(async_session) -> None:
    """The plan, not the timing — a wall-clock assert would be flaky in CI."""
    plan = (
        await async_session.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT symbol_id FROM wiki_symbols "
                "WHERE repository_id = :r AND file_path IN (:a, :b)"
            ),
            {"r": "repo", "a": "x.py", "b": "y.py"},
        )
    ).all()

    detail = " ".join(str(row[-1]) for row in plan)
    assert _INDEX_NAME in detail, f"expected a seek on {_INDEX_NAME}, got: {detail}"
    assert "file_path" in detail, f"index used but not keyed on file_path: {detail}"


_SYMBOL_COUNT = 40
_NAMES = [f"s{i:02d}" for i in range(_SYMBOL_COUNT)]


def _seed_symbols(conn: sqlite3.Connection, *, with_index: bool) -> sqlite3.Connection:
    """One file, many symbols, inserted in reverse name order.

    That reversal is the whole point: rowid order and name order disagree, so a
    plan that walks ``(repository_id, file_path)`` (then rowid) reaches
    different rows first than one walking ``uq_wiki_symbol`` (whose key is
    ``"<path>::<name>"``, i.e. name order within a file).
    """
    conn.execute(
        "CREATE TABLE wiki_symbols (id TEXT PRIMARY KEY, repository_id TEXT, "
        "file_path TEXT, symbol_id TEXT, name TEXT, kind TEXT, start_line INT)"
    )
    conn.execute("CREATE UNIQUE INDEX uq_wiki_symbol ON wiki_symbols (repository_id, symbol_id)")
    for i, name in enumerate(reversed(_NAMES)):
        conn.execute(
            "INSERT INTO wiki_symbols VALUES (?,?,?,?,?,?,?)",
            (f"id{i}", "repo", "a.py", f"a.py::{name}", name, "function", i),
        )
    if with_index:
        conn.execute(f"CREATE INDEX {_INDEX_NAME} ON wiki_symbols (repository_id, file_path)")
    conn.commit()
    return conn


def test_symbol_rescue_page_does_not_depend_on_which_index_exists() -> None:
    """A ``LIMIT`` with no ``ORDER BY`` is decided by the planner's index choice.

    Adding ``ix_wiki_symbols_repo_path`` changed which rows ``symbols_named``
    returned — on this fixture the page moved from ``s00..s07`` to ``s39..s32``,
    a disjoint set. Its caller reduces the page to a single hint, so the widened
    rescue could name a different symbol purely because an unrelated index
    appeared on the table. Ordering makes the page a property of the data.
    """
    without = _seed_symbols(sqlite3.connect(":memory:"), with_index=False)
    with_index = _seed_symbols(sqlite3.connect(":memory:"), with_index=True)
    try:
        before = symbols_named(without, "repo", _NAMES, 8)
        after = symbols_named(with_index, "repo", _NAMES, 8)

        assert before == after, "the returned page moved when an index was added"
        # And it is the page the name-keyed autoindex was giving all along.
        assert [r[0] for r in before] == _NAMES[:8]
    finally:
        without.close()
        with_index.close()
