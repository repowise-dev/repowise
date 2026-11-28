"""0150 widens evidence commit SHA columns to VARCHAR(64) for SHA-256 support."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command

from repowise.core.persistence.models import (
    EvidenceRun,
    EvidenceTestStats,
    SymbolHotness,
)
from tests.unit.persistence.test_wiki_pages_content_hash_migration import _alembic_config

_CHECK_COLUMNS = (
    ("evidence_runs", "commit_sha"),
    ("evidence_test_stats", "last_commit_sha"),
    ("symbol_hotness", "evidence_commit_sha"),
)


def _column_types(db_path: Path, table: str) -> dict[str, str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row[1]: row[2].upper() for row in rows}


def test_0150_widens_evidence_commit_sha_columns_to_64(tmp_path: Path) -> None:
    path = tmp_path / "wiki.db"
    config = _alembic_config(path)

    command.upgrade(config, "0149")
    for table, col in _CHECK_COLUMNS:
        types = _column_types(path, table)
        assert types[col] == "VARCHAR(40)"

    command.upgrade(config, "0150")
    for table, col in _CHECK_COLUMNS:
        types = _column_types(path, table)
        assert types[col] == "VARCHAR(64)"

    command.downgrade(config, "0149")
    for table, col in _CHECK_COLUMNS:
        types = _column_types(path, table)
        assert types[col] == "VARCHAR(40)"


def test_declared_model_columns_are_64_wide() -> None:
    assert EvidenceRun.__table__.c.commit_sha.type.length == 64
    assert EvidenceTestStats.__table__.c.last_commit_sha.type.length == 64
    assert SymbolHotness.__table__.c.evidence_commit_sha.type.length == 64
