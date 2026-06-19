"""FastMCP server instance, lifespan, and entry points."""

from __future__ import annotations

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repowise.core.persistence.database import (
    create_engine,
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


# Per-embedder remediation hints, appended to the ERROR log and the `_meta`
# warning so a misconfiguration is actionable without grepping SDK tracebacks.
# Keyed by built-in embedder name; unknown/custom embedders fall back to the
# generic exception message alone.
_EMBEDDER_REMEDIATION: dict[str, str] = {
    "openai": "set OPENAI_API_KEY in the MCP server's environment (and `pip install openai`)",
    "gemini": (
        "set GEMINI_API_KEY (or GOOGLE_API_KEY) in the MCP server's environment "
        "(and `pip install google-genai`)"
    ),
    "ollama": "start Ollama, pull an embedding model, and set OLLAMA_BASE_URL if not local",
    "openrouter": "set OPENROUTER_API_KEY in the MCP server's environment (and `pip install openai`)",
}


def _configured_embedder_name() -> str:
    """Read the configured embedder name from env or ``.repowise/config.yaml``.

    Returns a lowercased name, or ``""`` when nothing is explicitly configured
    (in which case MockEmbedder is the intended default, not a degradation).
    """
    name = os.environ.get("REPOWISE_EMBEDDER", "").strip().lower()
    if name:
        return name
    if _state._repo_path:
        try:
            from pathlib import Path

            cfg_path = Path(_state._repo_path) / ".repowise" / "config.yaml"
            if cfg_path.exists():
                import yaml  # type: ignore[import-untyped]

                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                return (cfg.get("embedder") or "").strip().lower()
        except Exception:
            _log.debug("Failed to read embedder from config.yaml", exc_info=True)
    return ""


def _embedder_kwargs(name: str) -> dict[str, Any]:
    """Map repowise embedding env vars onto an embedder's constructor kwargs.

    Kept backend-agnostic: ``REPOWISE_EMBEDDING_MODEL`` applies to any embedder
    that accepts a ``model`` arg; ``REPOWISE_EMBEDDING_DIMS`` is gemini-specific
    (its constructor exposes ``output_dimensionality``). Anything not set here
    falls through to the embedder's own defaults.
    """
    kwargs: dict[str, Any] = {}
    model = os.environ.get("REPOWISE_EMBEDDING_MODEL")
    if model:
        kwargs["model"] = model
    if name == "gemini":
        dims = os.environ.get("REPOWISE_EMBEDDING_DIMS")
        kwargs["output_dimensionality"] = int(dims) if dims else 768
    return kwargs


def _resolve_embedder():
    """Resolve the embedder from ``REPOWISE_EMBEDDER`` / ``.repowise/config.yaml``.

    Goes through the shared embedder registry (``get_embedder``) so *every*
    backend is honoured — openai, gemini, openrouter, and any custom embedder
    registered via ``register_embedder`` — not just a hardcoded subset.

    When an embedder is **explicitly configured** but fails to initialise (most
    often a missing API key, but also a missing SDK or an unknown name), we
    still fall back to ``MockEmbedder`` so the server keeps serving non-RAG
    tools — but we record the degradation in ``_state._embedder_status`` and log
    at ``ERROR`` with the missing key and remediation. ``build_meta`` then
    surfaces ``embedder_degraded`` in every tool's ``_meta`` envelope so callers
    can detect that semantic search is running on mock vectors instead of the
    real index, rather than the broken server masquerading as healthy (#306).

    When nothing is configured (or ``mock`` is requested explicitly),
    MockEmbedder is the intended default and is **not** flagged as degraded.
    """
    from repowise.core.providers.embedding import get_embedder

    name = _configured_embedder_name()

    if not name or name == "mock":
        _state._embedder_status = {
            "active": "mock",
            "requested": name or None,
            "degraded": False,
        }
        return MockEmbedder()

    try:
        embedder = get_embedder(name, **_embedder_kwargs(name))
        _state._embedder_status = {"active": name, "requested": name, "degraded": False}
        return embedder
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        reason = (
            f"Configured embedder '{name}' failed to initialise ({detail}). "
            "Semantic search (search_codebase, get_answer) is running on mock "
            "vectors and CANNOT match the real index — results will be empty or "
            "irrelevant."
        )
        remediation = _EMBEDDER_REMEDIATION.get(name)
        if remediation:
            reason += f" To fix: {remediation}, then restart the MCP server."
        _log.error(reason, exc_info=True)
        _state._embedder_status = {
            "active": "mock",
            "requested": name,
            "degraded": True,
            "reason": reason,
        }
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

        try:
            # Step 1 — import lancedb in a thread to keep event loop free.
            await _asyncio.to_thread(__import__, "lancedb")

            from repowise.core.persistence.vector_store import LanceDBVectorStore

            if repo_path:
                from pathlib import Path

                lance_dir = Path(repo_path) / ".repowise" / "lancedb"
                if lance_dir.exists():
                    vs = LanceDBVectorStore(str(lance_dir), embedder=embedder)
                    # Step 2 — pre-connect so first search() is instant.
                    await vs._ensure_connected()
                    vector_store = vs
        except ImportError:
            pass
        except Exception:
            _log.warning("LanceDB pre-connect failed — using InMemory fallback")

        # decision_store is repointed to the shared page store — decisions are
        # now embedded under the "decision:" namespace within the same table.
        _state._vector_store = vector_store
        _state._decision_store = vector_store
    except Exception:
        _log.exception("Failed to load vector stores — falling back to MockEmbedder")
        _fallback = InMemoryVectorStore(embedder=MockEmbedder())
        _state._vector_store = _fallback
        _state._decision_store = _fallback
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
            from repowise.core.workspace.system_graph import SYSTEM_GRAPH_FILENAME
            from repowise.server.mcp_server._enrichment import CrossRepoEnricher

            cross_repo_path = ws_root / WORKSPACE_DATA_DIR / "cross_repo_edges.json"
            contracts_path = ws_root / WORKSPACE_DATA_DIR / CONTRACTS_FILENAME
            system_graph_path = ws_root / WORKSPACE_DATA_DIR / SYSTEM_GRAPH_FILENAME
            enricher = CrossRepoEnricher(
                cross_repo_path,
                contracts_path=contracts_path,
                system_graph_path=system_graph_path,
            )
            if enricher.has_data or enricher.has_system_graph:
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

    _log.info("repowise MCP: initialising database…")
    engine = create_engine(db_url)
    await init_db(engine)

    _state._session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    _state._fts = FullTextSearch(engine)
    await _state._fts.ensure_index()

    # Seed InMemory placeholder so tools that don't need vector search
    # can start immediately, before the background load completes.
    # decision_store is repointed to the same store — no separate table.
    _placeholder = InMemoryVectorStore(embedder=MockEmbedder())
    _state._vector_store = _placeholder
    _state._decision_store = _placeholder

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
    # _decision_store is an alias for _vector_store — close only once.
    await _state._vector_store.close()


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
    elif transport == "streamable-http":
        mcp.settings.port = port
        mcp.run(transport="streamable-http")
    else:
        # stdio servers are spawned per-session by the MCP client; when the
        # client dies abnormally the stdio loop doesn't exit (and Windows
        # never kills children), leaking servers that hold wiki.db handles.
        # The watchdog exits this process once the client is gone.
        from repowise.server.mcp_server._watchdog import start_parent_watchdog

        start_parent_watchdog()
        mcp.run(transport="stdio")
