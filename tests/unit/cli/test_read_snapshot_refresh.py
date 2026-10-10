"""Index and update leave the read snapshots behind for the next reader."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
from pathlib import Path

from repowise.cli.commands.update_cmd.persistence import refresh_read_snapshots


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), capture_output=True, check=True)


def _kinds(repo: Path) -> list[str]:
    with sqlite3.connect(repo / ".repowise" / "wiki.db") as db:
        return sorted(r[0] for r in db.execute("SELECT kind FROM read_snapshots"))


def test_index_writes_and_update_restores_the_snapshots(tmp_path: Path) -> None:
    from repowise.core.pipeline.full_index import index_repo_full

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("def run(x):\n    return x + 1\n")
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "c0")
    (repo / ".repowise").mkdir()

    asyncio.run(index_repo_full(repo))
    assert _kinds(repo) == ["actions", "fix_first"]

    with sqlite3.connect(repo / ".repowise" / "wiki.db") as db:
        db.execute("DELETE FROM read_snapshots")
    refresh_read_snapshots(repo)
    assert _kinds(repo) == ["actions", "fix_first"]


def test_every_update_outcome_ends_in_one_helper() -> None:
    """Each outcome of ``repowise update`` refreshes the views through one exit."""
    import inspect

    from repowise.cli.commands.update_cmd import command

    source = inspect.getsource(command)
    assert source.count("_refresh_editor_stamp(repo_path") == 1  # only inside the helper
    assert source.count("refresh_read_snapshots(repo_path)") == 1
    assert source.count("_finish_outcome(repo_path") == 5


def test_the_up_to_date_outcome_refreshes_the_views(tmp_path: Path, monkeypatch) -> None:
    from repowise.cli.commands.update_cmd import command as upd_cmd
    from tests.unit.cli.test_update_up_to_date_lock import (
        _indexed_repo,
        _install_recorders,
        _invoke_update,
    )

    repo, _head = _indexed_repo(tmp_path)
    calls: dict[str, int] = {}
    _install_recorders(monkeypatch, calls)
    monkeypatch.setattr(upd_cmd, "try_acquire_update_lock", lambda *_: None)
    monkeypatch.setattr(
        upd_cmd, "refresh_read_snapshots", lambda _p: calls.update(views=calls.get("views", 0) + 1)
    )
    _invoke_update(repo)
    assert calls.get("views") == 1
