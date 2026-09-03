"""PostgreSQL regression for issue #1565 — long symbol names must persist.

SQLite ignores ``VARCHAR`` length, so every SQLite test in this suite passes
whatever the declared width is. Only PostgreSQL enforces it, and that is where
a generated identifier past 255 characters aborted the persistence phase after
the whole index had been computed.

Skipped unless a PostgreSQL URL is configured, so a local ``pytest`` run is
unchanged::

    REPOWISE_TEST_PG_URL=postgresql+asyncpg://user@localhost:5432/repowise_test \\
        uv run pytest tests/integration/test_postgres_symbol_lengths.py
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import (
    Base,
    DeadCodeFinding,
    GraphNode,
    HealthFinding,
    RefactoringSuggestion,
    Repository,
    WikiSymbol,
    _new_uuid,
)

PG_URL = os.environ.get("REPOWISE_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="REPOWISE_TEST_PG_URL not set — PostgreSQL-only regression"
)

# Longer than the 255 the columns used to declare, and shorter than anything a
# btree index limit would care about. Real generated bindings reach this.
LONG_NAME = "process_" + "generated_protobuf_message_field_descriptor_" * 8 + "value"


@pytest.fixture
async def pg_session():
    # The fixture drops every table, so it refuses a database that is not
    # named like a scratch one. Pointing the env var at a real index and
    # losing it should take more than a typo.
    database = (PG_URL or "").rsplit("/", 1)[-1].split("?")[0]
    if "test" not in database and "scratch" not in database:
        pytest.skip(f"refusing to drop tables in {database!r}: name it *test* or *scratch*")

    engine = create_async_engine(PG_URL or "", poolclass=None)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await init_db(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_long_symbol_names_round_trip(pg_session: AsyncSession) -> None:
    """Every column that stores a symbol name accepts one 365 characters long."""
    assert len(LONG_NAME) > 255

    now = datetime.now(UTC)
    repo = Repository(id=_new_uuid(), name="repro", local_path="/tmp/repro", url="")
    pg_session.add(repo)
    await pg_session.flush()

    pg_session.add_all(
        [
            GraphNode(
                id=_new_uuid(),
                repository_id=repo.id,
                node_id=f"generated.py::{LONG_NAME}",
                node_type="symbol",
                name=LONG_NAME,
                qualified_name=LONG_NAME,
                file_path="generated.py",
                created_at=now,
            ),
            WikiSymbol(
                id=_new_uuid(),
                repository_id=repo.id,
                symbol_id=f"generated.py::{LONG_NAME}",
                name=LONG_NAME,
                qualified_name=LONG_NAME,
                kind="function",
                parent_name=LONG_NAME,
                file_path="generated.py",
            ),
            DeadCodeFinding(
                id=_new_uuid(),
                repository_id=repo.id,
                kind="unused_export",
                file_path="generated.py",
                symbol_name=LONG_NAME,
            ),
            HealthFinding(
                id=_new_uuid(),
                repository_id=repo.id,
                file_path="generated.py",
                biomarker_type="brain_method",
                severity="high",
                function_name=LONG_NAME,
            ),
            RefactoringSuggestion(
                id=_new_uuid(),
                repository_id=repo.id,
                refactoring_type="extract_method",
                file_path="generated.py",
                target_symbol=LONG_NAME,
            ),
        ]
    )
    await pg_session.commit()

    stored = await pg_session.scalar(select(WikiSymbol.name).where(WikiSymbol.repository_id == repo.id))
    assert stored == LONG_NAME, "the name came back truncated"
