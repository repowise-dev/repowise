"""Vector store abstract base class and shared helpers.

The concrete implementations live in sibling modules
(:mod:`in_memory`, :mod:`lancedb_store`, :mod:`pgvector_store`) and are
re-exported from the package ``__init__`` so the historical import path
``repowise.core.persistence.vector_store`` keeps working unchanged.
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..information_floor import count_page_denied_a_vector, meets_information_floor
from ..search import SearchResult

__all__ = [
    "EMBED_BATCH_MAX_ITEMS",
    "EMBED_TEXT_MAX_CHARS",
    "STORED_SNIPPET_CHARS",
    "VectorStore",
    "cosine_similarity",
    "embed_item",
    "iter_embed_chunks",
]

logger = logging.getLogger(__name__)

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

# How much of a page's content a vector row keeps for its evidence snippet.
#
# Defined here rather than in a backend because it belongs to the item every
# backend is handed, not to any one store: a row can only ever show what the
# recipe put in front of it, so a store that cuts at a width the recipe never
# reaches is cutting nothing. That is what happened when this widened — the
# store raised its ceiling while the recipe still handed it 600 characters,
# and on the paths that passed no content at all, an empty string.
STORED_SNIPPET_CHARS = 2_000


def embed_item(
    page_id: str,
    *,
    title: str,
    page_type: str,
    target_path: str,
    summary: str,
    content: str,
) -> tuple[str, str, dict] | None:
    """Build the one ``(page_id, text, metadata)`` item every writer embeds.

    Returns ``None`` when the page says too little to be worth a search slot,
    which callers skip. See the information floor note below.

    Four things write vectors for a page — generation, ``reindex``,
    ``doctor --repair`` and the hosted indexer — and until now each built its
    own text. One embedded the content alone, one prefixed the title, one
    passed neither summary nor path. A vector was therefore not comparable
    with another vector: whether a page could be found by its own name
    depended on which command last wrote it, and nothing anywhere reported
    the difference.

    The text carries all four fields because each answers a question the
    others cannot. ``target_path`` is the strongest one: a page about
    ``search.py`` has no idea it is about ``search.py`` unless its prose
    happens to say so, and prose usually does not repeat its own filename.

    ``title`` is required. A page with no title is not a page whose title is
    empty — it is a writer that lost it, and the row it produces looks
    healthy while being unfindable by name, so this raises rather than
    storing it. The other three may legitimately be empty (some page types
    have no summary; a repository-wide page has no path).

    **The information floor.** A page whose content says too little to answer
    anything gets no vector, and ``None`` comes back instead. The full-text
    index already applies the same rule, and it has to be the same rule: a
    page held out of one arm and kept in the other is still fetched, still
    occupies one of the fixed number of rows retrieval takes before it filters
    anything, and still displaces a page that could have answered. The test is
    applied to ``content`` alone for that reason — the same input the
    full-text side measures, so the two arms cannot disagree about a page.

    The page itself is untouched either way. It stays in ``wiki_pages``, still
    resolves as a link target, and a reader who arrives at it still learns the
    file exists. It is only kept out of the index, where its cost is paid by
    other pages. The floor is 0 by default, which admits everything.
    """
    if not title.strip():
        raise ValueError(
            f"embed_item: page {page_id!r} has no title. A blank title writes a "
            f"vector that cannot be found by name and reports nothing wrong; "
            f"pass the page's real title."
        )
    if not meets_information_floor(content):
        count_page_denied_a_vector()
        return None
    parts = [p for p in (title, target_path, summary, content) if p]
    return (
        page_id,
        "\n".join(parts),
        {
            "title": title,
            "page_type": page_type,
            "target_path": target_path,
            "summary": summary,
            # Wide enough for a store to cut an evidence window out of, and a
            # prefix rather than the page so a large corpus is not held twice.
            "content": content[:STORED_SNIPPET_CHARS],
        },
    )


def iter_embed_chunks(
    items: list[tuple[str, str, dict]],
) -> Iterator[tuple[list[tuple[str, str, dict]], list[str]]]:
    """Yield ``(chunk, capped_texts)`` slices sized for one embedder request.

    Text past :data:`EMBED_TEXT_MAX_CHARS` is dropped, and dropping it is
    reported. The cap was sized when the largest page in a corpus was well
    under it, so for a long time it bound on nothing; a corpus whose page mix
    has since changed can push pages over it, and the characters past the cut
    are simply absent from the vector. Nothing downstream can tell that apart
    from a page that never said those words, so the only place the loss is
    observable is here.

    Reported at ``error`` although the run continues, because the CLI pins
    ``repowise.core`` to that level unless ``--verbose`` is passed. Anything
    quieter would be discarded by the very commands that write the index,
    which is a report that exists only in the tests for it.
    """
    for start in range(0, len(items), EMBED_BATCH_MAX_ITEMS):
        chunk = items[start : start + EMBED_BATCH_MAX_ITEMS]
        for page_id, text, _meta in chunk:
            if len(text) > EMBED_TEXT_MAX_CHARS:
                logger.error(
                    # ``page_id`` is empty for the raw ``embed_texts`` path,
                    # which embeds loose strings belonging to no page.
                    "embed_text_truncated page_id=%s chars=%d chars_dropped=%d cap=%d",
                    page_id,
                    len(text),
                    len(text) - EMBED_TEXT_MAX_CHARS,
                    EMBED_TEXT_MAX_CHARS,
                )
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
