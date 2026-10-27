"""``repo_index_session`` reconciles the schema only when the store is behind."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from repowise.cli import helpers


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """A current store with one repository row: ``(root, repo_id)``."""
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    root = tmp_path.resolve()
    (root / ".repowise").mkdir()

    async def seed() -> str:
        engine = create_engine(f"sqlite+aiosqlite:///{_db(root).as_posix()}")
        try:
            await init_db(engine)
            async with get_session(create_session_factory(engine)) as session:
                repo = await upsert_repository(session, name="r", local_path=str(root))
                await session.commit()
                return repo.id
        finally:
            await engine.dispose()

    return root, asyncio.run(seed())


def _db(root: Path) -> Path:
    return root / ".repowise" / "wiki.db"


def _drop_a_column(root: Path) -> None:
    """The store an older repowise wrote: one column the models declare is absent."""
    import sqlite3

    with sqlite3.connect(_db(root)) as conn:
        conn.execute("ALTER TABLE health_file_metrics DROP COLUMN analyzed_commit")


def _read(root: Path) -> str | None:
    async def go() -> str | None:
        async with helpers.repo_index_session(root) as opened:
            return None if opened is None else opened[1]

    return asyncio.run(go())


def _count_reconciles(monkeypatch: pytest.MonkeyPatch, *, repair: bool) -> list[str]:
    calls: list[str] = []
    real = helpers.reconcile_schema_best_effort

    async def counting(url: str) -> None:
        calls.append(url)
        if repair:
            await real(url)

    monkeypatch.setattr(helpers, "reconcile_schema_best_effort", counting)
    return calls


def test_a_current_store_opens_without_a_reconcile(store, monkeypatch) -> None:
    root, repo_id = store
    calls = _count_reconciles(monkeypatch, repair=True)
    assert _read(root) == repo_id
    assert calls == []


def test_a_store_missing_a_column_is_reconciled_once(store, monkeypatch) -> None:
    root, repo_id = store
    _drop_a_column(root)
    calls = _count_reconciles(monkeypatch, repair=True)
    assert _read(root) == repo_id
    assert len(calls) == 1
    # Repaired in place, so the next read pays nothing.
    assert _read(root) == repo_id
    assert len(calls) == 1


def test_a_store_the_reconcile_cannot_repair_names_the_fix(store, monkeypatch) -> None:
    root, _ = store
    _drop_a_column(root)
    _count_reconciles(monkeypatch, repair=False)
    with pytest.raises(helpers.StaleIndexError, match="run `repowise update`"):
        _read(root)
