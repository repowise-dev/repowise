"""PostgreSQL regression for issue #2162 — a long git_function_blame.symbol_id
must persist.

SQLite ignores ``VARCHAR`` length, so this never surfaced there. Only
PostgreSQL enforces it, and that is where a ``"{path}::{name}"`` past 512
characters — routine for a deeply-nested generated file — aborted the
persistence phase after the whole blame index had already been computed.

Skipped unless a PostgreSQL URL is configured, so a local ``pytest`` run is
unchanged::

    REPOWISE_TEST_PG_URL=postgresql+asyncpg://user@localhost:5432/repowise_test \\
        uv run pytest tests/integration/test_postgres_git_function_blame_symbol_id.py
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Base, GitFunctionBlame, Repository, _new_uuid

PG_URL = os.environ.get("REPOWISE_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="REPOWISE_TEST_PG_URL not set — PostgreSQL-only regression"
)

# Longer than the 512 the column used to declare. A deeply-nested generated
# file's "{path}::{name}" reaches this routinely.
LONG_PATH = "packages/generated/vendor/protobuf/" + "sub_module/" * 20
LONG_NAME = "process_" + "generated_protobuf_message_field_descriptor_handler_" * 6 + "value"
LONG_SYMBOL_ID = f"{LONG_PATH}::{LONG_NAME}"


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


async def test_long_symbol_id_round_trips(pg_session: AsyncSession) -> None:
    """git_function_blame.symbol_id accepts a "{path}::{name}" past 512 chars."""
    assert len(LONG_SYMBOL_ID) > 512

    now = datetime.now(UTC)
    repo = Repository(id=_new_uuid(), name="repro", local_path="/tmp/repro", url="")
    pg_session.add(repo)
    await pg_session.flush()

    pg_session.add(
        GitFunctionBlame(
            id=_new_uuid(),
            repository_id=repo.id,
            symbol_id=LONG_SYMBOL_ID,
            file_path=LONG_PATH,
            function_name=LONG_NAME,
            start_line=1,
            end_line=10,
            line_count=10,
            created_at=now,
            updated_at=now,
        )
    )
    await pg_session.commit()

    stored = await pg_session.scalar(
        select(GitFunctionBlame.symbol_id).where(GitFunctionBlame.repository_id == repo.id)
    )
    assert stored == LONG_SYMBOL_ID, "the symbol_id came back truncated"
