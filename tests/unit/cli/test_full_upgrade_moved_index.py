"""``repowise update --full`` on an index copied to a new path.

The copied ``wiki.db`` keeps the old ``local_path``. The upgrade must reuse
that repository row, not add an empty one whose missing graph then fails the
rehydrate with a raw traceback.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
import pytest

from repowise.cli.commands import upgrade_flow as up_mod


def _seed_store(repo: Path, local_path: str) -> str:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    async def _run() -> str:
        engine = create_engine(f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}")
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            row = await upsert_repository(session, name=Path(local_path).name, local_path=local_path)
            repo_id = row.id
        await engine.dispose()
        return repo_id

    return asyncio.run(_run())


def _repo_rows(repo: Path) -> list[tuple[str, str]]:
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(repo / ".repowise" / "wiki.db")) as conn:
        return conn.execute("SELECT id, local_path FROM repositories").fetchall()


@pytest.fixture
def moved_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    repo = tmp_path / "repo-b"
    (repo / ".repowise").mkdir(parents=True)
    repo_id = _seed_store(repo, str(tmp_path / "repo-a"))
    return repo, repo_id


def test_open_repository_reuses_the_moved_row(moved_repo: tuple[Path, str]) -> None:
    repo, repo_id = moved_repo

    async def _open() -> str:
        engine, _sf, opened_id = await up_mod._open_repository(repo)
        await engine.dispose()
        return opened_id

    assert asyncio.run(_open()) == repo_id
    assert _repo_rows(repo) == [(repo_id, str(repo))]


def test_missing_graph_is_a_clear_error(
    moved_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = moved_repo

    async def _no_backfill(*a: object, **k: object) -> tuple[dict, None]:
        return {}, None

    monkeypatch.setattr(up_mod, "_backfill_git", _no_backfill)
    run = up_mod._run_upgrade(
        repo,
        None,
        None,
        exclude_patterns=[],
        commit_limit=None,
        follow_renames=False,
        embedder_name=None,
        yes=True,
    )
    with pytest.raises(click.ClickException, match="repowise init"):
        asyncio.run(run)


def test_a_store_with_several_repos_is_not_rebound(
    moved_repo: tuple[Path, str],
) -> None:
    repo, repo_id = moved_repo
    other_id = _seed_store(repo, str(repo.parent / "repo-c"))

    async def _open() -> str:
        engine, _sf, opened_id = await up_mod._open_repository(repo)
        await engine.dispose()
        return opened_id

    opened = asyncio.run(_open())
    assert opened not in (repo_id, other_id)
    assert (repo_id, str(repo.parent / "repo-a")) in _repo_rows(repo)


def test_a_configured_database_is_not_rebound(
    moved_repo: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, repo_id = moved_repo
    monkeypatch.setattr("repowise.core.persistence.get_configured_db_url", lambda: "sqlite://")
    monkeypatch.setattr("repowise.cli.helpers.get_db_url_for_repo", lambda _p: f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}")

    async def _open() -> str:
        engine, _sf, opened_id = await up_mod._open_repository(repo)
        await engine.dispose()
        return opened_id

    assert asyncio.run(_open()) != repo_id
    assert (repo_id, str(repo.parent / "repo-a")) in _repo_rows(repo)
