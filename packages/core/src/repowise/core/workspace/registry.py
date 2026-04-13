"""Workspace repo registry — manages per-repo database contexts.

Provides ``RepoContext`` (per-repo resources) and ``RepoRegistry``
(lazy-loading with LRU eviction) for workspace-aware MCP serving.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_log = logging.getLogger("repowise.workspace.registry")


# ---------------------------------------------------------------------------
# RepoContext — per-repo resource bundle
# ---------------------------------------------------------------------------


@dataclass
class RepoContext:
    """Holds all resources needed to serve MCP queries for a single repo."""

    alias: str
    path: Path
    session_factory: async_sessionmaker[AsyncSession]
    fts: Any  # FullTextSearch
    vector_store: Any  # LanceDB or InMemoryVectorStore
    decision_store: Any  # LanceDB or InMemoryVectorStore
    vector_store_ready: asyncio.Event = field(default_factory=asyncio.Event)
    _engine: Any = field(default=None, repr=False)  # AsyncEngine, for dispose


# ---------------------------------------------------------------------------
# RepoRegistry — lazy loading + LRU eviction
# ---------------------------------------------------------------------------


class RepoRegistry:
    """Manages multiple ``RepoContext`` instances for a workspace.

    Loads repo databases lazily on first access.  When the number of loaded
    repos exceeds ``MAX_LOADED``, the least-recently-used context is evicted
    (engine disposed, stores closed).
    """

    MAX_LOADED: int = 5

    def __init__(
        self,
        workspace_root: Path,
        ws_config: Any,  # WorkspaceConfig
        embedder_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._workspace_root = workspace_root
        self._ws_config = ws_config
        self._embedder_factory = embedder_factory
        self._contexts: dict[str, RepoContext] = {}
        self._access_order: dict[str, float] = {}
        self._vs_tasks: dict[str, asyncio.Task[None]] = {}

    # -- Public API --------------------------------------------------------

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def ws_config(self) -> Any:
        return self._ws_config

    def get_all_aliases(self) -> list[str]:
        """Return aliases of all repos in the workspace config."""
        return [r.alias for r in self._ws_config.repos]

    def get_default_alias(self) -> str:
        """Return the alias of the default/primary repo."""
        primary = self._ws_config.get_primary()
        if primary:
            return primary.alias
        if self._ws_config.default_repo:
            return self._ws_config.default_repo
        if not self._ws_config.repos:
            raise RuntimeError("Workspace has no repos configured")
        return self._ws_config.repos[0].alias

    def resolve_repo_param(self, repo: str | None) -> str | list[str]:
        """Resolve the ``repo`` tool parameter.

        Returns:
            A single alias (str) or list of aliases (for ``"all"``).

        Raises:
            ValueError: if the alias is unknown.
        """
        if repo is None:
            return self.get_default_alias()
        if repo == "all":
            return self.get_all_aliases()
        # Validate alias exists
        if self._ws_config.get_repo(repo) is None:
            available = self.get_all_aliases()
            raise ValueError(
                f"Unknown repo '{repo}'. Available: {available}"
            )
        return repo

    async def get(self, alias: str) -> RepoContext:
        """Get the ``RepoContext`` for *alias*, loading lazily if needed."""
        if alias in self._contexts:
            self._access_order[alias] = time.monotonic()
            return self._contexts[alias]

        # Evict if at capacity.  The default repo is protected from eviction, so
        # only count non-default contexts against the cap — this prevents the
        # cap from being silently bypassed when the default is always loaded.
        default_alias = self.get_default_alias()
        evictable = [a for a in self._contexts if a != default_alias]
        if len(evictable) >= self.MAX_LOADED:
            await self._evict_lru()

        ctx = await self._load_context(alias)
        self._contexts[alias] = ctx
        self._access_order[alias] = time.monotonic()
        return ctx

    async def get_default(self) -> RepoContext:
        """Shortcut for ``get(default_alias)``."""
        return await self.get(self.get_default_alias())

    async def close(self) -> None:
        """Dispose all loaded engines and close stores."""
        for alias in list(self._contexts):
            await self._dispose_context(alias)
        self._contexts.clear()
        self._access_order.clear()

    # -- Internal ----------------------------------------------------------

    async def _load_context(self, alias: str) -> RepoContext:
        """Create engine, session factory, FTS, and vector stores for a repo."""
        entry = self._ws_config.get_repo(alias)
        if entry is None:
            raise ValueError(f"No repo with alias '{alias}' in workspace config")

        repo_path = (self._workspace_root / entry.path).resolve()
        db_path = repo_path / ".repowise" / "wiki.db"

        if not db_path.exists():
            _log.warning("No wiki.db for repo '%s' at %s", alias, db_path)

        from repowise.core.persistence.database import (
            get_db_url,
            init_db,
        )
        from repowise.core.persistence.search import FullTextSearch
        from repowise.core.persistence.vector_store import InMemoryVectorStore
        from repowise.core.providers.embedding.base import MockEmbedder
        from sqlalchemy.ext.asyncio import (
            AsyncSession as _AsyncSession,
            async_sessionmaker as _async_sessionmaker,
            create_async_engine,
        )

        db_url = get_db_url(f"sqlite:///{db_path.as_posix()}")

        engine = create_async_engine(
            db_url,
            connect_args={"check_same_thread": False},
        )
        await init_db(engine)

        session_factory = _async_sessionmaker(
            engine, expire_on_commit=False, class_=_AsyncSession,
        )

        fts = FullTextSearch(engine)
        await fts.ensure_index()

        # Seed placeholder vector stores
        embedder = self._embedder_factory() if self._embedder_factory else MockEmbedder()
        vector_store: Any = InMemoryVectorStore(embedder=embedder)
        decision_store: Any = InMemoryVectorStore(embedder=embedder)

        vs_ready = asyncio.Event()

        ctx = RepoContext(
            alias=alias,
            path=repo_path,
            session_factory=session_factory,
            fts=fts,
            vector_store=vector_store,
            decision_store=decision_store,
            vector_store_ready=vs_ready,
            _engine=engine,
        )

        # Load real vector stores in background; track task for cancellation on eviction
        task = asyncio.create_task(
            self._load_vector_stores(ctx, repo_path, embedder),
            name=f"vs-load-{alias}",
        )
        self._vs_tasks[alias] = task

        return ctx

    async def _load_vector_stores(
        self,
        ctx: RepoContext,
        repo_path: Path,
        embedder: Any,
    ) -> None:
        """Background task: load LanceDB vector stores for a repo."""
        try:
            try:
                await asyncio.to_thread(__import__, "lancedb")
                from repowise.core.persistence.vector_store import LanceDBVectorStore

                lance_dir = repo_path / ".repowise" / "lancedb"
                if lance_dir.exists():
                    vs = LanceDBVectorStore(str(lance_dir), embedder=embedder)
                    ds = LanceDBVectorStore(
                        str(lance_dir), embedder=embedder, table_name="decision_records",
                    )
                    await vs._ensure_connected()
                    await ds._ensure_connected()
                    ctx.vector_store = vs
                    ctx.decision_store = ds
            except ImportError:
                pass
            except Exception:
                _log.warning(
                    "LanceDB load failed for '%s' — using InMemory fallback",
                    ctx.alias,
                )
        finally:
            # Only signal ready if this context is still the active one.
            # If it was evicted before we finished loading, a fresh context
            # will have its own event — setting the stale one would leave the
            # new context's event unset and cause callers to hang forever.
            if self._contexts.get(ctx.alias) is ctx:
                ctx.vector_store_ready.set()
            else:
                _log.debug(
                    "VS load for '%s' completed after eviction — skipping set()",
                    ctx.alias,
                )
            # Remove task reference regardless
            self._vs_tasks.pop(ctx.alias, None)

    async def _evict_lru(self) -> None:
        """Evict the least-recently-used context to free resources."""
        if not self._access_order:
            return
        # Find the alias with the oldest access time
        # Never evict the default repo
        default_alias = self.get_default_alias()
        candidates = {
            a: t for a, t in self._access_order.items()
            if a != default_alias
        }
        if not candidates:
            return  # Only default loaded, can't evict

        lru_alias = min(candidates, key=candidates.get)  # type: ignore[arg-type]
        _log.info("Evicting repo context '%s' (LRU)", lru_alias)
        await self._dispose_context(lru_alias)

    async def _dispose_context(self, alias: str) -> None:
        """Dispose engine and close stores for a loaded context."""
        ctx = self._contexts.pop(alias, None)
        if ctx is None:
            return
        self._access_order.pop(alias, None)

        # Cancel any in-flight background VS-load task so it cannot race with
        # a fresh context that will be created when this alias is re-accessed.
        task = self._vs_tasks.pop(alias, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        try:
            if ctx._engine is not None:
                await ctx._engine.dispose()
            if hasattr(ctx.vector_store, "close"):
                await ctx.vector_store.close()
            if hasattr(ctx.decision_store, "close"):
                await ctx.decision_store.close()
        except Exception:
            _log.warning("Error disposing context for '%s'", alias, exc_info=True)
