"""Migration 0114: the renamed biomarker id on every column that stores one."""

from __future__ import annotations

import sqlite3
from pathlib import Path

OLD = "bumpy_road"
NEW = "repeated_nesting"

_COLUMNS = (
    ("health_findings", "biomarker_type"),
    ("git_commit_health_findings", "biomarker_type"),
    ("performance_opportunities", "biomarker_type"),
    ("refactoring_suggestions", "source_biomarker"),
    ("refactoring_opportunities", "lead_biomarker"),
)


def _alembic_config(db_path: Path):
    from alembic.config import Config

    root = Path(__file__).resolve().parents[3] / "packages" / "core"
    config = Config()
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return config


def _filler(table: str, name: str, kind: str | None) -> object:
    numeric = any(k in (kind or "").upper() for k in ("INT", "FLOAT", "BOOL"))
    return 0 if numeric else f"{table}-{name}"


def _seed(db_path: Path) -> None:
    """One row per column holding the old id, with every NOT NULL column filled."""
    conn = sqlite3.connect(db_path)
    try:
        for table, column in _COLUMNS:
            info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            values = {
                name: OLD if name == column else _filler(table, name, kind)
                for _, name, kind, notnull, default, _pk in info
                if name == column or (notnull and default is None)
            }
            names = ", ".join(f'"{n}"' for n in values)
            marks = ", ".join("?" for _ in values)
            conn.execute(f'INSERT INTO "{table}" ({names}) VALUES ({marks})', list(values.values()))
        conn.commit()
    finally:
        conn.close()


def _stored(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {conn.execute(f'SELECT "{c}" FROM "{t}"').fetchone()[0] for t, c in _COLUMNS}
    finally:
        conn.close()


def test_the_migration_renames_and_restores_the_id(tmp_path: Path) -> None:
    from alembic import command

    db_path = tmp_path / "wiki.db"
    config = _alembic_config(db_path)
    command.upgrade(config, "0113")
    _seed(db_path)

    command.upgrade(config, "0114")
    assert _stored(db_path) == {NEW}
    command.downgrade(config, "0113")
    assert _stored(db_path) == {OLD}
