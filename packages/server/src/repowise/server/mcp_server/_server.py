"""FastMCP server instance, lifespan, and entry points."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from repowise.core.persistence.database import get_session, init_db
from repowise.core.persistence.embedder import MockEmbedder
from repowise.core.persistence.search import FullTextSearch
from repowise.core.persistence.vector_store import InMemoryVectorStore
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
            from repowise.core.persistence.gemini_embedder import GeminiEmbedder

            dims = int(os.environ.get("REPOWISE_EMBEDDING_DIMS", "768"))
            return GeminiEmbedder(output_dimensionality=dims)
        except Exception:
            pass
    if name == "openai":
        try:
            from repowise.core.persistence.openai_embedder import OpenAIEmbedder

            model = os.environ.get("REPOWISE_EMBEDDING_MODEL", "text-embedding-3-small")
            return OpenAIEmbedder(model=model)
        except Exception:
            pass
    return MockEmbedder()


@asynccontextmanager
async def _lifespan(server: FastMCP):
    """Initialize DB engine, session factory, vector store, FTS on startup."""
    db_url = os.environ.get(
        "REPOWISE_DATABASE_URL", "sqlite+aiosqlite:///repowise.db"
    )

    # If a repo path was configured, try .repowise/wiki.db
    if _state._repo_path:
        from pathlib import Path
        import logging as _logging

        _log = _logging.getLogger("repowise.mcp")
        repowise_dir = Path(_state._repo_path) / ".repowise"
        if not repowise_dir.exists():
            _log.warning(
                "No .repowise directory at %s — run 'repowise init' first",
                _state._repo_path,
            )
        elif not (repowise_dir / "wiki.db").exists():
            _log.warning(
                "No wiki.db in %s — run 'repowise init' to generate the wiki",
                repowise_dir,
            )
        if repowise_dir.exists():
            db_path = repowise_dir / "wiki.db"
            if db_path.exists():
                db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

    connect_args: dict = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine = create_async_engine(db_url, connect_args=connect_args)
    await init_db(engine)

    _state._session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    _state._fts = FullTextSearch(engine)
    await _state._fts.ensure_index()

    # Resolve real embedder from env/config instead of always using MockEmbedder
    embedder = _resolve_embedder()
    _state._vector_store = InMemoryVectorStore(embedder=embedder)

    # Try to load LanceDB if available
    try:
        from repowise.core.persistence.vector_store import LanceDBVectorStore

        if _state._repo_path:
            from pathlib import Path

            lance_dir = Path(_state._repo_path) / ".repowise" / "lancedb"
            if lance_dir.exists():
                _state._vector_store = LanceDBVectorStore(
                    str(lance_dir), embedder=embedder
                )
                _state._decision_store = LanceDBVectorStore(
                    str(lance_dir), embedder=embedder, table_name="decision_records"
                )
    except ImportError:
        pass

    if _state._decision_store is None:
        _state._decision_store = InMemoryVectorStore(embedder=embedder)

    yield

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
