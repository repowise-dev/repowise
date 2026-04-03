"""FastMCP server instance, lifespan, and entry points."""

from __future__ import annotations

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from repowise.core.persistence.database import (
    get_configured_db_url,
    get_repo_db_path,
    init_db,
    resolve_db_url,
)
from repowise.core.persistence.search import FullTextSearch
from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.server.mcp_server import _state


def _resolve_embedder():
    """Resolve embedder from REPOWISE_EMBEDDER env var or .repowise/config.yaml."""
    name = os.environ.get("REPOWISE_EMBEDDER", "").lower()
    if not name and _state._repo_path:
        try:
            from pathlib import Path

            cfg_path = Path(_state._repo_path) / ".repowise" / "config.yaml"
            if cfg_path.exists():
                import yaml

                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                name = (cfg.get("embedder") or "").lower()
        except Exception:
            pass
    if name == "gemini":
        try:
            from repowise.core.providers.embedding.gemini import GeminiEmbedder

            dims = int(os.environ.get("REPOWISE_EMBEDDING_DIMS", "768"))
            return GeminiEmbedder(output_dimensionality=dims)
        except Exception:
            pass
    if name == "openai":
        try:
            from repowise.core.providers.embedding.openai import OpenAIEmbedder

            model = os.environ.get("REPOWISE_EMBEDDING_MODEL", "text-embedding-3-small")
            return OpenAIEmbedder(model=model)
        except Exception:
            pass
    return MockEmbedder()


async def _load_vector_stores(repo_path: str | None) -> None:
    """Load embedder + vector stores in the background.

    Runs as an asyncio.Task started from _lifespan so the MCP server
    starts accepting connections immediately.  tool_search awaits
    _state._vector_store_ready before performing a search.

    We pre-warm the LanceDB connection here so the first search() call
    never hits a cold import or connection.  Specifically:

    1. `import lancedb` is deferred to asyncio.to_thread — the first-time
       import loads Rust/Arrow DLLs which can block the event loop for
       tens of seconds on Windows (AV scanning).  Running it in a thread
       keeps the event loop responsive.
    2. `_ensure_connected()` is called here so LanceDB opens the table
       before the first search.  Subsequent search() calls see
       self._db is not None and skip the blocking import entirely.
    """
    import asyncio as _asyncio
    import logging as _logging

    _log = _logging.getLogger("repowise.mcp")
    try:
        embedder = _resolve_embedder()
        vector_store: Any = InMemoryVectorStore(embedder=embedder)
        decision_store: Any = InMemoryVectorStore(embedder=embedder)

        try:
            # Step 1 — import lancedb in a thread to keep event loop free.
            await _asyncio.to_thread(__import__, "lancedb")

            from repowise.core.persistence.vector_store import LanceDBVectorStore

            if repo_path:
                from pathlib import Path

                lance_dir = Path(repo_path) / ".repowise" / "lancedb"
                if lance_dir.exists():
                    vs = LanceDBVectorStore(str(lance_dir), embedder=embedder)
                    ds = LanceDBVectorStore(
                        str(lance_dir), embedder=embedder, table_name="decision_records"
                    )
                    # Step 2 — pre-connect so first search() is instant.
                    await vs._ensure_connected()
                    await ds._ensure_connected()
                    vector_store = vs
                    decision_store = ds
        except ImportError:
            pass
        except Exception:
            _log.warning("LanceDB pre-connect failed — using InMemory fallback")

        _state._vector_store = vector_store
        _state._decision_store = decision_store
    except Exception:
        _log.exception("Failed to load vector stores — falling back to MockEmbedder")
        _state._vector_store = InMemoryVectorStore(embedder=MockEmbedder())
        _state._decision_store = InMemoryVectorStore(embedder=MockEmbedder())
    finally:
        if _state._vector_store_ready is not None:
            _state._vector_store_ready.set()


@asynccontextmanager
async def _lifespan(server: FastMCP):
    """Initialize DB engine, session factory, and FTS synchronously on startup.

    Vector store / LanceDB loading is deferred to a background asyncio task so
    the server starts accepting tool calls immediately.  search_codebase awaits
    _state._vector_store_ready before querying the vector store.
    """
    import logging as _logging

    _log = _logging.getLogger("repowise.mcp")

    configured_db_url = get_configured_db_url()
    db_url = resolve_db_url(_state._repo_path, create_parent=False)

    # If a repo path was configured, prefer <repo>/.repowise/wiki.db unless an env
    # override was provided explicitly.
    if _state._repo_path and configured_db_url is None:
        db_path = get_repo_db_path(_state._repo_path)
        repowise_dir = db_path.parent
        if not repowise_dir.exists():
            _log.warning(
                "No .repowise directory at %s — run 'repowise init' first",
                _state._repo_path,
            )
        elif not db_path.exists():
            _log.warning(
                "No wiki.db in %s — run 'repowise init' to generate the wiki",
                repowise_dir,
            )

    connect_args: dict = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    _log.info("repowise MCP: initialising database…")
    engine = create_async_engine(db_url, connect_args=connect_args)
    await init_db(engine)

    _state._session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    _state._fts = FullTextSearch(engine)
    await _state._fts.ensure_index()

    # Seed InMemory placeholders so tools that don't need vector search
    # can start immediately, before the background load completes.
    _state._vector_store = InMemoryVectorStore(embedder=MockEmbedder())
    _state._decision_store = InMemoryVectorStore(embedder=MockEmbedder())

    # Defer embedder resolution + LanceDB open to a background task so
    # the server starts accepting connections without blocking on disk I/O.
    _state._vector_store_ready = asyncio.Event()
    _bg_task = asyncio.create_task(_load_vector_stores(_state._repo_path))
    _log.info("repowise MCP: ready (vector stores loading in background)")

    yield

    _bg_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await _bg_task

    await engine.dispose()
    await _state._vector_store.close()
    if _state._decision_store is not None:
        await _state._decision_store.close()


# ---------------------------------------------------------------------------
# Create the MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "repowise",
    instructions=(
        "repowise is a codebase documentation engine. Use these tools to query "
        "the wiki for architecture overviews, contextual docs on files/modules/"
        "symbols, modification risk assessment, architectural decision rationale, "
        "semantic search, dependency paths, dead code, and architecture diagrams."
    ),
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Server entry points
# ---------------------------------------------------------------------------


def create_mcp_server(repo_path: str | None = None) -> FastMCP:
    """Create and return the MCP server instance, optionally scoped to a repo."""
    _state._repo_path = repo_path
    return mcp


def run_mcp(
    transport: str = "stdio",
    repo_path: str | None = None,
    port: int = 7338,
) -> None:
    """Run the MCP server with the specified transport."""
    _state._repo_path = repo_path

    if transport == "sse":
        mcp.settings.port = port
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")
