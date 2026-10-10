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
