"""Repository-aware vector-store helpers for REST search and background jobs.

These live in a separate module so they can be reused by other routers
(chat, MCP-over-HTTP, future endpoints) without each one re-implementing
LanceDB rehydration, routing, and lazy caching.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path
from typing import Any

logger = logging.getLogger("repowise.server.search_helpers")

# Asyncio lock per repo_id, used to prevent two concurrent semantic
# search requests from both opening the LanceDB store. The first one in
# allocates; everyone else awaits the cached result.
_vector_store_locks: dict[str, asyncio.Lock] = {}


def _build_repo_vector_store(repo_path: Path, embedder: Any, *, create: bool) -> Any | None:
    """Open a repo-local LanceDB store, with an in-memory write fallback.

    Search-only callers do not create a new store when no persisted index
    exists. Job callers pass ``create=True`` so server-generated embeddings use
    the same durable ``.repowise/lancedb`` location as the CLI.
    """
    lance_dir = repo_path / ".repowise" / "lancedb"
    if not create and not lance_dir.is_dir():
        return None

    try:
        import lancedb  # noqa: F401  # verify the optional runtime is installed

        from repowise.core.persistence.vector_store import LanceDBVectorStore

        if create:
            lance_dir.mkdir(parents=True, exist_ok=True)
        return LanceDBVectorStore(str(lance_dir), embedder=embedder)
    except (ImportError, OSError):
        if not create:
            return None
        from repowise.core.persistence.vector_store import InMemoryVectorStore

        logger.warning(
            "lancedb_unavailable_using_memory",
            extra={"repo_path": str(repo_path)},
        )
        return InMemoryVectorStore(embedder)


async def build_primary_vector_store(
    session_factory, db_url: str, embedder: Any
) -> tuple[Any, str | None]:
    """Build the primary repo's durable vector store when it can be identified.

    A repo-local SQLite database normally contains one repository row. For a
    shared registry database, only a row whose ``wiki.db`` matches ``db_url`` is
    treated as primary. The returned repo id lets startup seed the per-repo
    cache so jobs and searches reuse the same connection.
    """
    from sqlalchemy import select

    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import Repository
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    rows: list[tuple[str, str]] = []
    try:
        async with get_session(session_factory) as session:
            result = await session.execute(select(Repository.id, Repository.local_path))
            rows = [(row[0], row[1]) for row in result.all() if row[1]]
    except Exception:
        logger.debug("primary_vector_repo_lookup_failed", exc_info=True)

    selected: tuple[str, str] | None = None
    normalized_url = db_url.replace("\\", "/")
    for repo_id, local_path in rows:
        local_db = (Path(local_path).resolve() / ".repowise" / "wiki.db").as_posix()
        if local_db in normalized_url:
            selected = (repo_id, local_path)
            break
    if selected is None and len(rows) == 1:
        selected = rows[0]

    if selected is not None:
        repo_id, local_path = selected
        store = _build_repo_vector_store(Path(local_path).resolve(), embedder, create=True)
        if store is not None:
            logger.info(
                "primary_vector_store_loaded",
                extra={"repo_id": repo_id, "repo_path": local_path},
            )
            return store, repo_id

    return InMemoryVectorStore(embedder), None


async def resolve_repo_vector_store(
    app_state,
    repo_id: str,
    *,
    repo_path: str | Path | None = None,
    create: bool = False,
) -> Any | None:
    """Return the vector store belonging to ``repo_id``.

    Stores are cached by repository id. When ``repo_path`` is omitted, the
    repository's routed session factory is used to find its canonical local
    path, which works for workspace and API-managed repositories alike.
    """
    cache = getattr(app_state, "workspace_vector_stores", None)
    if cache is None:
        cache = {}
        app_state.workspace_vector_stores = cache
    if repo_id in cache:
        return cache[repo_id]

    lock = _vector_store_locks.setdefault(repo_id, asyncio.Lock())
    async with lock:
        if repo_id in cache:
            return cache[repo_id]

        if repo_path is None:
            from repowise.core.persistence.crud import get_repository
            from repowise.core.persistence.database import get_session
            from repowise.server.deps import resolve_session_factory

            try:
                factory = resolve_session_factory(app_state, repo_id)
                async with get_session(factory) as session:
                    repo = await get_repository(session, repo_id)
                    repo_path = repo.local_path if repo is not None else None
            except Exception:
                logger.debug(
                    "vector_repo_path_lookup_failed",
                    extra={"repo_id": repo_id},
                    exc_info=True,
                )
                return None

        if not repo_path:
            return None

        store = _build_repo_vector_store(
            Path(repo_path).resolve(),
            _resolve_embedder_from_state(app_state),
            create=create,
        )
        if store is not None:
            cache[repo_id] = store
        return store


async def resolve_workspace_vector_store(app, repo_id: str) -> Any | None:
    """Backward-compatible app wrapper for repo vector-store resolution.

    Search callers do not create a new index, so ``None`` is returned when the
    repository has no persisted LanceDB directory yet.
    """
    return await resolve_repo_vector_store(app.state, repo_id)


def _resolve_embedder_from_state(app_state):
    """Pull the same embedder the primary vector store was built with.

    ``InMemoryVectorStore`` and ``LanceDBVectorStore`` both expose
    ``_embedder`` (set in __init__). Falls back to a fresh mock when the
    primary store doesn't expose one — keeps semantic search "working"
    against LanceDB stores built with the mock embedder during tests.
    """
    primary_vs = getattr(app_state, "vector_store", None)
    embedder = getattr(primary_vs, "_embedder", None) if primary_vs else None
    if embedder is None:
        from repowise.core.providers.embedding.base import MockEmbedder

        embedder = MockEmbedder()
    return embedder


async def close_workspace_vector_stores(app) -> None:
    """Close every cached workspace vector store. Called on shutdown."""
    cache: dict | None = getattr(app.state, "workspace_vector_stores", None)
    if not cache:
        return
    primary = getattr(app.state, "vector_store", None)
    for store in list(cache.values()):
        if store is primary:
            continue
        with suppress(Exception):
            await store.close()
    cache.clear()
