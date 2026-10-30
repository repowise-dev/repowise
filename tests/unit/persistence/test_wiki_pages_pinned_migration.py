"""Migration 0119: the ``pinned`` column on wiki_pages (issue #812)."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path


def _columns(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute('PRAGMA table_info("wiki_pages")')}
    finally:
        conn.close()


def _alembic_config(db_path: Path):
    from alembic.config import Config

    root = Path(__file__).resolve().parents[3] / "packages" / "core"
    config = Config()
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return config


def test_the_migration_adds_and_drops_the_column(tmp_path: Path) -> None:
    from alembic import command

    db_path = tmp_path / "wiki.db"
    config = _alembic_config(db_path)
    command.upgrade(config, "0119")
    assert "pinned" in _columns(db_path)
    command.downgrade(config, "0118")
    assert "pinned" not in _columns(db_path)


def test_the_migration_and_the_model_agree(tmp_path: Path) -> None:
    from alembic import command

    from repowise.core.persistence.database import create_engine, init_db

    migrated = tmp_path / "migrated.db"
    command.upgrade(_alembic_config(migrated), "head")
    declared = tmp_path / "declared.db"

    async def _build() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{declared}")
        await init_db(engine)
        await engine.dispose()

    asyncio.run(_build())
    assert _columns(migrated) == _columns(declared)
