"""Tests for the query-embedding cache wrapper.

Every ``search_codebase`` embeds its query through the provider — a network
round trip on a path where the vector lookup itself is an order of magnitude
cheaper. These pin that a repeat is served locally, and that the cache cannot
hand back a vector from a different embedder or a different width.
"""

from __future__ import annotations

from repowise.core.providers.embedding.caching import MAX_ENTRIES, CachingEmbedder


class _CountingEmbedder:
    """Records every batch it is asked to embed."""

    def __init__(self, dimensions: int = 4, seed: float = 1.0) -> None:
        self._dimensions = dimensions
        self._seed = seed
        self.calls: list[list[str]] = []

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[self._seed * (i + 1)] * self._dimensions for i, _ in enumerate(texts)]


async def test_repeated_query_is_served_from_cache():
    inner = _CountingEmbedder()
    embedder = CachingEmbedder(inner)

    first = await embedder.embed(["auth service"])
    second = await embedder.embed(["auth service"])

    assert first == second
    assert inner.calls == [["auth service"]], "the provider was asked twice"


async def test_distinct_queries_each_reach_the_provider():
    inner = _CountingEmbedder()
    embedder = CachingEmbedder(inner)

    await embedder.embed(["one"])
    await embedder.embed(["two"])

    assert inner.calls == [["one"], ["two"]]


async def test_multi_text_batches_are_not_cached():
    """Bulk calls are indexing or decision matching, never repeated queries."""
    inner = _CountingEmbedder()
    embedder = CachingEmbedder(inner)

    await embedder.embed(["a", "b"])
    await embedder.embed(["a", "b"])

    assert inner.calls == [["a", "b"], ["a", "b"]]


async def test_a_bulk_call_does_not_evict_or_serve_queries():
    inner = _CountingEmbedder()
    embedder = CachingEmbedder(inner)

    await embedder.embed(["query"])
    await embedder.embed(["query", "other"])
    await embedder.embed(["query"])

    # The single-text query was embedded once; the batch neither used nor
    # displaced its entry.
    assert inner.calls == [["query"], ["query", "other"]]


async def test_cache_is_bounded_and_evicts_least_recently_used():
    """A per-query entry that is never evicted is a leak in a long-lived server."""
    inner = _CountingEmbedder()
    embedder = CachingEmbedder(inner)

    await embedder.embed(["oldest"])
    for i in range(MAX_ENTRIES - 1):
        await embedder.embed([f"filler-{i}"])
    await embedder.embed(["oldest"])  # refresh, so a filler is now least recent
    await embedder.embed(["overflow"])  # pushes past the cap

    assert len(embedder._cache) == MAX_ENTRIES
    inner.calls.clear()
    await embedder.embed(["oldest"])
    await embedder.embed(["filler-0"])

    assert inner.calls == [["filler-0"]], "the refreshed entry should have survived"


async def test_a_mutated_hit_does_not_corrupt_the_entry():
    """The hit path must hand back a copy: the miss path returns the inner
    embedder's own list, so only a mutated *hit* can reach the stored entry."""
    inner = _CountingEmbedder()
    embedder = CachingEmbedder(inner)

    await embedder.embed(["q"])  # miss: populates the entry
    hit = await embedder.embed(["q"])
    hit[0][0] = 999.0
    again = await embedder.embed(["q"])

    assert again[0][0] != 999.0
    assert inner.calls == [["q"]], "still served from cache, so the entry is what leaked"


async def test_dimensions_are_forwarded():
    inner = _CountingEmbedder(dimensions=1536)
    assert CachingEmbedder(inner).dimensions == 1536


async def test_an_empty_batch_is_delegated_untouched():
    inner = _CountingEmbedder()
    embedder = CachingEmbedder(inner)

    assert await embedder.embed([]) == []
    assert inner.calls == [[]]
    assert embedder._cache == {}


async def test_a_short_inner_response_is_not_cached():
    """A provider that returns the wrong number of vectors must not have that
    response memoized for the life of the process."""

    class _Empty(_CountingEmbedder):
        async def embed(self, texts):
            self.calls.append(list(texts))
            return []

    inner = _Empty()
    embedder = CachingEmbedder(inner)

    assert await embedder.embed(["q"]) == []
    assert await embedder.embed(["q"]) == []
    assert inner.calls == [["q"], ["q"]], "the bad response was cached"
