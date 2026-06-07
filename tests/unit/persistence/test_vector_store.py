"""Unit tests for Embedder and VectorStore.

Tests use MockEmbedder (8-dim, deterministic, zero deps) and
InMemoryVectorStore (cosine similarity, no external deps).
LanceDB-specific tests are skipped if lancedb is not installed.
"""

from __future__ import annotations

import math

import pytest

from repowise.core.providers.embedding.base import Embedder, MockEmbedder

# ---------------------------------------------------------------------------
# MockEmbedder
# ---------------------------------------------------------------------------


async def test_mock_embedder_returns_unit_vectors():
    emb = MockEmbedder()
    vecs = await emb.embed(["hello world", "goodbye moon"])
    for vec in vecs:
        norm = math.sqrt(sum(x * x for x in vec))
        assert abs(norm - 1.0) < 1e-6, f"Vector is not unit length: norm={norm}"


async def test_mock_embedder_returns_correct_dimension():
    emb = MockEmbedder()
    vecs = await emb.embed(["test"])
    assert len(vecs[0]) == MockEmbedder.dimensions


async def test_mock_embedder_is_deterministic():
    emb = MockEmbedder()
    v1 = (await emb.embed(["hello"]))[0]
    v2 = (await emb.embed(["hello"]))[0]
    assert v1 == v2


async def test_mock_embedder_different_texts_different_vectors():
    emb = MockEmbedder()
    v1 = (await emb.embed(["hello"]))[0]
    v2 = (await emb.embed(["goodbye"]))[0]
    assert v1 != v2


async def test_mock_embedder_batch_equals_individual():
    emb = MockEmbedder()
    texts = ["alpha", "beta", "gamma"]
    batch = await emb.embed(texts)
    for i, text in enumerate(texts):
        individual = (await emb.embed([text]))[0]
        assert batch[i] == individual


async def test_mock_embedder_satisfies_protocol():
    emb = MockEmbedder()
    assert isinstance(emb, Embedder)


# ---------------------------------------------------------------------------
# InMemoryVectorStore
# ---------------------------------------------------------------------------


async def test_in_memory_store_empty_search_returns_empty(in_memory_vector_store):
    results = await in_memory_vector_store.search("anything", limit=5)
    assert results == []


async def test_in_memory_store_upsert_and_search(in_memory_vector_store):
    await in_memory_vector_store.embed_and_upsert(
        "page1",
        "Python decorator pattern for caching",
        {
            "title": "Decorators",
            "page_type": "file_page",
            "target_path": "src/cache.py",
            "content": "Python decorator pattern for caching",
        },
    )
    results = await in_memory_vector_store.search("Python decorator caching")
    assert len(results) == 1
    assert results[0].page_id == "page1"
    assert results[0].title == "Decorators"


async def test_in_memory_store_search_returns_closest(in_memory_vector_store):
    """Store two pages; query text similar to page1 should rank page1 first."""
    await in_memory_vector_store.embed_and_upsert(
        "p1",
        "Python decorator pattern",
        {
            "title": "p1",
            "page_type": "file_page",
            "target_path": "a.py",
            "content": "Python decorator pattern",
        },
    )
    await in_memory_vector_store.embed_and_upsert(
        "p2",
        "Rust ownership and borrowing",
        {
            "title": "p2",
            "page_type": "file_page",
            "target_path": "b.py",
            "content": "Rust ownership and borrowing",
        },
    )
    results = await in_memory_vector_store.search("Python decorator pattern", limit=2)
    assert results[0].page_id == "p1"


async def test_in_memory_store_limit_respected(in_memory_vector_store):
    for i in range(10):
        await in_memory_vector_store.embed_and_upsert(
            f"page{i}",
            f"content number {i}",
            {
                "title": f"Page {i}",
                "page_type": "file_page",
                "target_path": f"f{i}.py",
                "content": f"content number {i}",
            },
        )
    results = await in_memory_vector_store.search("content", limit=3)
    assert len(results) == 3


async def test_in_memory_store_delete_removes_entry(in_memory_vector_store):
    await in_memory_vector_store.embed_and_upsert(
        "to-delete",
        "transient content",
        {
            "title": "tmp",
            "page_type": "file_page",
            "target_path": "t.py",
            "content": "transient content",
        },
    )
    assert len(in_memory_vector_store) == 1

    await in_memory_vector_store.delete("to-delete")
    assert len(in_memory_vector_store) == 0

    results = await in_memory_vector_store.search("transient")
    assert results == []


async def test_in_memory_store_delete_nonexistent_is_safe(in_memory_vector_store):
    """Deleting a page that doesn't exist should not raise."""
    await in_memory_vector_store.delete("ghost-page")


async def test_in_memory_store_upsert_overwrites(in_memory_vector_store):
    meta = {
        "title": "v1",
        "page_type": "file_page",
        "target_path": "a.py",
        "content": "version one",
    }
    await in_memory_vector_store.embed_and_upsert("p", "version one", meta)
    assert len(in_memory_vector_store) == 1

    meta2 = {
        "title": "v2",
        "page_type": "file_page",
        "target_path": "a.py",
        "content": "version two",
    }
    await in_memory_vector_store.embed_and_upsert("p", "version two", meta2)
    assert len(in_memory_vector_store) == 1  # still one entry

    results = await in_memory_vector_store.search("version two")
    assert results[0].title == "v2"


async def test_in_memory_store_score_between_zero_and_one(in_memory_vector_store):
    """Cosine similarity is in [-1, 1]; for unit vectors all positive texts give ≥ 0."""
    await in_memory_vector_store.embed_and_upsert(
        "p1",
        "machine learning algorithms",
        {
            "title": "ML",
            "page_type": "file_page",
            "target_path": "ml.py",
            "content": "machine learning algorithms",
        },
    )
    results = await in_memory_vector_store.search("machine learning")
    assert 0.0 <= results[0].score <= 1.0


async def test_in_memory_store_snippet_truncated(in_memory_vector_store):
    long_content = "x" * 500
    await in_memory_vector_store.embed_and_upsert(
        "p",
        long_content,
        {"title": "t", "page_type": "file_page", "target_path": "f.py", "content": long_content},
    )
    results = await in_memory_vector_store.search(long_content[:10])
    assert len(results[0].snippet) <= 200


async def test_in_memory_store_search_type_is_vector(in_memory_vector_store):
    await in_memory_vector_store.embed_and_upsert(
        "p",
        "text",
        {"title": "t", "page_type": "file_page", "target_path": "f.py", "content": "text"},
    )
    results = await in_memory_vector_store.search("text")
    assert results[0].search_type == "vector"


async def test_in_memory_store_close_clears(in_memory_vector_store):
    await in_memory_vector_store.embed_and_upsert(
        "p",
        "text",
        {"title": "t", "page_type": "file_page", "target_path": "f.py", "content": "text"},
    )
    await in_memory_vector_store.close()
    assert len(in_memory_vector_store) == 0


# ---------------------------------------------------------------------------
# embed_batch
# ---------------------------------------------------------------------------


async def test_in_memory_embed_batch_empty_is_noop(in_memory_vector_store):
    await in_memory_vector_store.embed_batch([])
    assert len(in_memory_vector_store) == 0


async def test_in_memory_embed_batch_matches_single_path(mock_embedder):
    """embed_batch must produce the same stored vectors as N embed_and_upsert calls."""
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    items = [
        (
            f"p{i}",
            f"content number {i}",
            {
                "title": f"Page {i}",
                "page_type": "file_page",
                "target_path": f"f{i}.py",
                "content": f"content number {i}",
            },
        )
        for i in range(5)
    ]

    batched = InMemoryVectorStore(mock_embedder)
    await batched.embed_batch(items)

    single = InMemoryVectorStore(mock_embedder)
    for page_id, text, meta in items:
        await single.embed_and_upsert(page_id, text, meta)

    assert len(batched) == len(single) == 5
    # Identical query results prove vectors + metadata landed identically.
    bq = await batched.search("content number 2", limit=5)
    sq = await single.search("content number 2", limit=5)
    assert [(r.page_id, r.title) for r in bq] == [(r.page_id, r.title) for r in sq]


async def test_in_memory_embed_batch_searchable(in_memory_vector_store):
    await in_memory_vector_store.embed_batch(
        [
            (
                "p1",
                "Python decorator pattern",
                {"title": "Dec", "page_type": "file_page", "target_path": "a.py", "content": "Python decorator pattern"},
            ),
            (
                "p2",
                "Rust ownership model",
                {"title": "Rust", "page_type": "file_page", "target_path": "b.py", "content": "Rust ownership model"},
            ),
        ]
    )
    assert len(in_memory_vector_store) == 2
    results = await in_memory_vector_store.search("Python decorator pattern", limit=2)
    assert results[0].page_id == "p1"


async def test_base_embed_batch_default_uses_single_path(mock_embedder):
    """A backend that does NOT override embed_batch falls back to the ABC's
    sequential default, which must still upsert every item correctly.
    """
    from repowise.core.persistence.vector_store import VectorStore

    class _CountingStore(VectorStore):
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def embed_and_upsert(self, page_id, text, metadata):
            self.calls.append(page_id)

        async def search(self, query, limit=10):
            return []

        async def delete(self, page_id):
            return None

        async def close(self):
            return None

    store = _CountingStore()
    await store.embed_batch([("a", "x", {}), ("b", "y", {})])
    assert store.calls == ["a", "b"]


# ---------------------------------------------------------------------------
# LanceDB (optional — skipped if not installed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lancedb_vector_store_basic(tmp_path, mock_embedder):
    lancedb = pytest.importorskip("lancedb")  # noqa: F841
    from repowise.core.persistence.vector_store import LanceDBVectorStore

    store = LanceDBVectorStore(str(tmp_path / "lance"), mock_embedder)
    try:
        await store.embed_and_upsert(
            "p1",
            "Python async generators",
            {"title": "Async", "page_type": "file_page", "target_path": "a.py"},
        )
        results = await store.search("async generators", limit=5)
        assert len(results) >= 1
        assert results[0].page_id == "p1"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_lancedb_embed_batch(tmp_path, mock_embedder):
    lancedb = pytest.importorskip("lancedb")  # noqa: F841
    from repowise.core.persistence.vector_store import LanceDBVectorStore

    store = LanceDBVectorStore(str(tmp_path / "lance"), mock_embedder)
    try:
        await store.embed_batch(
            [
                ("p1", "Python async generators", {"target_path": "a.py"}),
                ("p2", "Rust ownership model", {"target_path": "b.py"}),
            ]
        )
        ids = await store.list_page_ids()
        assert ids == {"p1", "p2"}
    finally:
        await store.close()


class _FixedDimEmbedder:
    """Deterministic embedder with a configurable output dimension."""

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i, _ in enumerate(texts):
            vec = [0.0] * self.dimensions
            vec[i % self.dimensions] = 1.0  # unit-length
            out.append(vec)
        return out


@pytest.mark.asyncio
async def test_lancedb_reindex_recreates_table_on_dimension_change(tmp_path, mock_embedder):
    """Switching embedders (mock dim 8 → dim 1536) must recreate the table.

    Regression test for #234: the stale wiki_pages table kept the old vector
    schema, so every write failed with an opaque LanceDB IO error instead of
    being transparently rebuilt for the new embedder.
    """
    lancedb = pytest.importorskip("lancedb")  # noqa: F841
    from repowise.core.persistence.vector_store import LanceDBVectorStore

    db_path = str(tmp_path / "lance")

    # First index with the 8-dim mock embedder.
    store = LanceDBVectorStore(db_path, mock_embedder)
    try:
        await store.embed_and_upsert(
            "p1", "old content", {"target_path": "a.py", "content": "old content"}
        )
    finally:
        await store.close()

    # Reindex against the same path with a different-dimension embedder.
    big_embedder = _FixedDimEmbedder(1536)
    store = LanceDBVectorStore(db_path, big_embedder)
    try:
        await store.embed_and_upsert(
            "p2", "new content", {"target_path": "b.py", "content": "new content"}
        )
        # The old 8-dim row is gone; the table now holds only the new write.
        ids = await store.list_page_ids()
        assert ids == {"p2"}
        results = await store.search("new content", limit=5)
        assert {r.page_id for r in results} == {"p2"}
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# embed_batch chunking (regression: one giant request lost a whole level)
# ---------------------------------------------------------------------------


class _RecordingEmbedder:
    """Mock-dim embedder that records every embed() call's batch shape."""

    dimensions = 8

    def __init__(self, fail_on_call: int | None = None) -> None:
        self.calls: list[list[str]] = []
        self._fail_on_call = fail_on_call

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self._fail_on_call is not None and len(self.calls) == self._fail_on_call:
            raise RuntimeError("simulated 400 max_tokens_per_request")
        return [[1.0] + [0.0] * 7 for _ in texts]


def _items(n: int, text: str = "content") -> list[tuple[str, str, dict]]:
    return [(f"p{i}", text, {"target_path": f"f{i}.py"}) for i in range(n)]


@pytest.mark.asyncio
async def test_in_memory_embed_batch_chunks_requests():
    # Regression: a generation level of 275 full pages went out as ONE
    # embedder request (~560k tokens) and failed OpenAI's 300k cap, silently
    # losing every file-page embedding. Batches must be chunked.
    from repowise.core.persistence.vector_store import InMemoryVectorStore
    from repowise.core.persistence.vector_store._base import EMBED_BATCH_MAX_ITEMS

    emb = _RecordingEmbedder()
    store = InMemoryVectorStore(emb)
    await store.embed_batch(_items(EMBED_BATCH_MAX_ITEMS * 2 + 3))
    assert len(emb.calls) == 3
    assert all(len(c) <= EMBED_BATCH_MAX_ITEMS for c in emb.calls)
    assert len(await store.list_page_ids()) == EMBED_BATCH_MAX_ITEMS * 2 + 3


@pytest.mark.asyncio
async def test_in_memory_embed_batch_caps_text_length():
    from repowise.core.persistence.vector_store import InMemoryVectorStore
    from repowise.core.persistence.vector_store._base import EMBED_TEXT_MAX_CHARS

    emb = _RecordingEmbedder()
    store = InMemoryVectorStore(emb)
    await store.embed_batch(_items(1, text="x" * (EMBED_TEXT_MAX_CHARS + 5_000)))
    assert len(emb.calls[0][0]) == EMBED_TEXT_MAX_CHARS


@pytest.mark.asyncio
async def test_lancedb_embed_batch_isolates_failed_chunk(tmp_path):
    # One failing chunk must not sink the rest of the level, but the loss
    # must surface as an error (the old code swallowed it entirely).
    pytest.importorskip("lancedb")
    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.core.persistence.vector_store._base import EMBED_BATCH_MAX_ITEMS

    emb = _RecordingEmbedder(fail_on_call=2)
    store = LanceDBVectorStore(str(tmp_path / "lance"), emb)
    try:
        with pytest.raises(RuntimeError, match="failed to embed"):
            await store.embed_batch(_items(EMBED_BATCH_MAX_ITEMS * 3))
        ids = await store.list_page_ids()
        # Chunks 1 and 3 persisted; only chunk 2 was lost.
        assert len(ids) == EMBED_BATCH_MAX_ITEMS * 2
    finally:
        await store.close()
