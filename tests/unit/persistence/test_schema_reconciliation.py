"""Regression tests for schema reconciliation in ``init_db``.

Covers the bug where a user who indexed a repo with an older repowise version
would hit ``no such column: decision_records.verification`` after upgrading,
because ``Base.metadata.create_all`` does not ALTER existing tables. The fix
reconciles columns + indexes against the model on every ``init_db`` call.

These tests pin the contract generically — anything a future additive
migration adds to the model (column, index, table) must be picked up by
``init_db`` without per-migration code in ``database.py``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from repowise.core.persistence import create_engine, init_db
from repowise.core.persistence.models import Base


def _table_columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    finally:
        conn.close()


def _table_indexes(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f'PRAGMA index_list("{table}")')}
    finally:
        conn.close()


def _table_exists(db_path: Path, table: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _execute(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _fetchall(db_path: Path, sql: str) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(db_path)
    try:
        return list(conn.execute(sql))
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_fresh_db_has_all_model_columns(tmp_path: Path) -> None:
    """A brand-new DB should mirror Base.metadata exactly — no drift between
    what the ORM expects and what create_all built."""
    db_path = tmp_path / "wiki.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
    finally:
        await engine.dispose()

    for table in Base.metadata.tables.values():
        assert _table_exists(db_path, table.name), f"missing table: {table.name}"
        db_cols = _table_columns(db_path, table.name)
        for column in table.columns:
            assert column.name in db_cols, f"{table.name}.{column.name} missing after fresh init_db"


@pytest.mark.asyncio
async def test_legacy_db_missing_column_is_reconciled(tmp_path: Path) -> None:
    """The actual bug scenario: simulate a pre-migration DB by dropping the
    ``verification`` column from a fresh ``decision_records`` table, then run
    ``init_db`` again and verify it was added back."""
    db_path = tmp_path / "wiki.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
    finally:
        await engine.dispose()

    # Simulate a legacy DB by dropping the column SQLAlchemy uses today.
    # SQLite ≥ 3.35 supports DROP COLUMN; on older builds the test is skipped.
    try:
        _execute(db_path, 'ALTER TABLE "decision_records" DROP COLUMN "verification"')
    except sqlite3.OperationalError as exc:
        pytest.skip(f"SQLite build doesn't support DROP COLUMN: {exc}")

    assert "verification" not in _table_columns(db_path, "decision_records")

    # Re-run init_db — reconciliation must restore the missing column.
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
    finally:
        await engine.dispose()

    assert "verification" in _table_columns(db_path, "decision_records"), (
        "init_db did not reconcile missing column"
    )


@pytest.mark.asyncio
async def test_reconciler_preserves_existing_row_data(tmp_path: Path) -> None:
    """Reconciling a missing column on a populated table must not lose
    pre-existing rows — the ALTER TABLE ADD COLUMN should back-fill with
    the model's server_default."""
    db_path = tmp_path / "wiki.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
    finally:
        await engine.dispose()

    # Drop verification and seed a row that has no value for it.
    try:
        _execute(db_path, 'ALTER TABLE "decision_records" DROP COLUMN "verification"')
    except sqlite3.OperationalError as exc:
        pytest.skip(f"SQLite build doesn't support DROP COLUMN: {exc}")

    # Cover every NOT NULL column on decision_records — we're bypassing the
    # ORM (which would apply Python defaults), so the raw INSERT must list
    # every column the model marks not-null. Keep this in sync with models.py
    # if new NOT NULL columns are added without server_default.
    _execute(
        db_path,
        """
        INSERT INTO decision_records
            (id, repository_id, title, status, context, decision, rationale,
             alternatives_json, consequences_json, affected_files_json,
             affected_modules_json, tags_json, evidence_commits_json,
             source, confidence, staleness_score, created_at, updated_at)
        VALUES
            ('rec-1', 1, 't', 'active', '', 'd', '',
             '[]', '[]', '[]',
             '[]', '[]', '[]',
             'inline', 0.5, 0.0, '2026-01-01', '2026-01-01')
        """,
    )

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
    finally:
        await engine.dispose()

    rows = _fetchall(db_path, "SELECT id, verification FROM decision_records")
    assert len(rows) == 1
    assert rows[0][0] == "rec-1"
    # ``verification`` has server_default="unverified" — back-filled rows get it.
    assert rows[0][1] == "unverified"


@pytest.mark.asyncio
async def test_legacy_db_missing_table_is_created(tmp_path: Path) -> None:
    """Tables added in a later release (e.g. ``decision_evidence``) must
    appear on an upgraded DB — covered by create_all but pinned here so the
    reconciliation contract is regression-tested end-to-end."""
    db_path = tmp_path / "wiki.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
    finally:
        await engine.dispose()

    _execute(db_path, 'DROP TABLE IF EXISTS "decision_evidence"')
    assert not _table_exists(db_path, "decision_evidence")

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
    finally:
        await engine.dispose()

    assert _table_exists(db_path, "decision_evidence")


@pytest.mark.asyncio
async def test_legacy_db_missing_index_is_recreated(tmp_path: Path) -> None:
    """Indexes declared on the model that were dropped from a legacy DB
    must be re-created — proves the reconciler covers index drift too."""
    db_path = tmp_path / "wiki.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
    finally:
        await engine.dispose()

    # Pick any model-declared index to drop.
    candidate_table = None
    candidate_index = None
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            if index.name and not index.name.startswith("sqlite_"):
                candidate_table = table.name
                candidate_index = index.name
                break
        if candidate_index:
            break
    assert candidate_index, "no model-declared index found in metadata"

    _execute(db_path, f'DROP INDEX IF EXISTS "{candidate_index}"')
    assert candidate_index not in _table_indexes(db_path, candidate_table)

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
    finally:
        await engine.dispose()

    assert candidate_index in _table_indexes(db_path, candidate_table), (
        f"reconciler did not re-create index {candidate_index}"
    )


@pytest.mark.asyncio
async def test_init_db_is_idempotent(tmp_path: Path) -> None:
    """Running init_db twice in a row must not raise, and the second call
    must not duplicate columns or indexes."""
    db_path = tmp_path / "wiki.db"

    for _ in range(2):
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            await init_db(engine)
        finally:
            await engine.dispose()

    cols = _table_columns(db_path, "decision_records")
    # Each name appears exactly once in PRAGMA table_info — set semantics
    # would hide duplicates, so explicitly query the raw count too.
    rows = _fetchall(db_path, 'SELECT name FROM pragma_table_info("decision_records")')
    assert len(rows) == len(cols), "duplicate column names after re-running init_db"


@pytest.mark.asyncio
async def test_reconciler_handles_arbitrary_new_column(tmp_path: Path) -> None:
    """Forward-compat contract: simulate a *future* migration by dropping any
    server_default'd column and verifying init_db adds it back, regardless of
    which table or column it is. Drives the test off Base.metadata rather
    than hard-coding ``verification`` so the contract is generic."""
    db_path = tmp_path / "wiki.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
    finally:
        await engine.dispose()

    # Find any non-PK column the reconciler can safely back-fill: either it
    # has a server_default, or it has a static Python default the reconciler
    # synthesizes a DEFAULT clause from, or it's nullable. Anything else is
    # genuinely unsafe to back-fill onto a populated table.
    target_table = None
    target_column = None
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if column.primary_key:
                continue
            backfillable = (
                column.nullable
                or column.server_default is not None
                or (
                    column.default is not None
                    and getattr(column.default, "arg", None) is not None
                    and not callable(getattr(column.default, "arg", None))
                )
            )
            if not backfillable:
                continue
            target_table = table.name
            target_column = column.name
            break
        if target_column:
            break
    assert target_column, "metadata has no back-fillable non-PK column"

    try:
        _execute(db_path, f'ALTER TABLE "{target_table}" DROP COLUMN "{target_column}"')
    except sqlite3.OperationalError as exc:
        pytest.skip(f"SQLite build doesn't support DROP COLUMN: {exc}")

    assert target_column not in _table_columns(db_path, target_table)

    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
    finally:
        await engine.dispose()

    assert target_column in _table_columns(db_path, target_table)
