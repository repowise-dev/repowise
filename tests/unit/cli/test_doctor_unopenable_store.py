"""A corrupt ``wiki.db`` must fail fast with a named recovery, not hang.

Store helpers used to call ``engine.dispose()`` only on success, so a failed
open left an aiosqlite worker thread alive and the interpreter blocked at
shutdown: doctor printed every row and then never exited, and init crashed
with a traceback and also hung. Every site below now disposes in ``finally``,
and both commands name the same recovery.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import git as gitpython
import pytest
from sqlalchemy.exc import SQLAlchemyError

from repowise.cli._repo_session import open_repo_db
from repowise.cli.commands.doctor_cmd._checks.context import CheckContext
from repowise.cli.commands.doctor_cmd._checks.core import _count_pages, _database_rows
from repowise.cli.commands.doctor_cmd._checks.providers import _read_stale_counts
from repowise.cli.commands.doctor_cmd._checks.stores import (
    _coordinator_health,
    _reconcile,
)
from repowise.cli.commands.doctor_cmd._repair import _repair_stores


def _repo(tmp_path: Path) -> Path:
    repo = (tmp_path / "repo").resolve()
    repo.mkdir()
    gitpython.Repo.init(repo)
    repowise_dir = repo / ".repowise"
    repowise_dir.mkdir()
    (repowise_dir / "wiki.db").write_bytes(b"this is not a database file")
    return repo


def _ctx(repo: Path) -> CheckContext:
    return CheckContext(repo_path=repo, repowise_dir=repo / ".repowise")


def test_database_row_names_path_and_recovery(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    row = _database_rows(_ctx(repo))[0]
    assert row.ok is False
    assert row.detail.startswith("cannot open index at ")
    assert "wiki.db" in row.detail
    assert "file is not a database" in row.detail
    assert row.fix is not None
    assert "wiki.db-wal" in row.fix and "wiki.db-shm" in row.fix
    assert row.fix.endswith("repowise init")


@pytest.mark.parametrize(
    "coro_fn",
    [_count_pages, _read_stale_counts],
)
def test_doctor_store_reads_raise_on_garbage_db(tmp_path: Path, coro_fn) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(SQLAlchemyError):
        asyncio.run(coro_fn(repo))


def test_reconcile_and_coordinator_raise_on_garbage_db(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(SQLAlchemyError):
        asyncio.run(_reconcile(repo, repo / ".repowise"))
    with pytest.raises(SQLAlchemyError):
        asyncio.run(_coordinator_health(repo, repo / ".repowise"))


def test_repair_raises_on_garbage_db(tmp_path: Path) -> None:
    from repowise.cli.commands.doctor_cmd._checks.context import StoreDrift

    repo = _repo(tmp_path)
    ctx = _ctx(repo)
    # Empty drift repairs nothing and never touches the store; give it a
    # missing FTS entry so the repair actually queries the broken database.
    ctx.drift = StoreDrift(missing_from_fts={"file_page:kept.py"})
    with pytest.raises(SQLAlchemyError):
        asyncio.run(_repair_stores(ctx))


def test_open_repo_db_disposes_on_failed_open(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(SQLAlchemyError):
        asyncio.run(open_repo_db(repo))


def test_init_generation_names_recovery_instead_of_traceback(tmp_path: Path) -> None:
    import click

    from repowise.cli.commands.init_cmd._generation_persist import (
        run_generation_with_persistence,
    )

    repo = _repo(tmp_path)
    with pytest.raises(click.ClickException) as excinfo:
        asyncio.run(
            run_generation_with_persistence(repo_path=repo, repo_name="repo")
        )
    message = excinfo.value.format_message()
    assert "Cannot open the index at " in message
    assert "wiki.db" in message
    assert "repowise init" in message


def test_init_resume_controller_names_recovery(tmp_path: Path) -> None:
    import click

    from repowise.cli.commands.init_cmd.persistence import build_resume_controller

    repo = _repo(tmp_path)
    with pytest.raises(click.ClickException) as excinfo:
        asyncio.run(build_resume_controller(repo, resume=False))
    message = excinfo.value.format_message()
    assert "Cannot open the index at " in message
    assert "repowise init" in message
