"""Workspace status reads each member's index time from its own store.

A member updated on its own leaves the workspace config's ``indexed_at`` at
the last workspace pass, which made ``repowise status`` report "2d ago" for a
repo synced hours earlier.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from repowise.core.workspace.config import RepoEntry, WorkspaceConfig


def _store(repo: Path) -> None:
    db = repo / ".repowise" / "wiki.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE repositories (updated_at TEXT)")
        conn.execute("INSERT INTO repositories VALUES ('2026-10-20 19:21:15')")


def test_status_rows_report_the_store_sync_time(tmp_path, monkeypatch) -> None:
    from repowise.cli.commands import status_cmd

    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.setattr(status_cmd, "_query_repo_counts", lambda _p: (1, 1))
    monkeypatch.setattr(status_cmd, "_query_page_count", lambda _p: 0)
    _store(tmp_path / "api")
    ws = WorkspaceConfig(
        repos=[RepoEntry(path="api", alias="api", indexed_at="2026-10-19T13:57:00+00:00")],
        default_repo="api",
    )

    rows = status_cmd._workspace_rows(SimpleNamespace(ws_root=tmp_path, ws_config=ws))

    assert rows[0]["indexed_at"] == "2026-10-20T19:21:15+00:00"
