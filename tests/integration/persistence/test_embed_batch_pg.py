"""PgVectorStore.embed_batch against a real pgvector-enabled PostgreSQL.

Skipped automatically when ``testcontainers`` is not installed or Docker is
not running. Exercises the batched embed path (one embedder call + per-row
UPDATE of ``wiki_pages.embedding``) on the dialect production uses.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql import text as sa_text

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Page
from repowise.core.persistence.stores import SqlIndexStore
from repowise.core.persistence.vector_store import PgVectorStore
from repowise.core.providers.embedding.base import MockEmbedder

testcontainers = pytest.importorskip(
    "testcontainers.postgres",
    reason="testcontainers not installed",
)


@pytest.fixture(scope="module")
def pgvector_container():
    try:
        with testcontainers.PostgresContainer("pgvector/pgvector:pg16") as pg:
            yield pg
    except Exception as exc:  # pragma: no cover - skip when Docker / image missing
        pytest.skip(f"pgvector container unavailable: {exc}")


@pytest.fixture
async def pg_factory(pgvector_container):
    raw_url = pgvector_container.get_connection_url()
    url = raw_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    engine = create_async_engine(url, echo=False)
    try:
        await init_db(engine)
        # The pgvector ``embedding`` column is added by the Alembic migration in
        # production; create_all does not, so add it here to match.
        async with engine.begin() as conn:
            await conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(
                sa_text(
                    "ALTER TABLE wiki_pages ADD COLUMN IF NOT EXISTS "
                    f"embedding vector({MockEmbedder.dimensions})"
                )
            )
        yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_embed_batch_writes_embeddings_postgres(pg_factory):
    async with pg_factory() as session:
        repo = await SqlIndexStore(session).upsert_repository(
            name="pg-embed", local_path="/tmp/pg-embed"
        )
        now = datetime.now(UTC)
        for i in range(3):
            session.add(
                Page(
                    id=f"file_page:m{i}.py",
                    repository_id=repo.id,
                    page_type="file_page",
                    title=f"File m{i}",
                    content=f"content {i}",
                    summary="",
                    target_path=f"m{i}.py",
                    source_hash="h",
                    model_name="mock-model-1",
                    provider_name="mock",
                    created_at=now,
                    updated_at=now,
                )
            )
        await session.commit()

    store = PgVectorStore(pg_factory, MockEmbedder())
    assert await store.list_page_ids() == set()  # no embeddings yet

    items = [(f"file_page:m{i}.py", f"content {i}", {"page_type": "file_page"}) for i in range(3)]
    await store.embed_batch(items)

    assert await store.list_page_ids() == {f"file_page:m{i}.py" for i in range(3)}

    # Empty batch is a no-op.
    await store.embed_batch([])
    assert len(await store.list_page_ids()) == 3
