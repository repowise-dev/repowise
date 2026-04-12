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

_log = __import__("logging").getLogger("repowise.mcp")


def _resolve_embedder():
    """Resolve embedder from REPOWISE_EMBEDDER env var or .repowise/config.yaml."""
    name = os.environ.get("REPOWISE_EMBEDDER", "").lower()
    if not name and _state._repo_path:
        try:
            from pathlib import Path

            cfg_path = Path(_state._repo_path) / ".repowise" / "config.yaml"
            if cfg_path.exists():
                import yaml  # type: ignore[import-untyped]

                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                name = (cfg.get("embedder") or "").lower()
        except Exception:
            _log.debug("Failed to read embedder from config.yaml", exc_info=True)
    if name == "gemini":
        try:
            from repowise.core.providers.embedding.gemini import GeminiEmbedder

            dims = int(os.environ.get("REPOWISE_EMBEDDING_DIMS", "768"))
            return GeminiEmbedder(output_dimensionality=dims)
        except Exception:
            _log.warning("Failed to initialise Gemini embedder — falling back to mock", exc_info=True)
    if name == "openai":
        try:
            from repowise.core.providers.embedding.openai import OpenAIEmbedder

            model = os.environ.get("REPOWISE_EMBEDDING_MODEL", "text-embedding-3-small")
            return OpenAIEmbedder(model=model)
        except Exception:
            _log.warning("Failed to initialise OpenAI embedder — falling back to mock", exc_info=True)
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


def _detect_workspace(repo_path: str | None):
    """Check if ``repo_path`` is inside a workspace.

    Returns ``(workspace_root, ws_config, repo_alias)`` or ``(None, None, None)``.
    """
    if not repo_path:
        return None, None, None
    try:
        from pathlib import Path as _Path

        from repowise.core.workspace import WorkspaceConfig, find_workspace_root

        ws_root = find_workspace_root(_Path(repo_path))
        if ws_root is None:
            return None, None, None

        ws_config = WorkspaceConfig.load(ws_root)
        if not ws_config.repos:
            return None, None, None

        # Determine which repo the given path belongs to
        resolved = _Path(repo_path).resolve()
        repo_alias = None
        for entry in ws_config.repos:
            entry_abs = (ws_root / entry.path).resolve()
            try:
                resolved.relative_to(entry_abs)
                repo_alias = entry.alias
                break
            except ValueError:
                continue

        if repo_alias is None:
            # Path is inside workspace but doesn't match a repo — use default
            primary = ws_config.get_primary()
            repo_alias = primary.alias if primary else ws_config.repos[0].alias

        return ws_root, ws_config, repo_alias
    except Exception:
        _log.debug("Workspace detection failed", exc_info=True)
        return None, None, None


@asynccontextmanager
async def _lifespan(server: FastMCP):
    """Initialize DB engine, session factory, and FTS synchronously on startup.

    Vector store / LanceDB loading is deferred to a background asyncio task so
    the server starts accepting tool calls immediately.  search_codebase awaits
    _state._vector_store_ready before querying the vector store.
    """

    # --- Workspace detection ------------------------------------------------
    ws_root, ws_config, ws_repo_alias = _detect_workspace(_state._repo_path)

    if ws_root is not None and ws_config is not None:
        # Workspace mode — use RepoRegistry for multi-repo serving
        from pathlib import Path as _Path

        from repowise.core.workspace.registry import RepoRegistry

        # Override default repo to the one the path points at
        if ws_repo_alias and ws_config.get_repo(ws_repo_alias):
            ws_config.default_repo = ws_repo_alias

        registry = RepoRegistry(
            workspace_root=ws_root,
            ws_config=ws_config,
            embedder_factory=lambda: _resolve_embedder(),
        )

        # Eagerly load the default repo so tools work immediately
        default_ctx = await registry.get_default()

        _state._registry = registry
        _state._workspace_root = str(ws_root)

        # Alias default repo's resources into _state for backward compat
        _state._session_factory = default_ctx.session_factory
        _state._fts = default_ctx.fts
        _state._vector_store = default_ctx.vector_store
        _state._decision_store = default_ctx.decision_store
        _state._vector_store_ready = default_ctx.vector_store_ready

        # Load cross-repo enricher (Phase 3 + 4)
        try:
            from repowise.core.workspace.config import WORKSPACE_DATA_DIR
            from repowise.core.workspace.contracts import CONTRACTS_FILENAME
            from repowise.server.mcp_server._enrichment import CrossRepoEnricher

            cross_repo_path = ws_root / WORKSPACE_DATA_DIR / "cross_repo_edges.json"
            contracts_path = ws_root / WORKSPACE_DATA_DIR / CONTRACTS_FILENAME
            enricher = CrossRepoEnricher(
                cross_repo_path, contracts_path=contracts_path
            )
            if enricher.has_data:
                _state._cross_repo_enricher = enricher
                _log.info(
                    "Cross-repo enricher loaded: %d co-change edges, %d package deps, %d contract links",
                    len(enricher._co_changes),
                    len(enricher._package_deps),
                    len(enricher._contract_links),
                )
        except Exception:
            _log.debug("Cross-repo enricher not available", exc_info=True)

        _log.info(
            "repowise MCP: workspace mode — %d repos, default='%s'",
            len(ws_config.repos),
            registry.get_default_alias(),
        )

        yield

        _state._cross_repo_enricher = None
        await registry.close()
        _state._registry = None
        _state._workspace_root = None
        return

    # --- Single-repo mode (existing behavior) --------------------------------
    configured_db_url = get_configured_db_url()

    # When repo path is set and no env override, prefer repo-local DB.
    if _state._repo_path and configured_db_url is None:
        db_path = get_repo_db_path(_state._repo_path)
        repowise_dir = db_path.parent
        if not repowise_dir.exists():
            _log.warning(
                "No .repowise directory at %s — run 'repowise init' first",
                _state._repo_path,
            )
            repowise_dir.mkdir(parents=True, exist_ok=True)
        elif not db_path.exists():
            _log.warning(
                "No wiki.db in %s — run 'repowise init' to generate the wiki",
                repowise_dir,
            )

    db_url = resolve_db_url(_state._repo_path)

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
