"""A member's index time comes from its own store, never the workspace config.

A repo updated on its own leaves ``indexed_at`` in the workspace config at the
last workspace pass, so every workspace surface reads the store's sync stamp.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.core.workspace.update import (
    read_index_synced_at,
    stamp_workspace_entry,
    sync_workspace_state_from_disk,
)

_SYNCED = "2026-10-20T19:21:15.667232+00:00"


@pytest.fixture(autouse=True)
def _repo_local_store(monkeypatch):
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)


def _store(repo: Path, stamp: str | None = "2026-10-20 19:21:15.667232") -> Path:
    db = repo / ".repowise" / "wiki.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE repositories (updated_at TEXT)")
        if stamp is not None:
            conn.execute("INSERT INTO repositories VALUES (?)", (stamp,))
    return repo


def test_reads_the_store_stamp_as_utc(tmp_path: Path) -> None:
    assert read_index_synced_at(_store(tmp_path)) == _SYNCED


def test_no_store_or_no_row_is_none(tmp_path: Path) -> None:
    assert read_index_synced_at(tmp_path) is None
    assert read_index_synced_at(_store(tmp_path, stamp=None)) is None


def test_reconcile_replaces_a_stale_config_time(tmp_path: Path) -> None:
    _store(tmp_path / "api")
    ws = WorkspaceConfig(
        repos=[RepoEntry(path="api", alias="api", indexed_at="2026-10-19T13:57:00+00:00")]
    )

    assert sync_workspace_state_from_disk(tmp_path, ws, save_if_changed=False) == ["api"]
    assert ws.get_repo("api").indexed_at == _SYNCED
    # Agreeing values are not drift.
    assert sync_workspace_state_from_disk(tmp_path, ws, save_if_changed=False) == []


def test_stamp_workspace_entry_uses_the_store_time(tmp_path: Path) -> None:
    entry = SimpleNamespace(indexed_at=None, last_commit_at_index=None)
    stamp_workspace_entry(entry, _store(tmp_path), "c" * 40)
    assert (entry.indexed_at, entry.last_commit_at_index) == (_SYNCED, "c" * 40)


def test_shared_store_reads_this_members_row(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "shared.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE repositories (local_path TEXT, updated_at TEXT)")
        conn.executemany(
            "INSERT INTO repositories VALUES (?, ?)",
            [("/other", "2026-10-01 00:00:00"), (str(tmp_path), "2026-10-20 19:21:15.667232")],
        )
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db.as_posix()}")
    assert read_index_synced_at(tmp_path) == _SYNCED


def test_unreadable_stamps_are_none(tmp_path: Path, monkeypatch) -> None:
    from repowise.core.workspace.update import synced_at_iso

    assert synced_at_iso("not a time") is None
    monkeypatch.setenv("REPOWISE_DB_URL", "postgresql://u@h/db")
    assert read_index_synced_at(tmp_path) is None
