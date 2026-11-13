"""Migration 0124: index (repository_id, name) on wiki_symbols and graph_nodes.

Asserts both indexes exist after Alembic upgrade and after init_db.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from repowise.core.persistence.database import init_db


def _alembic_config(db_path: Path):
    from alembic.config import Config

    root = Path(__file__).resolve().parents[3] / "packages" / "core"
    config = Config()
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return config


def _index_columns(db_path: Path, index_name: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(f'PRAGMA index_info("{index_name}")')
        return [row[2] for row in cur.fetchall()]
    finally:
        conn.close()


def _table_indexes(db_path: Path, table_name: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(f'PRAGMA index_list("{table_name}")')
        return {row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def test_migration_0124_upgrade_and_downgrade(tmp_path: Path) -> None:
    from alembic import command

    db_path = tmp_path / "wiki.db"
    config = _alembic_config(db_path)

    command.upgrade(config, "0123")
    assert "ix_wiki_symbols_repo_name" not in _table_indexes(db_path, "wiki_symbols")
    assert "ix_graph_nodes_repo_name" not in _table_indexes(db_path, "graph_nodes")

    command.upgrade(config, "0124")
    assert "ix_wiki_symbols_repo_name" in _table_indexes(db_path, "wiki_symbols")
    assert "ix_graph_nodes_repo_name" in _table_indexes(db_path, "graph_nodes")
    assert _index_columns(db_path, "ix_wiki_symbols_repo_name") == ["repository_id", "name"]
    assert _index_columns(db_path, "ix_graph_nodes_repo_name") == ["repository_id", "name"]

    command.downgrade(config, "0123")
    assert "ix_wiki_symbols_repo_name" not in _table_indexes(db_path, "wiki_symbols")
    assert "ix_graph_nodes_repo_name" not in _table_indexes(db_path, "graph_nodes")


@pytest.mark.asyncio
async def test_init_db_creates_repo_name_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "init_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_db(engine)
    await engine.dispose()

    assert "ix_wiki_symbols_repo_name" in _table_indexes(db_path, "wiki_symbols")
    assert "ix_graph_nodes_repo_name" in _table_indexes(db_path, "graph_nodes")
    assert _index_columns(db_path, "ix_wiki_symbols_repo_name") == ["repository_id", "name"]
    assert _index_columns(db_path, "ix_graph_nodes_repo_name") == ["repository_id", "name"]
