"""FastAPI application factory for the repowise server.

The ``create_app()`` function builds and configures the FastAPI instance.
The ``lifespan`` context manager handles startup (DB, FTS, vector store,
scheduler) and shutdown (cleanup).
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from fastapi import FastAPI, Request
from repowise.core.persistence.database import (
    create_engine,
    create_session_factory,
    init_db,
    resolve_db_url,
)
from repowise.core.persistence.search import FullTextSearch
from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.server import __version__
from repowise.server.routers import (
    chat,
    claude_md,
    dead_code,
    decisions,
    git,
    graph,
    health,
    jobs,
    pages,
    providers,
    repos,
    search,
    symbols,
    webhooks,
)
from repowise.server.scheduler import setup_scheduler

logger = logging.getLogger(__name__)


def _build_embedder():
    """Build an embedder from REPOWISE_EMBEDDER env var (default: mock).

    Supported values:
        mock    — deterministic 8-dim SHA-256 embedder (default, no API key needed)
        gemini  — GeminiEmbedder via GEMINI_API_KEY / GOOGLE_API_KEY env var
        openai  — OpenAIEmbedder via OPENAI_API_KEY env var
    """
    name = os.environ.get("REPOWISE_EMBEDDER", "mock").lower()
    if name == "gemini":
        from repowise.core.providers.embedding.gemini import GeminiEmbedder

        dims = int(os.environ.get("REPOWISE_EMBEDDING_DIMS", "768"))
        return GeminiEmbedder(output_dimensionality=dims)
    if name == "openai":
        from repowise.core.providers.embedding.openai import OpenAIEmbedder

        model = os.environ.get("REPOWISE_EMBEDDING_MODEL", "text-embedding-3-small")
        return OpenAIEmbedder(model=model)
    logger.warning("embedder.mock_active — set REPOWISE_EMBEDDER=gemini or openai for real RAG")
    return MockEmbedder()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: create DB engine, session factory, FTS, vector store, scheduler.
    Shutdown: dispose engine, stop scheduler, close vector store.
    """
    # Database
    db_url = resolve_db_url()
    engine = create_engine(db_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)

    # Full-text search
    fts = FullTextSearch(engine)
    await fts.ensure_index()

    # Vector store (InMemory default; LanceDB/pgvector configured via env)
    embedder = _build_embedder()
    vector_store = InMemoryVectorStore(embedder=embedder)

    # Background scheduler
    scheduler = setup_scheduler(session_factory)
    scheduler.start()

    # Store on app state
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.fts = fts
    app.state.vector_store = vector_store
    app.state.scheduler = scheduler

    # Initialize chat tool state (bridges FastAPI state to MCP tool globals)
    from repowise.server.chat_tools import init_tool_state

    init_tool_state(
        session_factory=session_factory,
        fts=fts,
        vector_store=vector_store,
    )

    logger.info("repowise_server_started", extra={"version": __version__})
    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    await vector_store.close()
    await engine.dispose()
    logger.info("repowise_server_stopped")


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    app = FastAPI(
        title="repowise API",
        description="REST API for repowise — codebase documentation engine",
        version=__version__,
        lifespan=lifespan,
    )

    # CORS — allow all origins for local development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Exception handlers
    @app.exception_handler(LookupError)
    async def not_found_handler(request: Request, exc: LookupError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def bad_request_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # Include routers
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
    app.include_router(claude_md.router)
    app.include_router(decisions.router)
    app.include_router(chat.router)
    app.include_router(providers.router)

    return app
