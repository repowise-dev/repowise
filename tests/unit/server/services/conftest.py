"""Fixtures for server service tests.

Re-uses the in-memory SQLite engine pattern from the persistence tests but
defined locally so this file doesn't depend on tests/unit/persistence.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.database import init_db


@pytest.fixture
async def async_engine():
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
    factory = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
