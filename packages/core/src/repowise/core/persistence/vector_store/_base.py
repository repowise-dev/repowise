"""Vector store abstract base class and shared helpers.

The concrete implementations live in sibling modules
(:mod:`in_memory`, :mod:`lancedb_store`, :mod:`pgvector_store`) and are
re-exported from the package ``__init__`` so the historical import path
``repowise.core.persistence.vector_store`` keeps working unchanged.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod

from ..search import SearchResult

__all__ = ["VectorStore", "cosine_similarity"]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (returns 0.0 for zero vectors)."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    denom = norm_a * norm_b
    return dot / denom if denom > 0 else 0.0


class VectorStore(ABC):
    """Abstract vector store.  All methods are async."""

    @abstractmethod
    async def embed_and_upsert(self, page_id: str, text: str, metadata: dict) -> None:
        """Embed *text* and upsert the vector under *page_id*."""
        ...

    async def embed_batch(self, items: list[tuple[str, str, dict]]) -> None:
        """Embed and upsert many ``(page_id, text, metadata)`` items at once.

        The default implementation processes items sequentially via
        :meth:`embed_and_upsert`, so any backend gets correct behaviour for
        free. Backends that can embed a whole batch in a single model call
        (the common case) override this to amortise the network / GPU
        round-trip — see the bundled stores. Callers may always use this
        path; it never has worse semantics than calling
        :meth:`embed_and_upsert` in a loop.
        """
        for page_id, text, metadata in items:
            await self.embed_and_upsert(page_id, text, metadata)

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Embed *query* and return the *limit* nearest pages."""
        ...

    async def search_many(self, queries: list[str], limit: int = 10) -> list[list[SearchResult]]:
        """Batch variant of :meth:`search` — one result list per query, aligned
        by index.

        The default implementation fires the per-query searches concurrently
        via ``asyncio.gather``; a failed query yields an empty list (matching
        the caller-side behaviour of swallowing a single failed search).
        Backends override this to embed *all* queries in a single embedder
        call — the network round-trip dominates each search, so batching the
        embedding turns N round-trips into 1.
        """
        import asyncio as _asyncio

        if not queries:
            return []
        results = await _asyncio.gather(
            *(self.search(q, limit=limit) for q in queries), return_exceptions=True
        )
        return [r if isinstance(r, list) else [] for r in results]

    @abstractmethod
    async def delete(self, page_id: str) -> None:
        """Remove the vector for *page_id* from the store."""
        ...

    async def delete_many(self, page_ids: list[str]) -> None:
        """Remove the vectors for many *page_ids* from the store.

        Embeddings are keyed by page_id, so when a re-index sweeps stale
        structurally-keyed pages their vectors must be dropped too — otherwise
        a retired page's embedding lingers and pollutes search. The default
        implementation loops over :meth:`delete`; backends that can express a
        single bulk delete override this. Empty input is a no-op.
        """
        for page_id in page_ids:
            await self.delete(page_id)

    @abstractmethod
    async def close(self) -> None:
        """Release any resources held by the store."""
        ...

    async def list_page_ids(self) -> set[str]:
        """Return the set of page IDs currently stored.

        Used by ``repowise doctor --repair`` to detect three-store
        inconsistencies.  Implementations may override for efficiency.
        """
        return set()  # default: empty (subclasses should override)

    async def get_page_summary_by_path(self, path: str) -> dict | None:
        """Return {'summary': str, 'key_exports': list[str]} for a previously-indexed page, or None.

        Used for RAG context injection during doc generation: when generating page B
        that imports A, we fetch A's previously-generated summary and feed it to the LLM.
        """
        return None  # default: no-op (subclasses should override)

    async def get_page_summaries_by_paths(self, paths: list[str]) -> dict[str, dict]:
        """Batch variant of :meth:`get_page_summary_by_path`.

        Returns a mapping of resolved paths → summary dict for every
        input path that produced a non-None result. The default
        implementation fires all per-path calls concurrently via
        ``asyncio.gather`` so callers don't have to await each one
        sequentially — backends that can do a single SQL/index scan
        should override this for the obvious efficiency gain.
        """
        import asyncio as _asyncio

        if not paths:
            return {}
        coros = [self.get_page_summary_by_path(p) for p in paths]
        results = await _asyncio.gather(*coros, return_exceptions=True)
        out: dict[str, dict] = {}
        for path, result in zip(paths, results, strict=False):
            if isinstance(result, dict) and result.get("summary"):
                out[path] = result
        return out
