"""The SQL percentile recompute against a real PostgreSQL container.

Skipped automatically when ``testcontainers`` is not installed or Docker is
not running. The values must match the SQLite path, and the UPDATE must rank
once rather than re-running a ranking per updated row (a quadratic plan that
never finished on a repo with tens of thousands of files).
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from repowise.core.persistence.crud import recompute_git_percentiles, upsert_git_metadata_bulk
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import GitMetadata
from tests.unit.persistence.helpers import insert_repo

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
async def pg_engine(postgres_container):
    raw_url = postgres_container.get_connection_url()
    url = raw_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    engine = create_async_engine(url, echo=False)
    try:
        await init_db(engine)
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def pg_session(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
        await session.rollback()


async def _stored(session, repo_id):
    rows = (
        (await session.execute(select(GitMetadata).where(GitMetadata.repository_id == repo_id)))
        .scalars()
        .all()
    )
    return {r.file_path: r for r in rows}


async def test_ranks_code_rows_only_and_ties_by_path(pg_session) -> None:
    repo = await insert_repo(pg_session)
    code = [
        {
            "file_path": f"m{i}.py",
            "temporal_hotspot_score": float(i) * 10,
            "commit_count_90d": 30,
            "change_entropy": 0.5,
            "co_change_mass": 2.0,
            "prior_defect_count": i % 2,
        }
        for i in range(4)
    ]
    docs = [
        {"file_path": f"d{i}.md", "history_only": True, "commit_count_90d": 50} for i in range(8)
    ]
    # Upper- and lower-case paths: ties rank in code-point order, not locale order.
    code.append({"file_path": "Z.py", "change_entropy": 0.5, "commit_count_90d": 1})
    await upsert_git_metadata_bulk(pg_session, repo.id, code + docs)

    assert await recompute_git_percentiles(pg_session, repo.id) == 13

    stored = await _stored(pg_session, repo.id)
    # Churn ranks the five code rows alone: Z.py (0, 1 commit) lowest.
    assert stored["Z.py"].churn_percentile == 0.0
    assert [stored[f"m{i}.py"].churn_percentile for i in range(4)] == pytest.approx(
        [0.25, 0.5, 0.75, 1.0]
    )
    assert [stored[f"m{i}.py"].is_hotspot for i in range(4)] == [False, False, True, True]
    # Tied entropy ranks by path in code-point order: "Z.py" < "m0.py" < ...
    assert stored["Z.py"].change_entropy_pct == 0.0
    assert [stored[f"m{i}.py"].change_entropy_pct for i in range(4)] == pytest.approx(
        [0.2, 0.4, 0.6, 0.8]
    )
    for path, row in stored.items():
        if row.history_only:
            assert (row.churn_percentile, row.is_hotspot, row.prior_defect_pct) == (
                0.0,
                False,
                0.0,
            ), path


async def test_update_ranks_once(pg_engine, pg_session) -> None:
    """The plan joins each ranking once; no per-row SubPlan re-runs a ranking."""
    repo = await insert_repo(pg_session)
    await upsert_git_metadata_bulk(
        pg_session,
        repo.id,
        [{"file_path": f"f{i}.py", "temporal_hotspot_score": float(i)} for i in range(50)],
    )
    plan: list[str] = []

    def explain(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().startswith("WITH ranked AS"):
            statement = "EXPLAIN " + statement
        return statement, params

    def capture(conn, cursor, statement, params, context, executemany):
        if statement.startswith("EXPLAIN "):
            plan.extend(row[0] for row in cursor.fetchall())

    event.listen(pg_engine.sync_engine, "before_cursor_execute", explain, retval=True)
    event.listen(pg_engine.sync_engine, "after_cursor_execute", capture)
    try:
        await recompute_git_percentiles(pg_session, repo.id)
    finally:
        event.remove(pg_engine.sync_engine, "before_cursor_execute", explain)
        event.remove(pg_engine.sync_engine, "after_cursor_execute", capture)

    assert plan and plan[0].startswith("Update on git_metadata")
    assert not any("SubPlan" in line for line in plan), "\n".join(plan)
