"""`health --recompute` writes only into an index that already exists (#6547).

A linked worktree with no index of its own reads its main checkout's (#5510).
A `.repowise/` created in it reads as an index of its own from then on, so the
hooks and MCP switched to an empty one. Built on a real ``git worktree add``.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli._helpers.paths import WORKTREE_FALLBACK_ENV
from repowise.cli.commands.health_cmd import health_command
from repowise.cli.helpers import run_async
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Repository


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


async def _build_index(checkout: Path) -> None:
    """An index of *checkout* holding its repository row and nothing else."""
    db_path = checkout / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    await init_db(engine)
    async with async_sessionmaker(engine)() as session:
        session.add(Repository(id="repo1", name="main", local_path=str(checkout)))
        await session.commit()
    await engine.dispose()


def _health_rows(checkout: Path) -> int:
    with sqlite3.connect(checkout / ".repowise" / "wiki.db") as conn:
        return conn.execute("select count(*) from health_file_metrics").fetchone()[0]


@pytest.fixture(autouse=True)
def _repo_local_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    monkeypatch.delenv(WORKTREE_FALLBACK_ENV, raising=False)


@pytest.fixture()
def main(tmp_path: Path) -> Path:
    """A repository with one commit, so ``git worktree add`` is possible."""
    repo = tmp_path / "main"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "dev@example.com"], repo)
    _git(["config", "user.name", "Dev"], repo)
    (repo / "a.py").write_text("def a(x):\n    if x:\n        return 1\n    return 2\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "init"], repo)
    return repo.resolve()


def _invoke(checkout: Path, *args: str):
    return CliRunner().invoke(health_command, [str(checkout), "--no-workspace", *args])


@pytest.mark.parametrize("args", [("--file", "a.py", "--recompute"), ("--recompute",)])
def test_a_worktree_without_an_index_keeps_reading_the_main_checkouts(main, args):
    run_async(_build_index(main))
    worktree = main.parent / "wt"
    _git(["worktree", "add", "-q", str(worktree), "-b", "feat"], main)

    result = _invoke(worktree, *args)

    assert result.exit_code == 0, result.output
    assert not (worktree / ".repowise").exists()
    # The branch's numbers are not saved over the main checkout's either.
    assert _health_rows(main) == 0
    assert "main checkout's stored coverage" in result.output


def test_a_checkout_without_any_index_gets_none(main):
    result = _invoke(main, "--recompute")

    assert result.exit_code == 0, result.output
    assert not (main / ".repowise").exists()
    assert "Run `repowise init` to keep it" in result.output


def test_a_checkout_with_its_own_index_still_saves(main):
    run_async(_build_index(main))

    result = _invoke(main, "--recompute")

    assert result.exit_code == 0, result.output
    assert "No index" not in result.output
    assert _health_rows(main) == 1


def test_a_worktree_does_not_upgrade_the_main_checkouts_older_index(main):
    """The main checkout's index is only borrowed: an older one is not upgraded from here."""
    run_async(_build_index(main))
    with sqlite3.connect(main / ".repowise" / "wiki.db") as conn:
        conn.execute("drop table coverage_files")
    worktree = main.parent / "wt"
    _git(["worktree", "add", "-q", str(worktree), "-b", "feat"], main)

    result = _invoke(worktree, "--recompute")

    assert result.exit_code == 0, result.output
    with sqlite3.connect(main / ".repowise" / "wiki.db") as conn:
        tables = {
            row[0] for row in conn.execute("select name from sqlite_master where type='table'")
        }
    assert "coverage_files" not in tables
