"""The hook ledger degrades to the file's journal mode, not to being off.

``_open_injections`` already swallowed every ``sqlite3.Error`` and cached
``None`` for the process, so a WAL switch that lost a lock race silently
turned the ledger off for every hook fire that followed. The switch failing
must leave the connection usable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from repowise.cli import hook_ledger


def test_open_injections_survives_a_contended_wal_switch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(hook_ledger, "_CONN", {})
    first = hook_ledger._open_injections(tmp_path)
    assert first is not None
    first.close()
    monkeypatch.setattr(hook_ledger, "_CONN", {})

    db_path = tmp_path / ".repowise" / "sessions" / "sessions.db"
    reset = sqlite3.connect(db_path)
    reset.execute("PRAGMA journal_mode=DELETE")
    reset.close()
    holder = sqlite3.connect(db_path, isolation_level=None)
    holder.execute("BEGIN")
    holder.execute("SELECT count(*) FROM sqlite_master")
    try:
        conn = hook_ledger._open_injections(tmp_path)
    finally:
        holder.close()
    assert conn is not None
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert conn.execute("SELECT count(*) FROM injections").fetchone()[0] == 0
    conn.close()
