"""The statement-site index: every place a repo issues a database statement, stored with each index."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from alembic import command

from repowise.core.evidence.statement_join import (
    Columns,
    StatementSite,
    match_statement,
    statement_shape,
)
from repowise.core.ingestion.models import Symbol
from repowise.core.persistence.crud import (
    list_statement_sites,
    stamp_sites_fingerprint,
    stored_sites_fingerprint,
)
from repowise.core.workspace.statement_sites import (
    attach_functions,
    read_python_sites,
    read_schedule_sites,
    refresh_statement_sites,
    sites_fingerprint,
)

from ._repo_index import make_repo_index


def _by_name(
    sites: list[StatementSite],
) -> dict[tuple[str | None, str | None], list[StatementSite]]:
    out: dict[tuple[str | None, str | None], list[StatementSite]] = {}
    for s in sites:
        out.setdefault((s.name, s.verb), []).append(s)
    return out


_CLIENT = """\
EVENTS = "events"


async def record(client, event, user_id):
    row = {"event": event, "user_id": user_id}
    await client.table(EVENTS).insert(row).execute()


async def purge(client, snapshot_id, edge):
    q = client.table("pages").delete().eq("snapshot_id", snapshot_id)
    if edge:
        q = q.lte("path", edge)
    await q.execute()


async def lookup(client, repo_id):
    return await (
        client.table("snapshots").select("short_id").eq("repo_id", repo_id)
        .is_("reaped_at", "null").limit(1).execute()
    )


async def search(client, text, min_stars=None):
    params = {"p_search": text, "p_limit": 20}
    if min_stars is not None:
        params["p_min"] = min_stars
    return await client.rpc("search_repos", params).execute()


async def escaped(client, repo_id):
    q = client.table("snapshots").select("short_id").eq("repo_id", repo_id)
    return q  # filtered further by whoever gets it
"""


def test_client_calls_read_their_table_verb_columns_and_filters() -> None:
    sites = _by_name(read_python_sites({"app/db.py": _CLIENT}))

    (insert,) = sites[("events", "insert")]
    assert (insert.channel, insert.target, insert.via, insert.line) == (
        "postgrest",
        "table",
        "constant",
        6,
    )
    assert insert.columns == Columns(frozenset({"event", "user_id"}))

    (delete,) = sites[("pages", "delete")]
    # A builder kept in a local and run later: the later filters may apply.
    assert delete.filters == Columns(frozenset({"snapshot_id"}), frozenset({"path"}))

    lookup, escaped = sites[("snapshots", "select")]
    assert lookup.columns == Columns(frozenset({"short_id"}))
    assert lookup.filters == Columns(frozenset({"repo_id", "reaped_at"}))
    # Returned unrun: anyone may filter it more.
    assert escaped.filters == Columns(frozenset({"repo_id"}), closed=False)

    (rpc,) = sites[("search_repos", "call")]
    assert (rpc.target, rpc.columns) == (
        "rpc",
        Columns(frozenset({"p_search", "p_limit"}), frozenset({"p_min"})),
    )


_HELPERS = """\
from app.names import SIGHTINGS

TABLES = (("pages", "path", 100), ("chunks", "id", 500))


async def cached_rpc(client, fn, params, *, ttl=60):
    async def run():
        return await client.rpc(fn, params).execute()

    return await run()


def drain(client, table, key, snapshot_id):
    return client.table(table).delete().eq("snapshot_id", snapshot_id).execute()


def drain_all(client, snapshot_id):
    return {table: drain(client, table, key, snapshot_id) for table, key, _ in TABLES}


async def explore(client, text):
    params = {"p_search": text}
    return await cached_rpc(client, "explore_repos", params)


async def sighting(client, anon):
    return await cached_rpc(client, SIGHTINGS, {"p_anon": anon})


def get_cached(client, snapshot_id, *, table="overview_cache"):
    return client.table(table).select("payload").eq("snapshot_id", snapshot_id).execute()


def overview(client, snapshot_id):
    return get_cached(client, snapshot_id)


def stats(client, snapshot_id):
    return get_cached(client, snapshot_id, table="stats_cache")


def save_any(client, table, rows):
    return client.table(table).insert(rows).execute()


def save_from_request(client, request):
    return save_any(client, request.table, request.rows)
"""


def test_names_passed_through_helpers_and_constants_bind_where_they_are_written() -> None:
    texts = {"app/helpers.py": _HELPERS, "app/names.py": 'SIGHTINGS = "record_sighting"\n'}
    sites = _by_name(read_python_sites(texts))

    # A helper's caller passing the name holds the site, with the arguments it sends.
    (explore,) = sites[("explore_repos", "call")]
    assert (explore.line, explore.via, explore.columns) == (
        23,
        "wrapper",
        Columns(frozenset({"p_search"})),
    )
    (sighting,) = sites[("record_sighting", "call")]
    assert (sighting.line, sighting.columns) == (27, Columns(frozenset({"p_anon"})))
    # A loop over a constant table: the helper issues each name.
    assert [(s.line, s.via) for s in sites[("pages", "delete")]] == [(14, "data")]
    assert [(s.line, s.via) for s in sites[("chunks", "delete")]] == [(14, "data")]
    # A parameter's default names the table when a caller passes none.
    assert [(s.line, s.via) for s in sites[("overview_cache", "select")]] == [(31, "constant")]
    assert [(s.line, s.via) for s in sites[("stats_cache", "select")]] == [(39, "wrapper")]
    # A name only known at run time stays a site with no name.
    assert [(s.line, s.via) for s in sites[(None, "insert")]] == [(43, "runtime")]
    # Nothing is left of the helpers whose every caller named the table.
    assert not [s for s in sites.get((None, "call"), [])]


_MODELS = """\
from sqlalchemy import Table, delete, select


class User(Base):
    __tablename__ = "users"


audit = Table("audit_log", metadata)


async def get_user(session, user_id):
    return await session.execute(select(User).where(User.id == user_id))


async def add_user(session, name):
    session.add(User(name=name))


def write_audit(conn):
    conn.execute(audit.insert())


def remove(session, user_id):
    session.execute(delete(User).where(User.id == user_id))


def count_active(cursor):
    cursor.execute("SELECT count(*) FROM users WHERE active = %s", (True,))


def by_column(cursor, column):
    cursor.execute(f"SELECT id FROM users WHERE {column} = %s")


def legacy(session):
    return User.query.filter_by(active=True).all()
"""


def test_orm_models_and_literal_sql_are_sites_too() -> None:
    sites = _by_name(read_python_sites({"app/models.py": _MODELS}))
    assert [(s.line, s.via) for s in sites[("users", "select")]] == [
        (12, "model"),
        (28, "sql"),
        (32, "sql"),
        (36, "model"),
    ]
    assert [s.line for s in sites[("users", "insert")]] == [16]
    assert [s.line for s in sites[("audit_log", "insert")]] == [20]
    assert [s.line for s in sites[("users", "delete")]] == [24]
    literal = sites[("users", "select")][1]
    assert (literal.channel, literal.filters) == ("sql", Columns(frozenset({"active"})))
    # Part of an f-string says nothing of what the rest adds.
    assert sites[("users", "select")][2].filters == Columns(frozenset(), closed=False)


def test_test_files_and_docstrings_issue_nothing() -> None:
    text = '"""SELECT * FROM users"""\n\ndef f(c):\n    """DELETE FROM users"""\n    return 1\n'
    assert read_python_sites({"app/doc.py": text}) == []


_CRON = """\
select cron.schedule(
  'summary-refresh',
  '15 */2 * * *',
  $job$set statement_timeout = '9min'; select public.refresh_summary();$job$
);
select cron.schedule('nightly', '0 3 * * *', 'delete from sessions where expires_at < now()');
"""


def test_a_database_schedule_runs_each_statement_of_its_job() -> None:
    sites = read_schedule_sites("migrations/159_summary.sql", _CRON)
    assert [(s.name, s.verb, s.line, s.via, s.symbol_id) for s in sites] == [
        ("refresh_summary", "call", 1, "schedule", None),
        ("sessions", "delete", 6, "schedule", None),
    ]
    match = match_statement(statement_shape("select public.refresh_summary()"), sites)
    assert match.issuer == "database_schedule"


def test_a_site_binds_to_the_innermost_function_holding_its_line() -> None:
    site = StatementSite("a.py", 12, "postgrest", "table", "t", "select", "literal")
    spans = {"a.py": [(1, 40, "a.py::Outer.method"), (10, 15, "a.py::Outer.method.inner")]}
    (bound,) = attach_functions([site], spans)
    assert bound.symbol_id == "a.py::Outer.method.inner"
    (alone,) = attach_functions([site], {})
    assert alone.symbol_id is None


def _symbol(path: str, name: str, start: int, end: int) -> Symbol:
    return Symbol(
        id=f"{path}::{name}",
        name=name,
        qualified_name=name,
        kind="function",
        signature=f"def {name}()",
        start_line=start,
        end_line=end,
        docstring=None,
        visibility="public",
        language="python",
    )


async def test_the_index_stores_sites_and_rereads_only_when_python_or_sql_moves(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "svc"
    (repo / "app").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "app" / "db.py").write_text(_CLIENT)
    # A test's fake client issues nothing in production.
    (repo / "tests" / "test_db.py").write_text(
        'def test_x(client):\n    client.table("events").insert({"a": 1}).execute()\n'
    )
    (repo / "README.md").write_text("# svc\n")
    symbols = [
        _symbol("app/db.py", "record", 4, 6),
        _symbol("app/db.py", "purge", 9, 13),
        _symbol("app/db.py", "lookup", 16, 20),
        _symbol("app/db.py", "search", 23, 27),
        _symbol("app/db.py", "escaped", 30, 32),
    ]
    index = await make_repo_index(repo, {"app/db.py": symbols})
    session, repo_id = index.session, index.repo_id
    try:
        assert await refresh_statement_sites(session, repo_id, repo) == 5
        stored = await list_statement_sites(session, repo_id)
        assert {s.file_path for s in stored} == {"app/db.py"}
        insert = next(s for s in stored if s.verb == "insert")
        assert (insert.symbol_id, insert.columns) == (
            "app/db.py::record",
            Columns(frozenset({"event", "user_id"})),
        )
        assert await stored_sites_fingerprint(session, repo_id) == sites_fingerprint(repo)

        # An update that touches no Python or SQL file reads nothing.
        (repo / "app" / "db.py").write_text(_CLIENT.replace('"pages"', '"chunks"'))
        assert (
            await refresh_statement_sites(session, repo_id, repo, changed_paths=["README.md"])
            is None
        )
        assert any(s.name == "pages" for s in await list_statement_sites(session, repo_id))
        assert (
            await refresh_statement_sites(session, repo_id, repo, changed_paths=["app/db.py"]) == 5
        )
        names = {s.name for s in await list_statement_sites(session, repo_id)}
        assert "chunks" in names and "pages" not in names
        # Read by another version: the next update reads again whatever it touched.
        await stamp_sites_fingerprint(session, repo_id, "older")
        assert await refresh_statement_sites(session, repo_id, repo, changed_paths=[]) == 5
    finally:
        await index.close()


async def test_a_failed_read_keeps_the_rows_and_retries_on_the_next_update(
    tmp_path: Path, monkeypatch
) -> None:
    from repowise.core.workspace import statement_sites

    repo = tmp_path / "svc"
    repo.mkdir()
    (repo / "db.py").write_text(_CLIENT)
    index = await make_repo_index(repo, {})
    session, repo_id = index.session, index.repo_id
    try:
        assert await refresh_statement_sites(session, repo_id, repo) == 5

        async def broken(*_args, **_kwargs):
            raise RuntimeError("read failed")

        monkeypatch.setattr(statement_sites, "read_statement_sites", broken)
        assert (
            await refresh_statement_sites(session, repo_id, repo, changed_paths=["db.py"]) is None
        )
        assert len(await list_statement_sites(session, repo_id)) == 5
        assert await stored_sites_fingerprint(session, repo_id) is None
        # A missing checkout reads nothing and keeps the rows.
        assert await refresh_statement_sites(session, repo_id, tmp_path / "gone") is None
        assert len(await list_statement_sites(session, repo_id)) == 5
    finally:
        await index.close()


def _sqlite_columns(path: Path, table: str) -> dict[str, tuple[int, int]]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1]: (row[3], row[5]) for row in rows}


def test_the_migration_matches_the_models_and_downgrades(tmp_path: Path) -> None:
    from tests.unit.persistence.test_evidence_store import _alembic_config, _init_db

    migrated, created = tmp_path / "migrated.db", tmp_path / "created.db"
    _init_db(created)
    config = _alembic_config(migrated)
    command.upgrade(config, "head")
    for table in ("statement_sites", "statement_sites_stamp", "evidence_statement_stats"):
        assert _sqlite_columns(migrated, table) == _sqlite_columns(created, table), table

    command.downgrade(config, "0149")
    assert _sqlite_columns(migrated, "statement_sites") == {}
    assert _sqlite_columns(migrated, "statement_sites_stamp") == {}
    assert "issuer" not in _sqlite_columns(migrated, "evidence_statement_stats")
    command.upgrade(config, "head")
    assert _sqlite_columns(migrated, "statement_sites") == _sqlite_columns(
        created, "statement_sites"
    )


def test_the_whole_read_runs_without_a_database() -> None:
    # read_python_sites is pure: a hosted indexer calls it on its own checkout.
    assert asyncio.iscoroutinefunction(refresh_statement_sites)
    assert read_python_sites({}) == []


def test_an_interpolated_table_is_a_site_known_only_at_run_time() -> None:
    text = 'def purge(cursor, table):\n    cursor.execute(f"DELETE FROM {table} WHERE id = %s")\n'
    (site,) = read_python_sites({"app/purge.py": text})
    assert (site.name, site.verb, site.via, site.channel) == (None, "delete", "runtime", "sql")


def test_a_file_that_only_names_a_model_is_read_too() -> None:
    texts = {
        "app/models.py": 'class User(Base):\n    __tablename__ = "users"\n',
        "app/service.py": (
            "from app.models import User\n\n\n"
            "def load(session, user_id):\n    return session.get(User, user_id)\n"
        ),
    }
    (site,) = read_python_sites(texts)
    assert (site.file_path, site.line, site.name, site.verb) == (
        "app/service.py",
        5,
        "users",
        "select",
    )
