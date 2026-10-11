"""A configured database is a store, even with no repo-local ``wiki.db``.

Both head-commit writers guarded on the repo-local file alone and then built
their engine from ``resolve_db_url``, which prefers ``REPOWISE_DB_URL``. Under a
shared database that file is absent by design, so the guard returned before the
engine was ever built: ``repowise update`` left ``head_commit`` and
``updated_at`` stale in the row that ``/api/repos``, MCP ``_meta`` and the
health overview read, and the commit-offset heal never ran at all.

These tests pin the three cases the guard has to tell apart: a configured
database, a repo-local file, and neither.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.commands.update_cmd.persistence import heal_commit_offsets
from repowise.core.persistence import create_engine, create_session_factory, get_session
from repowise.core.persistence.crud import get_repository_by_path
from repowise.core.persistence.database import has_db_store
from repowise.core.workspace.update import reconcile_repo_head_commit

HEAD = "a" * 40


def _clear_db_env(monkeypatch) -> None:
    for name in ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


def _configure_shared_db(monkeypatch, tmp_path: Path) -> Path:
    """Point both env vars at a shared store outside the repo, as Postgres would be."""
    shared = tmp_path / "shared" / "index.db"
    shared.parent.mkdir(parents=True, exist_ok=True)
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite:///{shared.as_posix()}")
    return shared


def test_has_db_store_counts_a_configured_database(tmp_path, monkeypatch):
    _configure_shared_db(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    assert not (repo / ".repowise" / "wiki.db").exists()
    assert has_db_store(repo) is True


def test_has_db_store_needs_the_file_when_nothing_is_configured(tmp_path, monkeypatch):
    _clear_db_env(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()

    assert has_db_store(repo) is False

    (repo / ".repowise").mkdir()
    (repo / ".repowise" / "wiki.db").write_bytes(b"")
    assert has_db_store(repo) is True


async def test_stamp_writes_the_row_through_a_configured_database(tmp_path, monkeypatch):
    shared = _configure_shared_db(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    await reconcile_repo_head_commit(repo, HEAD)

    engine = create_engine(f"sqlite:///{shared.as_posix()}")
    try:
        async with get_session(create_session_factory(engine)) as session:
            row = await get_repository_by_path(session, str(repo))
    finally:
        await engine.dispose()

    assert row is not None, "a configured database is a store; the stamp has to land"
    assert row.head_commit == HEAD


def test_heal_reaches_a_configured_database(tmp_path, monkeypatch):
    shared = _configure_shared_db(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    assert not shared.exists()

    heal_commit_offsets(repo)

    # Reaching the store means opening it, and opening it migrates first. A
    # guard that returns early leaves the shared database untouched.
    assert shared.exists(), "the heal returned before it ever opened the store"


def test_stamp_still_writes_to_the_repo_local_store(tmp_path, monkeypatch):
    _clear_db_env(monkeypatch)
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    (repo / ".repowise" / "wiki.db").write_bytes(b"")

    import asyncio

    asyncio.run(reconcile_repo_head_commit(repo, HEAD))

    local = repo / ".repowise" / "wiki.db"
    engine = create_engine(f"sqlite:///{local.as_posix()}")

    async def _read():
        async with get_session(create_session_factory(engine)) as session:
            return await get_repository_by_path(session, str(repo))

    try:
        row = asyncio.run(_read())
    finally:
        asyncio.run(engine.dispose())

    assert row is not None
    assert row.head_commit == HEAD


def test_stamp_still_noops_when_there_is_no_store_at_all(tmp_path, monkeypatch):
    _clear_db_env(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()

    import asyncio

    asyncio.run(reconcile_repo_head_commit(repo, HEAD))

    assert not (repo / ".repowise" / "wiki.db").exists(), (
        "a stamp must never conjure an empty database"
    )


@pytest.mark.parametrize("head", [None, ""])
def test_stamp_ignores_an_absent_head(tmp_path, monkeypatch, head):
    shared = _configure_shared_db(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    import asyncio

    asyncio.run(reconcile_repo_head_commit(repo, head))

    assert not shared.exists()
