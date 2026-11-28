"""0139 adds the reports table and the answer-note columns, and drops them again."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command

from tests.unit.persistence.test_wiki_pages_content_hash_migration import _alembic_config

_NOTES = {"action_states": "note", "health_findings": "status_note", "refactoring_suggestions": "status_note"}


def _columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as db:
        return {row[1] for row in db.execute(f"pragma table_info({table})")}


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as db:
        return {row[0] for row in db.execute("select name from sqlite_master where type='table'")}


def test_0139_adds_and_drops_the_reports_table_and_note_columns(tmp_path: Path) -> None:
    path = tmp_path / "wiki.db"
    config = _alembic_config(path)
    command.upgrade(config, "0138")
    assert all(column not in _columns(path, t) for t, column in _NOTES.items())

    command.upgrade(config, "0139")
    assert "reports" in _tables(path)
    assert all(column in _columns(path, t) for t, column in _NOTES.items())

    command.downgrade(config, "0138")
    assert "reports" not in _tables(path)
    assert all(column not in _columns(path, t) for t, column in _NOTES.items())
