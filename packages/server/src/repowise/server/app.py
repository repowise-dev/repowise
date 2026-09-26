"""FastAPI application factory for the repowise server.

The ``create_app()`` function builds and configures the FastAPI instance.
The ``lifespan`` context manager handles startup (DB, FTS, vector store,
scheduler) and shutdown (cleanup).
"""

from __future__ import annotations

# Same reason as the CLI entry point, and it matters more here: a server is
# long-lived, so a BLAS workspace sized to the host's core count is held for
# the process's whole life rather than one index (issue #1394).
from repowise.core.blas_threads import limit_blas_threads

limit_blas_threads()

# ruff: noqa: E402 — the BLAS pin above is only effective before these run.
import logging
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from repowise.core.persistence.database import (
    create_engine,
    create_session_factory,
    init_db,
    resolve_db_url,
)
from repowise.core.persistence.search import FullTextSearch
from repowise.core.providers.embedding import is_semantic_embedder
from repowise.core.providers.embedding.base import KeylessEmbedder
from repowise.core.providers.embedding.caching import CachingEmbedder
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
    doc_drift,
    episodes,
    external_systems,
    feedback,
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
    refactoring,
    repos,
    search,
    security,
    stats,
    symbols,
    webhooks,
    workspace,
)
from repowise.server.scheduler import setup_scheduler
from repowise.server.workspace_startup import (
    attach_workspace,
    fail_interrupted_jobs,
    init_workspace_state,
    reset_workspace_stale_jobs,
    workspace_primary_db_url,
)

logger = logging.getLogger(__name__)


def _gemini_embedder():
    from repowise.core.providers.embedding.gemini import GeminiEmbedder

    dims_raw = os.environ.get("REPOWISE_EMBEDDING_DIMS")
    dims = 768
    if dims_raw:
        try:
            parsed = int(dims_raw)
        except (ValueError, OverflowError):
            parsed = 0
        if parsed > 0:
            dims = parsed
        else:
            print(
                f"REPOWISE_EMBEDDING_DIMS={dims_raw!r} is not a positive integer; using {dims}.",
                file=sys.stderr,
            )
    # Honour the indexed embedding model so serve doesn't silently rebuild
    # the embedder with a different default than init used (issue #426).
    model = os.environ.get("REPOWISE_EMBEDDING_MODEL")
    if model:
        return GeminiEmbedder(model=model, output_dimensionality=dims)
    return GeminiEmbedder(output_dimensionality=dims)


def _build_embedder():
    """Build an embedder from REPOWISE_EMBEDDER env var (default: mock).

    Supported values:
        mock       — deterministic 8-dim SHA-256 embedder (default, no API key needed)
        gemini     — GeminiEmbedder via GEMINI_API_KEY / GOOGLE_API_KEY env var
        openai     — OpenAIEmbedder via OPENAI_API_KEY env var
        openrouter — OpenRouterEmbedder via OPENROUTER_API_KEY env var
        edenai     — EdenAIEmbedder via EDENAI_API_KEY env var
    """
    name = os.environ.get("REPOWISE_EMBEDDER", "mock").lower()
    if name == "ollama":
        from repowise.core.providers.embedding.ollama import OllamaEmbedder

        return OllamaEmbedder()
    if name == "gemini":
        return _gemini_embedder()
    if name == "openai":
        from repowise.core.providers.embedding.openai import OpenAIEmbedder

        model = os.environ.get("REPOWISE_EMBEDDING_MODEL", "text-embedding-3-small")
        return OpenAIEmbedder(model=model)
    if name == "openrouter":
        from repowise.core.providers.embedding.openrouter import OpenRouterEmbedder

        model = os.environ.get("REPOWISE_EMBEDDING_MODEL", "google/gemini-embedding-001")
        return OpenRouterEmbedder(model=model)
    if name == "edenai":
        from repowise.core.providers.embedding.edenai import EdenAIEmbedder

        model = os.environ.get("REPOWISE_EMBEDDING_MODEL", "amazon/amazon.titan-embed-text-v2:0")
        return EdenAIEmbedder(model=model)
    logger.warning(
        "embedder.mock_active: set REPOWISE_EMBEDDER=gemini, openai, openrouter, "
        "ollama, or edenai for real RAG"
    )
    return KeylessEmbedder()


def _build_query_embedder():
    """Build the HTTP server's long-lived, query-side embedder.

    Keyless is left bare because semantic-search routing identifies it by type;
    wrapping it would incorrectly enable a vector leg with no signal.
    """
    embedder = _build_embedder()
    return CachingEmbedder(embedder) if is_semantic_embedder(embedder) else embedder


def _resolve_server_db_url() -> str:
    # In workspace mode, prefer the primary repo's DB over the global default.
    # This prevents the global ~/.repowise/wiki.db (which may contain stale
    # repos from old test runs) from being used as the main DB.
    db_url = resolve_db_url()
    if not os.environ.get("REPOWISE_DB_URL") and not os.environ.get("REPOWISE_DATABASE_URL"):
        db_url = workspace_primary_db_url() or db_url
    return db_url


async def _reset_stale_jobs(session_factory) -> None:
    # Reset any jobs left in "running" or "pending" state from a previous
    # server instance (crash, restart, or cancellation between row-insert and
    # background-task launch) — they can never complete now and would block
    # new syncs via the active-job guard in the repos router.
    # Note: with multi-worker deployments this is a best-effort race; the
    # try/except prevents a SQLite lock error from crashing startup.
    try:
        count = await fail_interrupted_jobs(session_factory, "Server restarted — job interrupted")
        if count:
            logger.warning("reset_stale_jobs", extra={"count": count})
    except Exception as exc:
        logger.warning("stale_job_reset_failed", extra={"error": str(exc)})


async def _open_fts(engine) -> FullTextSearch:
    # Full-text search. A failure here used to abort startup, so a store whose
    # index could not be upgraded served no documentation at all (issue #1309):
    # the wiki, the graph and the health pages were all unreachable over a
    # search index. Keyword search degrades to whatever shape the index is
    # already in, or to the vector arm alone; everything else keeps working.
    fts = FullTextSearch(engine)
    try:
        await fts.ensure_index()
    except Exception as exc:
        logger.warning("fts_ensure_index_failed", extra={"error": str(exc)})
    return fts


def _publish_core_state(
    app_state,
    *,
    engine,
    session_factory,
    db_url: str,
    fts: FullTextSearch,
    vector_store,
    primary_vector_repo_id: str | None,
) -> None:
    # Store on app state (before scheduler, so scheduler can reference app_state)
    app_state.engine = engine
    app_state.session_factory = session_factory
    app_state.db_url = db_url
    app_state.fts = fts
    app_state.vector_store = vector_store
    app_state.primary_vector_repo_id = primary_vector_repo_id
    app_state.background_tasks = set()  # Strong refs to prevent GC of asyncio tasks
    app_state.job_tasks = {}  # job_id → asyncio.Task (cancel endpoint)
    app_state.job_cancel_tokens = {}  # job_id → CancellationToken
    app_state.job_events = {}  # job_id → JobEventBuffer (SSE message frames)


async def _rediscover_repo_dbs(app_state) -> None:
    # Re-register per-repo databases for repos added via the API (their data
    # lives in <repo>/.repowise/wiki.db; the primary DB only holds a registry
    # row). Runs after workspace detection so already-registered workspace
    # repos are skipped.
    try:
        from repowise.server.repo_db import rediscover_repo_dbs

        rediscovered = await rediscover_repo_dbs(app_state)
        if rediscovered:
            logger.info("repo_dbs_rediscovered", extra={"count": rediscovered})
            # Jobs interrupted by the restart live in those per-repo DBs; the
            # earlier resets only covered the primary and workspace DBs.
            stale = await reset_workspace_stale_jobs(app_state)
            if stale:
                logger.warning("reset_stale_jobs", extra={"count": stale})
    except Exception:
        logger.debug("repo_db_rediscovery_skipped", exc_info=True)


async def _release_workspace_tools(app_state) -> None:
    # Release the per-repo contexts the chat tools resolved through
    _repo_registry = getattr(app_state, "repo_registry", None)
    if _repo_registry is None:
        return
    from repowise.server.chat_tools import set_tool_workspace

    with suppress(Exception):
        await _repo_registry.close()
    # The enricher is only ever published alongside the registry.
    set_tool_workspace(registry=None, workspace_root=None, cross_repo_enricher=None)
    # The test-impact join holds its own session per consumer repo.
    from repowise.server.mcp_server._test_impact import close_test_impact_indexes

    with suppress(Exception):
        await close_test_impact_indexes()


async def _shutdown(app: FastAPI, *, scheduler, vector_store, engine) -> None:
    scheduler.shutdown(wait=False)
    with suppress(Exception):
        await vector_store.close()
    # Close cached per-repo vector stores (LanceDB connections).
    try:
        from repowise.server.search_helpers import close_workspace_vector_stores

        await close_workspace_vector_stores(app)
    except Exception:
        logger.debug("workspace_vector_store_close_failed", exc_info=True)
    await _release_workspace_tools(app.state)
    # Dispose workspace repo engines first
    for ws_engine in getattr(app.state, "workspace_engines", []):
        with suppress(Exception):
            await ws_engine.dispose()
    await engine.dispose()
    logger.info("repowise_server_stopped")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: create DB engine, session factory, FTS, vector store, scheduler.
    Shutdown: dispose engine, stop scheduler, close vector store.
    """
    db_url = _resolve_server_db_url()
    engine = create_engine(db_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    await _reset_stale_jobs(session_factory)
    fts = await _open_fts(engine)

    # Reuse the repo-local LanceDB index written by CLI init/update. A fresh
    # in-memory store is used only when this database cannot be associated with
    # a repository or the optional LanceDB runtime is unavailable.
    embedder = _build_query_embedder()
    from repowise.server.search_helpers import build_primary_vector_store

    vector_store, primary_vector_repo_id = await build_primary_vector_store(
        session_factory,
        db_url,
        embedder,
    )
    _publish_core_state(
        app.state,
        engine=engine,
        session_factory=session_factory,
        db_url=db_url,
        fts=fts,
        vector_store=vector_store,
        primary_vector_repo_id=primary_vector_repo_id,
    )

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

    init_workspace_state(app.state, vector_store, primary_vector_repo_id)
    await attach_workspace(
        app.state,
        session_factory=session_factory,
        fts=fts,
        db_url=db_url,
        embedder_factory=_build_query_embedder,
    )
    await _rediscover_repo_dbs(app.state)

    logger.info("repowise_server_started", extra={"version": __version__})
    try:
        yield
    finally:
        # Shutdown. The finally matters for the workspace globals below: they
        # outlive the app object, so skipping this on a shutdown exception
        # would leave the tool layer pointing at disposed engines.
        await _shutdown(app, scheduler=scheduler, vector_store=vector_store, engine=engine)


def _cors_settings() -> tuple[list[str], bool]:
    """``(allowed origins, allow credentials)`` from the environment."""
    # CORS — configurable; default allows local dev but is browser-spec compliant.
    # Browsers reject `Access-Control-Allow-Origin: *` with `Allow-Credentials: true`
    # (preflight fails). When REPOWISE_CORS_ORIGINS is unset we allow any origin
    # without credentials (safe for local dev); when credentials are needed the
    # operator must set explicit origins via REPOWISE_CORS_ORIGINS.
    cors_origins_env = os.environ.get("REPOWISE_CORS_ORIGINS", "").strip()
    if cors_origins_env:
        return [o.strip() for o in cors_origins_env.split(",") if o.strip()], True
    # Wildcard with credentials is rejected by browsers — force False and warn
    # if the old unsafe combination is detected via explicit env.
    if os.environ.get("REPOWISE_CORS_ALLOW_CREDENTIALS", "").lower() in ("1", "true", "yes"):
        logger.warning(
            "cors.wildcard_with_credentials_rejected: "
            "REPOWISE_CORS_ORIGINS=* cannot be used with credentials; "
            "set REPOWISE_CORS_ORIGINS to explicit origins"
        )
    return ["*"], False


# Registration order is route precedence: a literal path must be registered
# before a parameterised one that would otherwise match it.
_ROUTERS = (
    health,
    repos,
    pages,
    search,
    jobs,
    symbols,
    graph,
    c4,
    webhooks,
    git,
    dead_code,
    doc_drift,
    code_health,
    coupling,
    claude_md,
    decisions,
    episodes,
    chat,
    providers,
    mcp,
    meta,
    costs,
    security,
    blast_radius,
    refactoring,
    knowledge_map,
    workspace,
    owners,
    modules,
    overview,
    stats,
    files,
    external_systems,
    feedback,
)


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    app = FastAPI(
        title="repowise API",
        description="REST API for repowise — codebase documentation engine",
        version=__version__,
        lifespan=lifespan,
    )

    cors_origins, cors_allow_credentials = _cors_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=cors_allow_credentials,
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
    for router_module in _ROUTERS:
        app.include_router(router_module.router)

    return app
