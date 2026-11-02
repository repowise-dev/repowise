"""A linked worktree's ``update`` stores that worktree's HEAD.

``repositories.head_commit`` used to be read only from a ``.git`` directory.
In a linked worktree ``.git`` is a ``gitdir:`` file, so the row kept the
commit it was seeded with and ``impacted-tests`` treated the index as
untrustworthy. After a commit and ``update``, the row matches
``git rev-parse HEAD``.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(args: list[str], cwd: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@e.x",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@e.x",
        }
    )
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def _rev_parse(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", "rev-parse", *args], cwd=cwd, text=True).strip()


def _remove_worktree(base_repo: Path, worktree_dir: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_dir)],
        cwd=base_repo,
        capture_output=True,
    )
    shutil.rmtree(worktree_dir, ignore_errors=True)
    subprocess.run(["git", "worktree", "prune"], cwd=base_repo, capture_output=True)


def _head_commit(db_path: Path, local_path: Path) -> str | None:
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT head_commit FROM repositories WHERE local_path = ?",
            (str(local_path),),
        ).fetchone()
    return None if row is None else row[0]


def test_worktree_update_stores_its_own_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed at the base commit, commit in the worktree, then ``update``.

    A staged edit of the module the one test imports is then selected by
    ``impacted-tests --staged`` instead of ``:all``.
    """
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(
        "from pkg.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    _git(["init", "-b", "main"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "init"], repo)

    runner = CliRunner()
    init_base = runner.invoke(cli, ["init", str(repo), "--index-only"], catch_exceptions=False)
    assert init_base.exit_code == 0, init_base.output

    worktree = tmp_path / "repo-wt"
    _git(["worktree", "add", "-b", "feat", str(worktree)], repo)
    try:
        init_wt = runner.invoke(
            cli, ["init", str(worktree), "--index-only"], catch_exceptions=False
        )
        assert init_wt.exit_code == 0, init_wt.output

        seeded = _head_commit(worktree / ".repowise" / "wiki.db", worktree)
        assert seeded == _rev_parse(worktree, "HEAD")

        calc = worktree / "pkg" / "calc.py"
        calc.write_text("def add(a, b):\n    return a + b + 0\n", encoding="utf-8")
        _git(["add", "pkg/calc.py"], worktree)
        _git(["commit", "-m", "tweak"], worktree)
        parent = _rev_parse(worktree, "HEAD~1")
        head = _rev_parse(worktree, "HEAD")
        assert head != parent

        updated = runner.invoke(
            cli, ["update", str(worktree), "--index-only"], catch_exceptions=False
        )
        assert updated.exit_code == 0, updated.output
        assert "Already up to date" not in updated.output
        assert "No changed files detected." not in updated.output

        stored = _head_commit(worktree / ".repowise" / "wiki.db", worktree)
        assert stored == head, updated.output
        assert stored != parent

        # The disagreement this bug produced made --staged select every test.
        # Stage one line of the module the test imports and ask again.
        calc.write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")
        _git(["add", "pkg/calc.py"], worktree)
        selected = runner.invoke(
            cli,
            ["impacted-tests", "--staged", "--format", "args", "--path", str(worktree)],
            catch_exceptions=False,
        )
        assert selected.exit_code == 0, selected.output
        # stdout is the runner args; the "1 argument(s)" line is stderr, and
        # CliRunner reports them together. ``:all`` is the full-run sentinel.
        assert selected.output.splitlines()[0].strip() == "tests/test_calc.py", selected.output
        assert ":all" not in selected.output
        assert "Run every test" not in selected.output
    finally:
        _remove_worktree(repo, worktree)
