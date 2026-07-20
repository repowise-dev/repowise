"""Vector store abstract base class and shared helpers.

The concrete implementations live in sibling modules
(:mod:`in_memory`, :mod:`lancedb_store`, :mod:`pgvector_store`) and are
re-exported from the package ``__init__`` so the historical import path
``repowise.core.persistence.vector_store`` keeps working unchanged.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..search import SearchResult

__all__ = [
    "EMBED_BATCH_MAX_ITEMS",
    "EMBED_TEXT_MAX_CHARS",
    "VectorStore",
    "cosine_similarity",
    "iter_embed_chunks",
]

# One embedder call per chunk of this many items. OpenAI rejects embedding
# requests past 300k total tokens — a generation level of 275 full wiki
# pages (~560k tokens) failed in one giant request and silently lost the
# whole level's embeddings (measured live: 400 max_tokens_per_request).
# 16 items x EMBED_TEXT_MAX_CHARS worst-case is ~120k tokens, comfortably
# under the cap and inside the embedder adapters' request timeouts
# (a 16-page chunk measured at 0.6s against OpenAI).
EMBED_BATCH_MAX_ITEMS = 16

# Per-input cap (~7.5k tokens): embedding models reject a single input past
# ~8,192 tokens, and one oversized page must not sink its whole chunk.
EMBED_TEXT_MAX_CHARS = 30_000


def iter_embed_chunks(
    items: list[tuple[str, str, dict]],
) -> Iterator[tuple[list[tuple[str, str, dict]], list[str]]]:
    """Yield ``(chunk, capped_texts)`` slices sized for one embedder request."""
    for start in range(0, len(items), EMBED_BATCH_MAX_ITEMS):
        chunk = items[start : start + EMBED_BATCH_MAX_ITEMS]
        yield chunk, [text[:EMBED_TEXT_MAX_CHARS] for _, text, _ in chunk]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (returns 0.0 for zero vectors)."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    denom = norm_a * norm_b
    return dot / denom if denom > 0 else 0.0


class VectorStore(ABC):
    """Abstract vector store.  All methods are async."""

    # Whether vectors written in a previous process survive into this one.
    # Durable backends set True; generation uses this to skip re-embedding
    # pages whose content is byte-identical to the prior run. Ephemeral
    # stores (in-memory) start empty every run and must keep embedding them.
    persists_across_runs: bool = False

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

    async def embed_texts(self, texts: list[str]) -> list[list[float]] | None:
        """Embed *texts* in batched embedder requests, without upserting.

        Lets a caller that needs the raw vectors (e.g. decision dedup, which
        searches *and* upserts the same text) pay for one batched embedding
        instead of one round-trip per item. Returns ``None`` when the backend
        holds no embedder — callers must fall back to the per-item text APIs.
        Chunked so a large input can't blow the embedder's per-request token
        cap; each text is capped at :data:`EMBED_TEXT_MAX_CHARS`.
        """
        embedder = getattr(self, "_embedder", None)
        if embedder is None:
            return None  # backend can't embed directly — caller falls back
        if not texts:
            return []
        out: list[list[float]] = []
        for _chunk, capped_texts in iter_embed_chunks([("", t, {}) for t in texts]):
            out.extend(await embedder.embed(capped_texts))
        return out

    async def search_by_vector(
        self, vector: list[float], limit: int = 10
    ) -> list[SearchResult] | None:
        """Return the *limit* nearest pages to a precomputed query *vector*.

        Batching hook for callers that already embedded their queries via
        :meth:`embed_texts`. Returns ``None`` when the backend can't search by
        raw vector (callers fall back to :meth:`search`), never raises for
        that reason.
        """
        return None

    async def upsert_vectors(self, items: list[tuple[str, list[float], dict]]) -> bool:
        """Upsert many ``(page_id, vector, metadata)`` items without embedding.

        The write-side counterpart of :meth:`search_by_vector`: callers that
        computed vectors once via :meth:`embed_texts` can persist them without
        a second embedder round-trip per item. Returns ``False`` when the
        backend doesn't support raw-vector writes (callers fall back to
        :meth:`embed_batch`), ``True`` after a successful write.
        """
        return False

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
