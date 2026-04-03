"""Shared fixtures for server unit tests.

Creates a test FastAPI app with an in-memory SQLite database and
provides an httpx AsyncClient for making requests.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.database import init_db
from repowise.core.persistence.search import FullTextSearch
from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder


def _create_test_app():
    """Create a FastAPI app without the lifespan (we manage state manually)."""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    from repowise.server.routers import (
        dead_code,
        git,
        graph,
        health,
        jobs,
        pages,
        repos,
        search,
        symbols,
        webhooks,
    )

    @asynccontextmanager
    async def noop_lifespan(app: FastAPI):
        yield

    app = FastAPI(title="repowise API Test", lifespan=noop_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(LookupError)
    async def not_found_handler(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def bad_request_handler(request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    app.include_router(health.router)
    app.include_router(repos.router)
    app.include_router(pages.router)
    app.include_router(search.router)
    app.include_router(jobs.router)
    app.include_router(symbols.router)
    app.include_router(graph.router)
    app.include_router(webhooks.router)
    app.include_router(git.router)
    app.include_router(dead_code.router)

    return app


@pytest.fixture
async def test_engine():
    """In-memory SQLite engine with all tables created."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(test_engine):
    """Async session factory for the test engine."""
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def app(test_engine, session_factory):
    """Test FastAPI app with all state configured."""
    test_app = _create_test_app()

    fts = FullTextSearch(test_engine)
    await fts.ensure_index()

    embedder = MockEmbedder()
    vector_store = InMemoryVectorStore(embedder=embedder)

    test_app.state.engine = test_engine
    test_app.state.session_factory = session_factory
    test_app.state.fts = fts
    test_app.state.vector_store = vector_store

    yield test_app
    await vector_store.close()


@pytest.fixture
async def client(app) -> AsyncClient:
    """httpx AsyncClient for making requests to the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def session(session_factory) -> AsyncSession:
    """A raw DB session for test setup (inserting test data directly)."""
    async with session_factory() as s:
        yield s
        await s.commit()


async def create_test_repo(client: AsyncClient) -> dict:
    """Helper: create a test repository via the API and return its response."""
    resp = await client.post(
        "/api/repos",
        json={
            "name": "test-repo",
            "local_path": "/tmp/test-repo",
            "url": "https://github.com/example/test-repo",
        },
    )
    assert resp.status_code == 201
    return resp.json()
