"""PostgreSQL planner-statistics refresh against a real container.

Skipped automatically when ``testcontainers`` is not installed or Docker is
not running. A first load analyzes the tables it filled, a small write
analyzes nothing, and a table that grew past the band is analyzed again, so
the test-linking read keeps its index plan after a bulk load.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import and_, event, insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from repowise.core.persistence import create_session_factory, refresh_planner_stats
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import FunctionFact, GraphNode, Repository

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
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await init_db(engine)
        yield engine
    finally:
        await engine.dispose()


def _record_analyze(engine) -> list[str]:
    seen: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, params, context, executemany):
        if statement.startswith("ANALYZE"):
            seen.append(statement)

    return seen


async def _load(engine, start: int, count: int, files: int) -> None:
    """*count* function nodes over *files* files, each with a function_facts row."""
    nodes = [
        {
            "repository_id": "r1",
            "node_id": f"src/f{i % files}.py::fn{i}",
            "node_type": "function",
            "file_path": f"src/f{i % files}.py",
        }
        for i in range(start, start + count)
    ]
    facts = [{"repository_id": "r1", "symbol_id": n["node_id"]} for n in nodes]
    async with create_session_factory(engine)() as session:
        for i in range(0, count, 5000):
            await session.execute(insert(GraphNode), nodes[i : i + 5000])
            await session.execute(insert(FunctionFact), facts[i : i + 5000])
        await session.commit()


async def _live_rows_reported(engine, table: str, rows: int) -> None:
    """Wait for the server's row counters, which trail a commit by about a second."""
    for _ in range(50):
        async with engine.connect() as conn:
            live = (
                await conn.execute(
                    text("SELECT n_live_tup FROM pg_stat_user_tables WHERE relname = :t"),
                    {"t": table},
                )
            ).scalar()
        if live is not None and live >= rows:
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"{table}: live rows never reached {rows}")


async def _reltuples(engine, table: str) -> float:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT reltuples FROM pg_class WHERE relname = :t"), {"t": table}
            )
        ).scalar()


async def test_first_load_analyzes_the_tables_it_filled(pg_engine) -> None:
    async with create_session_factory(pg_engine)() as session:
        session.add(Repository(id="r1", name="r", local_path="/r"))
        await session.commit()
    await _load(pg_engine, 0, 3000, 300)
    seen = _record_analyze(pg_engine)

    await refresh_planner_stats(pg_engine)

    assert len(seen) == 1
    assert '"public"."graph_nodes"' in seen[0]
    assert '"public"."function_facts"' in seen[0]
    # An empty table is left alone, so the statement is not a whole-store pass.
    assert '"public"."wiki_pages"' not in seen[0]
    assert await _reltuples(pg_engine, "graph_nodes") == 3000


async def test_small_write_analyzes_nothing_and_growth_reanalyzes(pg_engine) -> None:
    async with create_session_factory(pg_engine)() as session:
        session.add(Repository(id="r1", name="r", local_path="/r"))
        await session.commit()
    await _load(pg_engine, 0, 200, 2)
    await _live_rows_reported(pg_engine, "graph_nodes", 200)
    await refresh_planner_stats(pg_engine)
    seen = _record_analyze(pg_engine)

    await _load(pg_engine, 200, 20, 2)
    await _live_rows_reported(pg_engine, "graph_nodes", 220)
    await refresh_planner_stats(pg_engine)
    assert seen == []

    # A bulk load: the statistics still describe 200 rows over 2 files, which
    # plans a read of two files as a scan of the whole table.
    # No wait for the row counters: the file size shows the load at once.
    await _load(pg_engine, 220, 60_000, 6_000)
    await refresh_planner_stats(pg_engine)
    assert len(seen) == 1 and '"public"."graph_nodes"' in seen[0]

    # The test-linking read over a few changed files seeks the file index.
    query = (
        select(FunctionFact.symbol_id, FunctionFact.tests_json)
        .join(
            GraphNode,
            and_(
                GraphNode.repository_id == FunctionFact.repository_id,
                GraphNode.node_id == FunctionFact.symbol_id,
            ),
        )
        .where(
            FunctionFact.repository_id == "r1",
            GraphNode.repository_id == "r1",
            GraphNode.file_path.in_(["src/f1.py", "src/f2.py"]),
        )
    )
    async with pg_engine.connect() as conn:
        compiled = query.compile(pg_engine.sync_engine, compile_kwargs={"literal_binds": True})
        plan = "\n".join(r[0] for r in await conn.execute(text(f"EXPLAIN {compiled}")))
    assert "ix_graph_nodes_repo_file" in plan, plan
