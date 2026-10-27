"""The materialized opportunity store: migration shape and model agreement."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    finally:
        conn.close()


def _indexes(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f'PRAGMA index_list("{table}")')}
    finally:
        conn.close()


def _alembic_config(db_path: Path):
    from alembic.config import Config

    root = Path(__file__).resolve().parents[3] / "packages" / "core"
    # Built without the ini file on purpose; see the performance store's twin.
    config = Config()
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return config


def test_the_migration_upgrades_and_rolls_back(tmp_path: Path) -> None:
    from alembic import command

    db_path = tmp_path / "wiki.db"
    config = _alembic_config(db_path)

    command.upgrade(config, "0059")
    assert {"refactoring_opportunities", "refactoring_summaries"} <= _tables(db_path)
    assert {
        "opportunity_id",
        "refactoring_model_version",
        "status",
        "rank_position",
        "queue_position",
        "addresses_primary_problem",
        "details_json",
    } <= _columns(db_path, "refactoring_opportunities")

    command.downgrade(config, "0058")
    assert not {"refactoring_opportunities", "refactoring_summaries"} & _tables(db_path)


def test_the_migration_and_the_model_declare_the_same_shape(tmp_path: Path) -> None:
    """A local store is built by ``init_db`` and never sees Alembic.

    So the two descriptions have to agree, or a hosted store and a local one
    disagree about the table this phase serves everything from.
    """
    import asyncio

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

    for table in ("refactoring_opportunities", "refactoring_summaries"):
        assert _columns(migrated, table) == _columns(declared, table), table
    # Index names too: the queue reads through them by name in the query plan.
    assert _indexes(migrated, "refactoring_opportunities") == _indexes(
        declared, "refactoring_opportunities"
    )
