"""SqlJobStore against a real PostgreSQL container.

Skipped automatically when ``testcontainers`` is not installed or Docker
is not running. The test exercises the same surface as the unit-level
contract suite but against the dialect that production deployments use.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from repowise.core.persistence._interfaces import JobState
from repowise.core.persistence.database import init_db
from repowise.core.persistence.stores import SqlIndexStore, SqlJobStore

testcontainers = pytest.importorskip(
    "testcontainers.postgres",
    reason="testcontainers not installed",
)


@pytest.fixture(scope="module")
def postgres_container():
    try:
        with testcontainers.PostgresContainer("postgres:16-alpine") as pg:
            yield pg
    except Exception as exc:  # pragma: no cover - skip when Docker missing
        pytest.skip(f"PostgreSQL container unavailable: {exc}")


@pytest.fixture
async def pg_session(postgres_container):
    raw_url = postgres_container.get_connection_url()
    # testcontainers returns psycopg2-style; rewrite for asyncpg.
    url = raw_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    engine = create_async_engine(url, echo=False)
    try:
        await init_db(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_job_store_roundtrip_postgres(pg_session: AsyncSession):
    """Same contract as the SQLite unit test, but against Postgres."""
    idx = SqlIndexStore(pg_session)
    repo = await idx.upsert_repository(
        name="pg-test", local_path="/tmp/pg-test"
    )
    store = SqlJobStore(pg_session)

    job = await store.create_job(
        repository_id=repo.id, phase="graph", metadata={"k": 1}
    )
    assert job.state is JobState.PENDING

    running = await store.update_state(job.id, JobState.RUNNING)
    assert running.state is JobState.RUNNING

    cp = await store.checkpoint(job.id, "files/100")
    assert cp.cursor == "files/100"
    assert cp.state is JobState.RUNNING  # checkpoint does not flip state

    resumable = await store.find_resumable(repository_id=repo.id)
    assert len(resumable) == 1 and resumable[0].id == job.id

    done = await store.update_state(job.id, JobState.COMPLETED)
    assert done.state is JobState.COMPLETED
    assert (await store.find_resumable(repository_id=repo.id)) == []
