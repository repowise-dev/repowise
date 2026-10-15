"""Index and update leave the read snapshots behind for the next reader."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
from pathlib import Path

from repowise.cli.commands.update_cmd.persistence import refresh_read_snapshots


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), capture_output=True, check=True)


def _kinds(repo: Path) -> list[str]:
    with sqlite3.connect(repo / ".repowise" / "wiki.db") as db:
        return sorted(r[0] for r in db.execute("SELECT kind FROM read_snapshots"))


def _payload(repo: Path, kind: str) -> dict:
    with sqlite3.connect(repo / ".repowise" / "wiki.db") as db:
        row = db.execute("SELECT payload_json FROM read_snapshots WHERE kind = ?", (kind,)).fetchone()
    return json.loads(row[0])


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
    assert _kinds(repo) == ["actions", "fix_first", "fix_first_counts"]
    counts = _payload(repo, "fix_first_counts")

    with sqlite3.connect(repo / ".repowise" / "wiki.db") as db:
        db.execute("DELETE FROM read_snapshots")
    refresh_read_snapshots(repo)
    assert _kinds(repo) == ["actions", "fix_first", "fix_first_counts"]
    # The restored item counts are the ones the index stored.
    assert _payload(repo, "fix_first_counts") == counts


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


def test_a_snapshot_not_written_is_named_with_its_error(tmp_path: Path, monkeypatch, capsys) -> None:
    """The update keeps the core log at ERROR, so the refresh says what failed itself."""
    from contextlib import asynccontextmanager

    from repowise.cli import helpers
    from repowise.core.persistence.crud.analysis import actions

    @asynccontextmanager
    async def opened(_root):
        yield (object(), "r1")

    async def failing(_session, _repo_id):
        return [("component", TypeError("key must be bytes [red]x[/red]\n  long traceback"))]

    monkeypatch.setattr(helpers, "repo_index_session", opened)
    monkeypatch.setattr(actions, "write_read_snapshots", failing)
    refresh_read_snapshots(tmp_path)
    shown = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("Snapshot not written")]
    # Escaped, first line only; the full error goes to the debug log.
    assert shown == ["Snapshot not written: component: TypeError: key must be bytes [red]x[/red]"]

    @asynccontextmanager
    async def broken(_root):
        raise OSError("database is locked")
        yield

    monkeypatch.setattr(helpers, "repo_index_session", broken)
    refresh_read_snapshots(tmp_path)
    assert "read snapshots: OSError: database is locked" in capsys.readouterr().out
