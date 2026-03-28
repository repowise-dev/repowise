"""Unit tests for Embedder and VectorStore.

Tests use MockEmbedder (8-dim, deterministic, zero deps) and
InMemoryVectorStore (cosine similarity, no external deps).
LanceDB-specific tests are skipped if lancedb is not installed.
"""

from __future__ import annotations

import math

import pytest

from repowise.core.providers.embedding.base import Embedder, MockEmbedder
from repowise.core.persistence.vector_store import InMemoryVectorStore


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
        {"title": "Decorators", "page_type": "file_page", "target_path": "src/cache.py", "content": "Python decorator pattern for caching"},
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
        {"title": "p1", "page_type": "file_page", "target_path": "a.py", "content": "Python decorator pattern"},
    )
    await in_memory_vector_store.embed_and_upsert(
        "p2",
        "Rust ownership and borrowing",
        {"title": "p2", "page_type": "file_page", "target_path": "b.py", "content": "Rust ownership and borrowing"},
    )
    results = await in_memory_vector_store.search("Python decorator pattern", limit=2)
    assert results[0].page_id == "p1"


async def test_in_memory_store_limit_respected(in_memory_vector_store):
    for i in range(10):
        await in_memory_vector_store.embed_and_upsert(
            f"page{i}",
            f"content number {i}",
            {"title": f"Page {i}", "page_type": "file_page", "target_path": f"f{i}.py", "content": f"content number {i}"},
        )
    results = await in_memory_vector_store.search("content", limit=3)
    assert len(results) == 3


async def test_in_memory_store_delete_removes_entry(in_memory_vector_store):
    await in_memory_vector_store.embed_and_upsert(
        "to-delete",
        "transient content",
        {"title": "tmp", "page_type": "file_page", "target_path": "t.py", "content": "transient content"},
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
    meta = {"title": "v1", "page_type": "file_page", "target_path": "a.py", "content": "version one"}
    await in_memory_vector_store.embed_and_upsert("p", "version one", meta)
    assert len(in_memory_vector_store) == 1

    meta2 = {"title": "v2", "page_type": "file_page", "target_path": "a.py", "content": "version two"}
    await in_memory_vector_store.embed_and_upsert("p", "version two", meta2)
    assert len(in_memory_vector_store) == 1  # still one entry

    results = await in_memory_vector_store.search("version two")
    assert results[0].title == "v2"


async def test_in_memory_store_score_between_zero_and_one(in_memory_vector_store):
    """Cosine similarity is in [-1, 1]; for unit vectors all positive texts give ≥ 0."""
    await in_memory_vector_store.embed_and_upsert(
        "p1",
        "machine learning algorithms",
        {"title": "ML", "page_type": "file_page", "target_path": "ml.py", "content": "machine learning algorithms"},
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
