"""graph_metrics materialization round-trip against a real PostgreSQL container.

Skipped automatically when ``testcontainers`` is not installed or Docker is
not running. Proves the materialized snapshot round-trips through Postgres and
that a GraphBuilder hydrated from it reproduces the NetworkX metrics.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo, Import, ParsedFile
from repowise.core.persistence import batch_upsert_graph_metrics, get_graph_metrics
from repowise.core.persistence.database import init_db
from repowise.core.persistence.stores import SqlIndexStore

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


def _build_sample() -> GraphBuilder:
    def fi(path: str) -> FileInfo:
        return FileInfo(
            path=path,
            abs_path=f"/repo/{path}",
            language="python",
            size_bytes=100,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )

    def imp(mp: str) -> Import:
        return Import(
            raw_statement=f"import {mp}",
            module_path=mp,
            imported_names=[],
            is_relative=False,
            resolved_file=None,
        )

    def parsed(path: str, imports: list[Import]) -> ParsedFile:
        return ParsedFile(
            file_info=fi(path),
            symbols=[],
            imports=imports,
            exports=[],
            docstring=None,
            parse_errors=[],
            content_hash="",
        )

    b = GraphBuilder()
    b.add_file(parsed("a.py", [imp("b"), imp("c")]))
    b.add_file(parsed("b.py", [imp("c")]))
    b.add_file(parsed("c.py", []))
    b.build()
    return b


@pytest.mark.asyncio
async def test_graph_metrics_roundtrip_postgres(pg_session: AsyncSession):
    idx = SqlIndexStore(pg_session)
    repo = await idx.upsert_repository(name="pg-metrics", local_path="/tmp/pg-metrics")

    builder = _build_sample()
    snapshot = builder.file_metrics_snapshot()

    await batch_upsert_graph_metrics(pg_session, repo.id, snapshot)
    read_back = await get_graph_metrics(pg_session, repo.id)

    assert set(read_back) == set(snapshot)
    for node, m in snapshot.items():
        assert read_back[node]["pagerank"] == pytest.approx(m["pagerank"])
        assert read_back[node]["betweenness"] == pytest.approx(m["betweenness"])
        assert read_back[node]["community_id"] == m["community_id"]
        assert read_back[node]["in_degree"] == m["in_degree"]
        assert read_back[node]["out_degree"] == m["out_degree"]

    # A builder hydrated from the SQL snapshot reproduces the NetworkX metrics.
    hydrated = _build_sample()
    hydrated.load_metrics_from_sql(read_back)
    nx_pr = builder.pagerank()
    for node, score in hydrated.pagerank().items():
        assert score == pytest.approx(nx_pr[node])

    # Upsert again → idempotent (no duplicate rows).
    await batch_upsert_graph_metrics(pg_session, repo.id, snapshot)
    assert len(await get_graph_metrics(pg_session, repo.id)) == len(snapshot)
