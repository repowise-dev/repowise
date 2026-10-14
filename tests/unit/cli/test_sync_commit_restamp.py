"""Every state.json writer leaves the repository row on the sync commit it wrote."""

from __future__ import annotations

import asyncio
import json
import subprocess
import types
from pathlib import Path

import pytest
from sqlalchemy import select


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


async def _seed_row(repo: Path, head: str) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from repowise.core.persistence import create_engine, init_db
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import Repository

    engine = create_engine(resolve_db_url(repo))
    try:
        await init_db(engine)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add(Repository(id="r1", name="r", local_path=str(repo), head_commit=head))
            await session.commit()
    finally:
        await engine.dispose()


async def _row_commit(repo: Path) -> str | None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from repowise.core.persistence import create_engine
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import Repository

    engine = create_engine(resolve_db_url(repo))
    try:
        async with async_sessionmaker(engine)() as session:
            return await session.scalar(select(Repository.head_commit))
    finally:
        await engine.dispose()


@pytest.fixture
def repo(tmp_path):
    """A checkout at ``new`` whose state.json and row both name its parent ``old``."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    for name in ("old", "new"):
        (tmp_path / name).write_text(name, encoding="utf-8")
        _git(tmp_path, "add", name)
        _git(tmp_path, "commit", "-qm", name)
    old = _git(tmp_path, "rev-parse", "HEAD~1")
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "state.json").write_text(
        json.dumps({"last_sync_commit": old}), encoding="utf-8"
    )
    asyncio.run(_seed_row(tmp_path, old))
    return tmp_path, old, _git(tmp_path, "rev-parse", "HEAD")


def _state_commit(repo: Path) -> str:
    return json.loads((repo / ".repowise" / "state.json").read_text())["last_sync_commit"]


def test_generate_leaves_both_stamps_on_one_commit(repo) -> None:
    from repowise.cli.commands.generate_cmd.command import _write_state

    path, _old, new = repo
    _write_state(
        path,
        {"last_sync_commit": _old},
        types.SimpleNamespace(provider_name="mock", model_name="m"),
        types.SimpleNamespace(total_pages=1, remaining_template_pages=1),
    )
    assert _state_commit(path) == new
    assert asyncio.run(_row_commit(path)) == new


def test_a_server_job_leaves_both_stamps_on_one_commit(repo) -> None:
    from repowise.server.job_state import _stamp_last_sync_commit

    path, _old, new = repo
    _stamp_last_sync_commit(path)
    assert _state_commit(path) == new
    assert asyncio.run(_row_commit(path)) == new


def test_a_write_that_keeps_the_sync_commit_leaves_the_row_alone(repo, monkeypatch) -> None:
    from repowise.cli.helpers import save_state
    from repowise.core.workspace import update

    path, old, _new = repo
    stamped: list[str] = []
    monkeypatch.setattr(
        update, "reconcile_repo_head_commit", lambda _p, head: stamped.append(head)
    )
    save_state(path, {"last_sync_commit": old, "total_pages": 3})
    assert stamped == []


def test_a_save_on_a_running_loop_skips_the_restamp_without_raising(repo) -> None:
    from repowise.cli.helpers import save_state

    path, old, new = repo

    async def _save() -> None:
        save_state(path, {"last_sync_commit": new})

    asyncio.run(asyncio.wait_for(_save(), timeout=10))
    assert _state_commit(path) == new
    assert asyncio.run(_row_commit(path)) == old


async def test_a_server_index_job_restamps_the_row_off_the_loop(repo) -> None:
    """The job executor writes state.json through asyncio.to_thread, so the
    restamp runs with no event loop of its own and is not skipped."""
    from repowise.server.job_executor import _write_index_state

    path, _old, new = repo
    run = types.SimpleNamespace(
        spec=types.SimpleNamespace(is_initial_index=False, repo_path=str(path), job_id="j")
    )
    await _write_index_state(run, types.SimpleNamespace(), total_pages=0)
    assert _state_commit(path) == new
    assert await _row_commit(path) == new
