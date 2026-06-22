"""FastAPI application factory for the repowise server.

The ``create_app()`` function builds and configures the FastAPI instance.
The ``lifespan`` context manager handles startup (DB, FTS, vector store,
scheduler) and shutdown (cleanup).
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import update as sa_update

from repowise.core.persistence.database import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
    resolve_db_url,
)
from repowise.core.persistence.models import GenerationJob
from repowise.core.persistence.search import FullTextSearch
from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.server import __version__
from repowise.server.routers import (
    blast_radius,
    c4,
    chat,
    claude_md,
    code_health,
    costs,
    coupling,
    dead_code,
    decisions,
    external_systems,
    files,
    git,
    graph,
    health,
    jobs,
    knowledge_map,
    mcp,
    meta,
    modules,
    overview,
    owners,
    pages,
    providers,
    repos,
    search,
    security,
    stats,
    symbols,
    webhooks,
    workspace,
)
from repowise.server.scheduler import setup_scheduler

logger = logging.getLogger(__name__)


async def reset_workspace_stale_jobs(app_state) -> int:
    """Mark interrupted pending/running jobs failed across workspace repo DBs."""
    reset_count = 0
    for ws_factory in getattr(app_state, "workspace_sessions", {}).values():
        async with get_session(ws_factory) as session:
            stale_result = await session.execute(
                sa_update(GenerationJob)
                .where(GenerationJob.status.in_(["running", "pending"]))
                .values(
                    status="failed",
                    error_message="Server restarted; job interrupted",
                    finished_at=datetime.now(UTC),
                )
            )
            reset_count += stale_result.rowcount or 0
    return reset_count


def _build_embedder():
    """Build an embedder from REPOWISE_EMBEDDER env var (default: mock).

    Supported values:
        mock       — deterministic 8-dim SHA-256 embedder (default, no API key needed)
        gemini     — GeminiEmbedder via GEMINI_API_KEY / GOOGLE_API_KEY env var
        openai     — OpenAIEmbedder via OPENAI_API_KEY env var
        openrouter — OpenRouterEmbedder via OPENROUTER_API_KEY env var
    """
    name = os.environ.get("REPOWISE_EMBEDDER", "mock").lower()
    if name == "ollama":
        from repowise.core.providers.embedding.ollama import OllamaEmbedder

        return OllamaEmbedder()
    if name == "gemini":
        from repowise.core.providers.embedding.gemini import GeminiEmbedder

        dims = int(os.environ.get("REPOWISE_EMBEDDING_DIMS", "768"))
        # Honour the indexed embedding model so serve doesn't silently rebuild
        # the embedder with a different default than init used (issue #426).
        model = os.environ.get("REPOWISE_EMBEDDING_MODEL")
        if model:
            return GeminiEmbedder(model=model, output_dimensionality=dims)
        return GeminiEmbedder(output_dimensionality=dims)
    if name == "openai":
        from repowise.core.providers.embedding.openai import OpenAIEmbedder

        model = os.environ.get("REPOWISE_EMBEDDING_MODEL", "text-embedding-3-small")
        return OpenAIEmbedder(model=model)
    if name == "openrouter":
        from repowise.core.providers.embedding.openrouter import OpenRouterEmbedder

        model = os.environ.get("REPOWISE_EMBEDDING_MODEL", "google/gemini-embedding-001")
        return OpenRouterEmbedder(model=model)
    logger.warning(
        "embedder.mock_active: set REPOWISE_EMBEDDER=gemini, openai, openrouter, or ollama for real RAG"
    )
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
            from repowise.core.workspace.config import WorkspaceConfig, find_workspace_root

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

    # Reset any jobs left in "running" or "pending" state from a previous
    # server instance (crash, restart, or cancellation between row-insert and
    # background-task launch) — they can never complete now and would block
    # new syncs via the active-job guard in the repos router.
    # Note: with multi-worker deployments this is a best-effort race; the
    # try/except prevents a SQLite lock error from crashing startup.
    try:
        from datetime import UTC as _UTC
        from datetime import datetime

        from sqlalchemy import update as sa_update

        from repowise.core.persistence.models import GenerationJob

        async with get_session(session_factory) as session:
            stale_result = await session.execute(
                sa_update(GenerationJob)
                .where(GenerationJob.status.in_(["running", "pending"]))
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
    app.state.workspace_sessions = {}  # repo_id → session_factory
    app.state.workspace_engines = []  # engines to dispose on shutdown
    # Per-repo FTS instances keyed by repo_id, used by the search router
    # to fan out across every workspace repo (single-repo FTS lives on
    # app.state.fts and stays as the primary).
    app.state.workspace_fts = {}  # repo_id → FullTextSearch
    # repo_id → vector store (LanceDB-backed) for per-repo semantic search.
    # Populated lazily by the search router on first use, then cached.
    app.state.workspace_vector_stores = {}  # repo_id → VectorStore

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
                # (the main engine already serves this repo) — but still
                # register the primary's FTS under its repo_id so the
                # search fan-out can include it.
                db_url_posix = repo_db.as_posix()
                if db_url and db_url_posix in db_url.replace("\\", "/"):
                    app.state.workspace_fts[repo_id] = fts
                    continue

                repo_engine = create_engine(f"sqlite+aiosqlite:///{db_url_posix}")
                await init_db(repo_engine)
                repo_sf = create_session_factory(repo_engine)
                app.state.workspace_sessions[repo_id] = repo_sf
                app.state.workspace_engines.append(repo_engine)

                # Build a per-repo FTS instance so the search router can
                # fan out queries across every workspace repo. Without
                # this, full-text search only ever sees the primary DB.
                try:
                    repo_fts = FullTextSearch(repo_engine)
                    await repo_fts.ensure_index()
                    app.state.workspace_fts[repo_id] = repo_fts
                except Exception:
                    logger.debug(
                        "workspace_fts_init_failed",
                        extra={"repo_id": repo_id},
                        exc_info=True,
                    )

            if app.state.workspace_sessions:
                logger.info(
                    "workspace_repo_dbs_loaded",
                    extra={"count": len(app.state.workspace_sessions)},
                )

            # Reset stale jobs in non-primary workspace DBs too. The primary
            # DB was handled before workspace detection, but each secondary
            # repo has its own generation_jobs table and stale running rows
            # there would keep the UI showing an in-progress sync forever.
            try:
                reset_count = await reset_workspace_stale_jobs(app.state)
                if reset_count:
                    logger.warning(
                        "reset_workspace_stale_jobs",
                        extra={"count": reset_count},
                    )
            except Exception as exc:
                logger.warning("workspace_stale_job_reset_failed", extra={"error": str(exc)})

            from repowise.core.workspace.breaking_change import BREAKING_CHANGES_FILENAME
            from repowise.core.workspace.conformance import CONFORMANCE_FILENAME
            from repowise.core.workspace.contracts import CONTRACTS_FILENAME
            from repowise.core.workspace.system_graph import SYSTEM_GRAPH_FILENAME
            from repowise.server.mcp_server._enrichment import CrossRepoEnricher

            cross_repo_path = _Path(ws_root) / WORKSPACE_DATA_DIR / "cross_repo_edges.json"
            contracts_path = _Path(ws_root) / WORKSPACE_DATA_DIR / CONTRACTS_FILENAME
            system_graph_path = _Path(ws_root) / WORKSPACE_DATA_DIR / SYSTEM_GRAPH_FILENAME
            breaking_changes_path = _Path(ws_root) / WORKSPACE_DATA_DIR / BREAKING_CHANGES_FILENAME
            conformance_path = _Path(ws_root) / WORKSPACE_DATA_DIR / CONFORMANCE_FILENAME
            enricher = CrossRepoEnricher(
                cross_repo_path,
                contracts_path=contracts_path,
                system_graph_path=system_graph_path,
                breaking_changes_path=breaking_changes_path,
                conformance_path=conformance_path,
            )
            if enricher.has_data or enricher.has_contract_data or enricher.has_system_graph:
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
    # Close cached per-repo vector stores (LanceDB connections).
    try:
        from repowise.server.search_helpers import close_workspace_vector_stores

        await close_workspace_vector_stores(app)
    except Exception:
        logger.debug("workspace_vector_store_close_failed", exc_info=True)
    # Dispose workspace repo engines first
    for ws_engine in getattr(app.state, "workspace_engines", []):
        with suppress(Exception):
            await ws_engine.dispose()
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
    app.include_router(c4.router)
    app.include_router(webhooks.router)
    app.include_router(git.router)
    app.include_router(dead_code.router)
    app.include_router(code_health.router)
    app.include_router(coupling.router)
    app.include_router(claude_md.router)
    app.include_router(decisions.router)
    app.include_router(chat.router)
    app.include_router(providers.router)
    app.include_router(mcp.router)
    app.include_router(meta.router)
    app.include_router(costs.router)
    app.include_router(security.router)
    app.include_router(blast_radius.router)
    app.include_router(knowledge_map.router)
    app.include_router(workspace.router)
    app.include_router(owners.router)
    app.include_router(modules.router)
    app.include_router(overview.router)
    app.include_router(stats.router)
    app.include_router(files.router)
    app.include_router(external_systems.router)

    return app
