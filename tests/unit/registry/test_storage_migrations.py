"""Plugin storage revisions: their ledger, the SQLite stamp, locking, read-only opens, failure."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.metadata
import json
import os
import sqlite3
import textwrap
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from repowise.core.persistence import database as db_module
from repowise.core.persistence.database import create_engine, init_db, schema_behind
from repowise.core.persistence.models import Base
from repowise.core.registry import plugins
from repowise.core.registry import storage_migrations as sm
from repowise.core.registry.plugins import SeamRegistry
from repowise.core.registry.storage_migrations import (
    StorageMigration,
    StorageMigrationError,
    StorageMigrationRegistry,
    ledger,
)


@pytest.fixture(autouse=True)
def registry(monkeypatch) -> StorageMigrationRegistry:
    """A fresh revision registry and plugin state, with no installed plugins."""
    fresh = StorageMigrationRegistry()
    monkeypatch.setattr(sm, "storage_migrations", fresh)
    monkeypatch.setattr(plugins, "_SEAMS", {})
    monkeypatch.delenv(plugins.ENV_VAR, raising=False)
    for name in ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: [])
    plugins.reset()
    yield fresh
    plugins.reset()


def _checksum(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def _creates(table: str, calls: list[str] | None = None, *, fail: bool = False):
    async def apply(conn) -> None:
        if calls is not None:
            calls.append(table)
        await conn.execute(text(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)'))
        if fail:
            raise RuntimeError("boom")

    return apply


def _revision(namespace: str, revision: str, apply, checksum: str | None = None):
    return StorageMigration(
        namespace, revision, checksum or _checksum(f"{namespace}:{revision}"), apply
    )


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return tmp_path / "wiki.db"


@contextlib.asynccontextmanager
async def _engine(path: Path, *, read_only: bool = False, **kwargs):
    """An engine on *path*, disposed after; *read_only* as a shared or mounted index opens."""
    url = f"file:{path.as_posix()}?mode=ro&uri=true" if read_only else path.as_posix()
    engine = create_engine(f"sqlite:///{url}", **kwargs)
    try:
        yield engine
    finally:
        await engine.dispose()


def _make_read_only(path: Path) -> None:
    """Leave *path* unwritable by name; a WAL store would need its ``-shm`` file writable."""
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
    path.chmod(0o444)


async def _init(path: Path) -> None:
    async with _engine(path) as engine:
        await init_db(engine)


def _rows(path: Path) -> dict[tuple[str, str], str]:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return {(n, r): c for n, r, c in conn.execute(f"SELECT * FROM {ledger.name}")}


def _tables(path: Path) -> set[str]:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _plugin(tmp_path: Path, monkeypatch, body: str) -> str:
    """Load a plugin module whose ``register()`` runs *body*; return its record name."""
    monkeypatch.syspath_prepend(str(tmp_path))
    name = f"rw_storage_plugin_{uuid.uuid4().hex[:8]}"
    source = textwrap.dedent(
        """
        from sqlalchemy import text
        from repowise.core.registry import storage_migrations as sm
        from repowise.core.registry.plugins import seams
        from repowise.core.registry.storage_migrations import StorageMigration

        def creates(table):
            async def apply(conn):
                await conn.execute(text(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)'))
            return apply

        def register():
        """
    ) + textwrap.indent(textwrap.dedent(body), "    ")
    (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
    monkeypatch.setenv(plugins.ENV_VAR, f"{name}:register")
    plugins.reset()
    return f"{name}:register"


_V1 = f'sm.storage_migrations.register(StorageMigration("acme.notes", "v1", "{"a" * 64}", creates("acme_notes")))\n'
_V2 = f'sm.storage_migrations.register(StorageMigration("acme.notes", "v2", "{"b" * 64}", creates("acme_tags")))\n'
_V1_EDITED = _V1.replace("a" * 64, "c" * 64)


async def test_revision_lifecycle_applies_once_keeps_the_cheap_open_and_runs_a_new_one(
    store, registry, monkeypatch
):
    seen: list[bool] = []
    calls: list[str] = []

    async def apply(conn) -> None:
        seen.append(await conn.run_sync(lambda c: inspect(c).has_table("repositories")))
        calls.append("acme_notes")
        await conn.execute(text("CREATE TABLE acme_notes (id INTEGER PRIMARY KEY)"))

    registry.register(_revision("acme.notes", "v1", apply))
    await _init(store)
    assert seen == [True]
    assert _rows(store) == {("acme.notes", "v1"): _checksum("acme.notes:v1")}
    assert "acme_notes" in _tables(store)
    # Alembic's target and the reconciler walk Base.metadata; the ledger is not in it.
    assert ledger.name not in Base.metadata.tables

    def walked(_conn) -> None:
        raise AssertionError("a stamped store reconciled again")

    with monkeypatch.context() as patched:
        patched.setattr(db_module, "_reconcile_schema", walked)
        await _init(store)
    assert calls == ["acme_notes"]

    registry.register(_revision("acme.notes", "v2", _creates("acme_tags", calls)))
    await _init(store)
    assert calls == ["acme_notes", "acme_tags"]
    assert set(_rows(store)) == {("acme.notes", "v1"), ("acme.notes", "v2")}


def test_stamp_does_not_depend_on_plugin_load_order(monkeypatch):
    stamps = []
    for order in (("a.plugin", "b.plugin"), ("b.plugin", "a.plugin")):
        registry = StorageMigrationRegistry()
        for namespace in order:
            registry.register(_revision(namespace, "v1", _creates("unused")))
            registry.register(_revision(namespace, "v2", _creates("unused")))
        monkeypatch.setattr(sm, "storage_migrations", registry)
        stamps.append(db_module.schema_stamp(7))
    assert stamps[0] == stamps[1]
    monkeypatch.setattr(sm, "storage_migrations", StorageMigrationRegistry())
    assert db_module.schema_stamp(7) != stamps[0]


async def test_two_processes_opening_together_apply_a_revision_once(store, registry):
    await _init(store)  # the core schema, as an earlier index left it
    calls: list[str] = []

    async def slow(conn) -> None:
        calls.append("v1")
        await asyncio.sleep(0.2)
        await conn.execute(text("CREATE TABLE acme_notes (id INTEGER PRIMARY KEY)"))

    registry.register(_revision("acme.notes", "v1", slow))
    async with _engine(store) as first, _engine(store) as second:
        await asyncio.gather(init_db(first), init_db(second))

    assert calls == ["v1"]
    assert list(_rows(store)) == [("acme.notes", "v1")]


async def _core_store(path: Path) -> Path:
    """A store with the core schema and one repository row, and no plugin revisions."""
    from repowise.core.persistence.models import Repository

    await _init(path)
    now = datetime.now(UTC)
    async with _engine(path) as engine, engine.begin() as conn:
        await conn.execute(
            Repository.__table__.insert().values(
                id="r1", name="repo", local_path=str(path.parent), default_branch="main",
                settings_json="{}", created_at=now, updated_at=now,
            )
        )
    return path


async def test_read_only_store_leaves_the_plugin_pending_and_its_seams_unready(
    store, tmp_path, monkeypatch
):
    path = await _core_store(store)
    seam = SeamRegistry("demo")
    seam.register("local", "LOCAL")
    name = _plugin(tmp_path, monkeypatch, _V1 + 'seams()["demo"].register("acme", "ACME")\n')

    async with _engine(path, read_only=True) as engine:
        await init_db(engine)  # does not raise
        assert not await schema_behind(engine)

    record = next(r for r in plugins.load_plugins() if r.name == name)
    assert record.status == "pending"
    assert "acme.notes:v1" in record.error and "read-only" in record.error
    assert seam.all() == [("local", "LOCAL")]
    with pytest.raises(KeyError) as raised:
        seam.get("acme")
    assert name in str(raised.value) and "acme.notes:v1" in str(raised.value)
    assert ledger.name not in _tables(path)


@pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0, reason="root ignores modes")
async def test_cli_read_session_opens_a_read_only_store_with_a_pending_revision(
    store, tmp_path, monkeypatch
):
    from repowise.cli._helpers.db import repo_index_session

    path = await _core_store(store)
    _make_read_only(path)
    _plugin(tmp_path, monkeypatch, _V1)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite:///{path.as_posix()}")

    async with repo_index_session(tmp_path) as opened:
        assert opened is not None
        assert opened[1] == "r1"


async def test_cli_read_session_applies_a_pending_revision_to_a_writable_store(
    store, tmp_path, monkeypatch
):
    from repowise.cli._helpers.db import repo_index_session

    path = await _core_store(store)
    _plugin(tmp_path, monkeypatch, _V1)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite:///{path.as_posix()}")
    async with _engine(path) as engine:
        assert await schema_behind(engine)

    async with repo_index_session(tmp_path) as opened:
        assert opened is not None
    assert list(_rows(path)) == [("acme.notes", "v1")]


async def test_cli_read_session_raises_when_a_revision_changed_after_it_applied(
    store, tmp_path, monkeypatch
):
    from repowise.cli._helpers.db import repo_index_session

    path = await _core_store(store)
    _plugin(tmp_path, monkeypatch, _V1)
    await _init(path)
    monkeypatch.setattr(sm, "storage_migrations", StorageMigrationRegistry())
    name = _plugin(tmp_path, monkeypatch, _V1_EDITED)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite:///{path.as_posix()}")

    with pytest.raises(StorageMigrationError) as raised:
        async with repo_index_session(tmp_path):
            pass
    assert f"acme.notes:v1 of plugin {name} changed after it was applied" in str(raised.value)


async def test_a_read_only_store_does_not_hold_back_a_writable_one(tmp_path, monkeypatch):
    read_only = await _core_store(tmp_path / "a.db")
    writable = await _core_store(tmp_path / "b.db")
    _plugin(tmp_path, monkeypatch, _V1)

    async with _engine(read_only, read_only=True) as engine:
        await init_db(engine)
    async with _engine(writable) as engine:
        assert await schema_behind(engine)  # the read path will bring it up to date
        await init_db(engine)

    assert ledger.name not in _tables(read_only)
    assert list(_rows(writable)) == [("acme.notes", "v1")]


async def test_a_locked_store_leaves_the_revision_pending_until_a_later_open(
    store, monkeypatch, registry
):
    path = await _core_store(store)
    registry.register(_revision("acme.notes", "v1", _creates("acme_notes")))
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    try:
        async with _engine(path, busy_timeout_ms=100) as engine:
            await init_db(engine)  # does not raise
            assert not await schema_behind(engine)
        [reason] = registry._pending.values()
        assert "another writer" in reason
    finally:
        writer.execute("ROLLBACK")
        writer.close()
    assert ledger.name not in _tables(path)

    later = StorageMigrationRegistry()  # the next process
    later.register(_revision("acme.notes", "v1", _creates("acme_notes")))
    monkeypatch.setattr(sm, "storage_migrations", later)
    await _init(path)
    assert list(_rows(path)) == [("acme.notes", "v1")]


async def test_a_pending_revision_that_applies_on_a_later_open_readies_the_plugin(
    store, tmp_path, monkeypatch, registry
):
    path = await _core_store(store)
    seam = SeamRegistry("demo")
    name = _plugin(tmp_path, monkeypatch, _V1 + 'seams()["demo"].register("acme", "ACME")\n')
    async with _engine(path, busy_timeout_ms=100) as engine:
        writer = sqlite3.connect(path, isolation_level=None)
        writer.execute("BEGIN IMMEDIATE")
        try:
            await init_db(engine)  # does not raise
        finally:
            writer.execute("ROLLBACK")
            writer.close()
        record = next(r for r in plugins.load_plugins() if r.name == name)
        assert record.status == "pending"
        assert seam.all() == []

        await init_db(engine)  # the same engine and registry, the lock released

    assert list(_rows(path)) == [("acme.notes", "v1")]
    assert registry._pending == {}
    assert (record.status, record.error) == ("loaded", None)
    assert seam.all() == [("acme", "ACME")]


async def _pending_under_a_writer(engine, path: Path) -> None:
    """Open *engine* while another connection holds *path*'s writer lock."""
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    try:
        await init_db(engine)
    finally:
        writer.execute("ROLLBACK")
        writer.close()


async def test_a_store_another_process_brought_up_to_date_readies_the_plugin(
    store, tmp_path, monkeypatch, registry
):
    path = await _core_store(store)
    name = _plugin(tmp_path, monkeypatch, _V1)
    async with _engine(path, busy_timeout_ms=100) as engine:
        await _pending_under_a_writer(engine, path)
        record = next(r for r in plugins.load_plugins() if r.name == name)
        assert record.status == "pending"

        other = StorageMigrationRegistry()  # another process applies and stamps it
        other.register(_revision("acme.notes", "v1", _creates("acme_notes"), "a" * 64))
        with monkeypatch.context() as patched:
            patched.setattr(sm, "storage_migrations", other)
            await _init(path)

        await init_db(engine)  # the stamp matches, so the fast path returns

    assert registry._pending == {}
    assert (record.status, record.error) == ("loaded", None)


async def test_a_namespace_that_applies_is_ready_when_a_later_one_fails(
    store, tmp_path, monkeypatch, registry
):
    path = await _core_store(store)
    name = _plugin(tmp_path, monkeypatch, _V1)
    async with _engine(path, busy_timeout_ms=100) as engine:
        await _pending_under_a_writer(engine, path)
        record = next(r for r in plugins.load_plugins() if r.name == name)
        registry.register(_revision("z.other", "v1", _creates("z_other", fail=True)))
        with pytest.raises(StorageMigrationError, match="boom"):
            await init_db(engine)

    assert list(_rows(path)) == [("acme.notes", "v1")]
    assert registry._pending == {}
    assert record.status == "loaded"


async def test_pending_clears_only_where_and_when_a_namespace_completes(
    tmp_path, monkeypatch, registry
):
    read_only = await _core_store(tmp_path / "ro.db")
    writable = await _core_store(tmp_path / "rw.db")
    name = _plugin(tmp_path, monkeypatch, _V1)
    async with _engine(read_only, read_only=True) as engine:
        await init_db(engine)
    record = next(r for r in plugins.load_plugins() if r.name == name)
    pending = dict(registry._pending)

    await _init(writable)  # applied there; still pending on the read-only store
    assert registry._pending == pending
    assert record.status == "pending"
    record.status, record.error = "failed", "independent failure"
    await _init(writable)
    assert (record.status, record.error) == ("failed", "independent failure")


async def test_a_namespace_left_part_applied_stays_pending(store, tmp_path, monkeypatch, registry):
    path = await _core_store(store)
    name = _plugin(tmp_path, monkeypatch, _V1)
    plugins.load_plugins()

    async def busy(conn) -> None:
        raise _dbapi_error(sqlite_errorcode=sqlite3.SQLITE_BUSY)

    registry.register(_revision("acme.notes", "v2", busy))
    for _ in range(2):
        await _init(path)
        record = next(r for r in plugins.load_plugins() if r.name == name)
        assert list(_rows(path)) == [("acme.notes", "v1")]
        assert record.status == "pending"
        assert len(registry._pending) == 1


def _dbapi_error(**codes) -> DBAPIError:
    driver = type("Driver", (Exception,), codes)
    return DBAPIError("stmt", None, driver("schreibgeschützt"))  # the message is never read


def test_a_refused_write_is_matched_by_error_code_not_message():
    refused = sm._write_refused
    readonly_dbmoved = sqlite3.SQLITE_READONLY | (4 << 8)
    assert "read-only" in refused(_dbapi_error(sqlite_errorcode=sqlite3.SQLITE_READONLY))
    assert "read-only" in refused(_dbapi_error(sqlite_errorcode=readonly_dbmoved))
    assert "read-only" in refused(_dbapi_error(sqlstate="25006"))
    assert "another writer" in refused(_dbapi_error(sqlite_errorcode=sqlite3.SQLITE_BUSY))
    assert refused(_dbapi_error(sqlite_errorcode=sqlite3.SQLITE_ERROR)) is None
    assert refused(_dbapi_error(sqlstate="42P07")) is None
    assert refused(_dbapi_error()) is None


def test_registering_a_revision_again_compares_its_checksum(registry):
    registry.register(_revision("acme.notes", "v1", _creates("acme_notes")))
    registry.register(_revision("acme.notes", "v1", _creates("another_callable")))
    with pytest.raises(ValueError, match="another checksum"):
        registry.register(_revision("acme.notes", "v1", _creates("x"), _checksum("edited")))
    assert len(registry.revisions()) == 1


async def test_any_other_database_error_still_fails_the_open(store, registry):
    async def broken(conn) -> None:
        await conn.execute(text("CREATE TABLE"))  # a syntax error, not a read-only store

    registry.register(_revision("acme.notes", "v1", broken))
    with pytest.raises(StorageMigrationError, match=r"acme\.notes:v1 failed"):
        await _init(store)


async def test_a_failed_revision_names_itself_and_leaves_nothing_behind(store, registry):
    registry.register(_revision("acme.notes", "v1", _creates("acme_notes")))
    registry.register(_revision("acme.notes", "v2", _creates("acme_tags", fail=True)))

    with pytest.raises(StorageMigrationError, match=r"acme\.notes:v2 failed: boom"):
        await _init(store)

    assert list(_rows(store)) == [("acme.notes", "v1")]
    tables = _tables(store)
    assert "acme_notes" in tables
    assert "acme_tags" not in tables  # its own transaction rolled the DDL back


async def test_a_changed_checksum_fails_every_open(store, monkeypatch, registry):
    registry.register(_revision("acme.notes", "v1", _creates("acme_notes")))
    await _init(store)

    edited = StorageMigrationRegistry()
    edited.register(_revision("acme.notes", "v1", _creates("acme_notes"), _checksum("edited")))
    monkeypatch.setattr(sm, "storage_migrations", edited)
    for read_only in (False, True):
        async with _engine(store, read_only=read_only) as engine:
            with pytest.raises(StorageMigrationError, match=r"acme\.notes:v1 changed after it"):
                await init_db(engine)


async def test_revisions_apply_in_registration_order_within_a_namespace(store, registry):
    calls: list[str] = []
    registry.register(_revision("z.plugin", "v10", _creates("z_first", calls)))
    registry.register(_revision("a.plugin", "v1", _creates("a_only", calls)))
    registry.register(_revision("z.plugin", "v2", _creates("z_second", calls)))
    await _init(store)

    assert [c for c in calls if c.startswith("z_")] == ["z_first", "z_second"]
    assert sorted(calls) == ["a_only", "z_first", "z_second"]


def _listing(tmp_path: Path, monkeypatch, before: str, after: str) -> tuple[str, dict]:
    """Apply *before* to the store here, then list plugins with *after* registered."""
    from repowise.cli.commands.plugins_cmd import plugins_command

    (tmp_path / ".repowise").mkdir()
    _plugin(tmp_path, monkeypatch, before)
    asyncio.run(_init(tmp_path / ".repowise" / "wiki.db"))
    monkeypatch.setattr(sm, "storage_migrations", StorageMigrationRegistry())
    name = _plugin(tmp_path, monkeypatch, after)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(plugins_command, ["--format", "json"])
    assert result.exit_code == 0, result.output
    return name, next(p for p in json.loads(result.output)["plugins"] if p["name"] == name)


def test_plugins_command_lists_applied_and_pending_revisions(tmp_path, monkeypatch):
    from repowise.cli.commands.plugins_cmd import plugins_command

    _, row = _listing(tmp_path, monkeypatch, _V1, _V1 + _V2)
    assert row["storage"] == {"applied": ["acme.notes:v1"], "pending": ["acme.notes:v2"]}
    assert row["status"] == "pending"
    assert "acme.notes:v2" in row["error"]
    store = tmp_path / ".repowise" / "wiki.db"
    assert ledger.name in _tables(store) and "acme_tags" not in _tables(store)

    table = CliRunner().invoke(plugins_command, [])
    assert "storage: applied acme.notes:v1; pending acme.notes:v2" in table.output


def test_plugins_command_lists_a_changed_revision_apart_from_applied(tmp_path, monkeypatch):
    _, row = _listing(tmp_path, monkeypatch, _V1, _V1_EDITED)
    assert row["storage"] == {"applied": [], "pending": []}
    assert row["status"] == "failed"
    assert "acme.notes:v1 changed after it was applied" in row["error"]
