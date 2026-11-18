"""Migration 0124: the body digest, its size and the lineage id on ``function_facts``."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

_ADDED = {"body_digest", "body_tokens", "lineage_id"}


def _columns(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute('PRAGMA table_info("function_facts")')}
    finally:
        conn.close()


def _alembic_config(db_path: Path):
    from alembic.config import Config

    root = Path(__file__).resolve().parents[3] / "packages" / "core"
    config = Config()
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return config


def test_the_migration_adds_and_drops_the_columns(tmp_path: Path) -> None:
    from alembic import command

    db_path = tmp_path / "wiki.db"
    config = _alembic_config(db_path)
    command.upgrade(config, "0124")
    assert _columns(db_path) >= _ADDED
    command.downgrade(config, "0123")
    assert not _ADDED & _columns(db_path)


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
