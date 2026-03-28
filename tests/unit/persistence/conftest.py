"""Shared fixtures for persistence unit tests.

The async_engine fixture creates an in-memory SQLite database with StaticPool
so all connections within a test share the same in-memory database instance.
This is mandatory — without StaticPool each connection opens a fresh :memory: DB.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.database import init_db
from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs, make_repo_kwargs


@pytest.fixture
async def async_engine():
    """In-memory SQLite engine with all tables and FTS5 index created."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    yield engine
    await engine.dispose()


@pytest.fixture
async def async_session(async_engine):
    """Session bound to the in-memory engine.  Does NOT auto-commit."""
    factory = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session


@pytest.fixture
async def session_factory(async_engine):
    """Return the async_sessionmaker itself (for tests that need it)."""
    return async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
def mock_embedder():
    return MockEmbedder()


@pytest.fixture
def in_memory_vector_store(mock_embedder):
    return InMemoryVectorStore(embedder=mock_embedder)


# Re-export helpers so test files can import from either location
__all__ = ["insert_repo", "make_page_kwargs", "make_repo_kwargs"]
