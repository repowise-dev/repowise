"""Cost history queries must work on SQLite and compile portably for PostgreSQL."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.core.generation.cost_tracker import CostTracker
from repowise.core.persistence.models import LlmCost, Repository
from repowise.server.routers.costs import list_costs


@pytest_asyncio.fixture
async def cost_sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Repository.__table__.create)
            await connection.run_sync(LlmCost.__table__.create)
        async with factory() as session:
            session.add_all(
                [Repository(id=repo, name=repo, local_path="/unused") for repo in ("repo", "other")]
            )
            await session.flush()
            for repo, day, hour, model, operation, cost in (
                ("repo", 28, 23, "model-a", "doc_generation", 1.0),
                ("repo", 29, 0, "model-a", "doc_generation", 2.0),
                ("repo", 29, 12, "model-b", "embedding", 3.0),
                ("other", 29, 12, "model-a", "doc_generation", 100.0),
            ):
                session.add(
                    LlmCost(
                        repository_id=repo,
                        ts=datetime(2026, 10, day, hour, tzinfo=UTC),
                        model=model,
                        operation=operation,
                        input_tokens=10,
                        output_tokens=5,
                        cost_usd=cost,
                    )
                )
            await session.commit()
        yield factory
    finally:
        await engine.dispose()


async def _totals(surface, factory, by="day", since=None):
    if surface == "tracker":
        return await CostTracker(factory, "repo").totals(group_by=by, since=since)
    async with factory() as session:
        rows = await list_costs(
            "repo", by=by, since=since.isoformat() if since else None, session=session
        )
    return [row.model_dump() for row in rows]


@pytest.mark.parametrize("surface", ["tracker", "router"])
async def test_daily_totals_group_dates_and_exclude_other_repositories(surface, cost_sessions):
    rows = await _totals(surface, cost_sessions)
    assert [row["group"] for row in rows] == ["2026-10-29", "2026-10-28"]
    assert [row["calls"] for row in rows] == [2, 1]
    assert [row["cost_usd"] for row in rows] == [5.0, 1.0]
    assert [row["input_tokens"] for row in rows] == [20, 10]
    assert [row["output_tokens"] for row in rows] == [10, 5]


@pytest.mark.parametrize("surface", ["tracker", "router"])
async def test_daily_totals_keep_since_filter_and_empty_results(surface, cost_sessions):
    rows = await _totals(surface, cost_sessions, since=datetime(2026, 10, 29, 1, tzinfo=UTC))
    assert [(row["group"], row["calls"], row["cost_usd"]) for row in rows] == [
        ("2026-10-29", 1, 3.0)
    ]
    assert await _totals(surface, cost_sessions, since=datetime(2026, 11, 1, tzinfo=UTC)) == []


@pytest.mark.parametrize("surface", ["tracker", "router"])
@pytest.mark.parametrize("by,groups", [("model", {"model-a", "model-b"}),
                                      ("operation", {"doc_generation", "embedding"})])
async def test_other_cost_groupings_remain_unchanged(surface, by, groups, cost_sessions):
    rows = await _totals(surface, cost_sessions, by=by)
    assert {row["group"] for row in rows} == groups
    assert sum(row["calls"] for row in rows) == 3
    assert sum(row["cost_usd"] for row in rows) == 6.0


@pytest.mark.parametrize("surface", ["tracker", "router"])
async def test_postgresql_daily_query_groups_by_utc(surface, cost_sessions, monkeypatch):
    dialect = postgresql.dialect()
    async with cost_sessions() as session:
        result = MagicMock()
        result.fetchall.return_value = []
        execute = AsyncMock(return_value=result)
        monkeypatch.setattr(session, "get_bind", lambda: SimpleNamespace(dialect=dialect))
        monkeypatch.setattr(session, "execute", execute)

        @asynccontextmanager
        async def session_factory():
            yield session

        await _totals(surface, session_factory)
        statement = execute.call_args.args[0]

    sql = str(statement.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
    assert "GROUP BY date(timezone('UTC', llm_costs.ts))" in sql
