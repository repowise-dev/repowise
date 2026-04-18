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
    get_session,
    init_db,
    resolve_db_url,
)
from repowise.core.persistence.search import FullTextSearch
from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.server import __version__
from repowise.server.routers import (
    blast_radius,
    chat,
    claude_md,
    costs,
    dead_code,
    decisions,
    git,
    graph,
    health,
    jobs,
    knowledge_map,
    pages,
    providers,
    repos,
    search,
    security,
    symbols,
    webhooks,
    workspace,
)
from repowise.server.scheduler import setup_scheduler

logger = logging.getLogger(__name__)


def _build_embedder():
    """Build an embedder from REPOWISE_EMBEDDER env var (default: mock).

    Supported values:
        mock       — deterministic 8-dim SHA-256 embedder (default, no API key needed)
        gemini     — GeminiEmbedder via GEMINI_API_KEY / GOOGLE_API_KEY env var
        openai     — OpenAIEmbedder via OPENAI_API_KEY env var
        openrouter — OpenRouterEmbedder via OPENROUTER_API_KEY env var
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
    if name == "openrouter":
        from repowise.core.providers.embedding.openrouter import OpenRouterEmbedder

        model = os.environ.get("REPOWISE_EMBEDDING_MODEL", "google/gemini-embedding-001")
        return OpenRouterEmbedder(model=model)
    logger.warning("embedder.mock_active — set REPOWISE_EMBEDDER=gemini, openai, or openrouter for real RAG")
    return MockEmbedder()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: create DB engine, session factory, FTS, vector store, scheduler.
    Shutdown: dispose engine, stop scheduler, close vector store.
    """
    # Database
    # In workspace mode, prefer the primary repo's DB over the global default.
    # This prevents the global ~/.repowise/wiki.db (which may contain stale
    # repos from old test runs) from being used as the main DB.
    db_url = resolve_db_url()
    if not os.environ.get("REPOWISE_DB_URL") and not os.environ.get("REPOWISE_DATABASE_URL"):
        try:
            from pathlib import Path as _WsPath
            from repowise.core.workspace.config import find_workspace_root, WorkspaceConfig

            _ws_root = find_workspace_root()
            if _ws_root is not None:
                _ws_cfg = WorkspaceConfig.load(_ws_root)
                _primary = _ws_cfg.get_primary()
                _primary_path = _ws_root / (_primary.path if _primary else ".")
                _primary_db = (_primary_path / ".repowise" / "wiki.db").resolve()
                if _primary_db.exists():
                    db_url = f"sqlite+aiosqlite:///{_primary_db.as_posix()}"
                    logger.info("workspace_primary_db", extra={"db": str(_primary_db)})
        except Exception:
            pass  # Fall back to default

    engine = create_engine(db_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)

    # Reset any jobs left in "running" state from a previous server instance
    # (crash or restart) — they can never complete now.
    # Note: with multi-worker deployments this is a best-effort race; the
    # try/except prevents a SQLite lock error from crashing startup.
    try:
        from sqlalchemy import update as sa_update
        from repowise.core.persistence.models import GenerationJob
        from datetime import datetime, UTC as _UTC

        async with get_session(session_factory) as session:
            stale_result = await session.execute(
                sa_update(GenerationJob)
                .where(GenerationJob.status == "running")
                .values(
                    status="failed",
                    error_message="Server restarted — job interrupted",
                    finished_at=datetime.now(_UTC),
                )
            )
            if stale_result.rowcount:
                logger.warning("reset_stale_jobs", extra={"count": stale_result.rowcount})
    except Exception as exc:
        logger.warning("stale_job_reset_failed", extra={"error": str(exc)})

    # Full-text search
    fts = FullTextSearch(engine)
    await fts.ensure_index()

    # Vector store (InMemory default; LanceDB/pgvector configured via env)
    embedder = _build_embedder()
    vector_store = InMemoryVectorStore(embedder=embedder)

    # Store on app state (before scheduler, so scheduler can reference app_state)
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.fts = fts
    app.state.vector_store = vector_store
    app.state.background_tasks: set = set()  # Strong refs to prevent GC of asyncio tasks

    # Background scheduler (pass app.state so polling can launch jobs)
    scheduler = setup_scheduler(session_factory, app_state=app.state)
    scheduler.start()
    app.state.scheduler = scheduler

    # Initialize chat tool state (bridges FastAPI state to MCP tool globals)
    from repowise.server.chat_tools import init_tool_state

    init_tool_state(
        session_factory=session_factory,
        fts=fts,
        vector_store=vector_store,
    )

    # Workspace detection — mirrors MCP _server.py:_detect_workspace()
    app.state.workspace_config = None
    app.state.workspace_root = None
    app.state.cross_repo_enricher = None
    app.state.workspace_sessions = {}   # repo_id → session_factory
    app.state.workspace_engines = []    # engines to dispose on shutdown

    try:
        from pathlib import Path as _Path

        from repowise.core.workspace.config import (
            WORKSPACE_DATA_DIR,
            WorkspaceConfig,
            find_workspace_root,
        )

        ws_root = find_workspace_root()
        if ws_root is not None:
            ws_config = WorkspaceConfig.load(ws_root)
            app.state.workspace_config = ws_config
            app.state.workspace_root = str(ws_root)

            # Create per-repo DB engines so all workspace repos are accessible
            # via the same REST API (sidebar, repo-specific pages, etc.)
            import sqlite3 as _sqlite3

            for repo_entry in ws_config.repos:
                repo_path = (_Path(ws_root) / repo_entry.path).resolve()
                repo_db = repo_path / ".repowise" / "wiki.db"
                if not repo_db.exists():
                    continue
                # Read repo_id from this DB
                try:
                    conn = _sqlite3.connect(str(repo_db))
                    row = conn.execute("SELECT id FROM repositories LIMIT 1").fetchone()
                    conn.close()
                    if not row:
                        continue
                    repo_id = row[0]
                except Exception:
                    continue

                # Skip if this is the primary DB we already connected to
                # (the main engine already serves this repo)
                db_url_posix = repo_db.as_posix()
                if db_url and db_url_posix in db_url.replace("\\", "/"):
                    continue

                repo_engine = create_engine(f"sqlite+aiosqlite:///{db_url_posix}")
                await init_db(repo_engine)
                repo_sf = create_session_factory(repo_engine)
                app.state.workspace_sessions[repo_id] = repo_sf
                app.state.workspace_engines.append(repo_engine)

            if app.state.workspace_sessions:
                logger.info(
                    "workspace_repo_dbs_loaded",
                    extra={"count": len(app.state.workspace_sessions)},
                )

            from repowise.core.workspace.contracts import CONTRACTS_FILENAME
            from repowise.server.mcp_server._enrichment import CrossRepoEnricher

            cross_repo_path = _Path(ws_root) / WORKSPACE_DATA_DIR / "cross_repo_edges.json"
            contracts_path = _Path(ws_root) / WORKSPACE_DATA_DIR / CONTRACTS_FILENAME
            enricher = CrossRepoEnricher(cross_repo_path, contracts_path=contracts_path)
            if enricher.has_data or enricher.has_contract_data:
                app.state.cross_repo_enricher = enricher
                logger.info(
                    "repowise_workspace_detected",
                    extra={
                        "repos": len(ws_config.repos),
                        "co_changes": len(getattr(enricher, "_co_changes", [])),
                        "contract_links": len(getattr(enricher, "_contract_links", [])),
                    },
                )
            else:
                logger.info("repowise_workspace_detected", extra={"repos": len(ws_config.repos)})
    except Exception:
        logger.debug("Workspace detection skipped", exc_info=True)

    logger.info("repowise_server_started", extra={"version": __version__})
    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    await vector_store.close()
    # Dispose workspace repo engines first
    for ws_engine in getattr(app.state, "workspace_engines", []):
        try:
            await ws_engine.dispose()
        except Exception:
            pass
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
    app.include_router(costs.router)
    app.include_router(security.router)
    app.include_router(blast_radius.router)
    app.include_router(knowledge_map.router)
    app.include_router(workspace.router)

    return app
