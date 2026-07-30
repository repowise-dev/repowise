"""One question, one embedding round-trip.

Answering a question embedded its text up to three times: the main hybrid
fetch, the concept lookup a subsystem-shaped question triggers, and the
neighbourhood re-rank a flow-shaped question triggers. Each one is a billed
network call to the embedding provider for a vector that cannot differ between
them — the store already exposes ``embed_texts`` / ``search_by_vector`` for
exactly this, and the answer path was the one caller not using them.

The spy here counts calls into the embedder, which is the only place the waste
was visible: every stage returned correct results while paying three times.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.server.mcp_server import _answer_pipeline as pipeline
from repowise.server.mcp_server import _neighbor_rerank as rerank

_QUESTION = "walk me through how a changed file travels into the persist path"


class _SpyEmbedder:
    """MockEmbedder that records every batch of texts it is asked to embed."""

    def __init__(self) -> None:
        self._inner = MockEmbedder()
        self.batches: list[list[str]] = []

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return await self._inner.embed(texts)


@pytest.fixture
async def spy_store():
    embedder = _SpyEmbedder()
    store = InMemoryVectorStore(embedder=embedder)
    await store.embed_batch(
        [
            (
                f"file_page:pkg/alpha/{name}.py",
                f"The {name} module hands changed files to the persist path.",
                {
                    "title": name,
                    "page_type": "file_page",
                    "target_path": f"pkg/alpha/{name}.py",
                },
            )
            for name in ("one", "two", "three")
        ]
    )
    embedder.batches.clear()  # indexing is not what is under test
    yield store, embedder
    await store.close()


@pytest.fixture(autouse=True)
def _clear_vector_cache():
    """Cross-test isolation: the cache is module-level, as the store it keys on
    is process-lived."""
    cache = getattr(pipeline, "_QUESTION_VECTORS", None)
    if cache is not None:
        cache.clear()
    yield
    if cache is not None:
        cache.clear()


def _questions_embedded(embedder: _SpyEmbedder) -> list[str]:
    return [t for batch in embedder.batches for t in batch]


async def test_the_main_fetch_embeds_the_question_once(spy_store):
    store, embedder = spy_store
    ctx = SimpleNamespace(fts=None, vector_store=store)

    hits = await pipeline.hybrid_retrieve(_QUESTION, ctx)

    assert hits, "the fetch must still return hits"
    assert _questions_embedded(embedder) == [_QUESTION]


async def test_the_concept_lookup_does_not_re_embed_after_the_main_fetch(spy_store):
    """A subsystem-shaped question runs both stages; both need the same vector."""
    store, embedder = spy_store
    ctx = SimpleNamespace(fts=None, vector_store=store)

    await pipeline.hybrid_retrieve(_QUESTION, ctx)
    await pipeline._semantic_concept_paths(_QUESTION, ctx)

    assert _questions_embedded(embedder) == [_QUESTION]


async def test_three_stages_share_one_embedding(spy_store):
    """The gate: the main fetch, the concept lookup and the neighbourhood
    re-rank each need the question's vector and between them pay once."""
    store, embedder = spy_store
    ctx = SimpleNamespace(fts=None, vector_store=store)
    pool = {"pkg/alpha/two.py", "pkg/alpha/three.py"}

    await pipeline.hybrid_retrieve(_QUESTION, ctx)
    await pipeline._semantic_concept_paths(_QUESTION, ctx)
    await rerank._relevance_order(
        store, _QUESTION, pool, vector=await pipeline.question_vector(ctx, _QUESTION)
    )

    assert _questions_embedded(embedder) == [_QUESTION]


async def test_the_relevance_scan_still_ranks_the_pool_from_the_shared_vector(spy_store):
    """Reusing the vector must return the same pool members as searching the
    text — a cheaper call that ranked nothing would be a silent regression."""
    store, embedder = spy_store
    ctx = SimpleNamespace(fts=None, vector_store=store)
    pool = {"pkg/alpha/two.py", "pkg/alpha/three.py"}

    from_text = await rerank._relevance_order(store, _QUESTION, pool)
    embedder.batches.clear()
    from_vector = await rerank._relevance_order(
        store, _QUESTION, pool, vector=await pipeline.question_vector(ctx, _QUESTION)
    )

    assert from_vector == from_text
    assert sorted(from_vector) == sorted(pool)


async def test_a_backend_that_cannot_embed_directly_still_retrieves(spy_store, caplog):
    """``embed_texts`` returns None for a store holding no embedder of its own.
    The stages then embed per-search as before, which costs money but must not
    cost results."""
    store, embedder = spy_store
    ctx = SimpleNamespace(fts=None, vector_store=store)

    async def _no_embedder(_texts):
        return None

    store.embed_texts = _no_embedder  # type: ignore[method-assign]

    assert await pipeline.question_vector(ctx, _QUESTION) is None
    hits = await pipeline.hybrid_retrieve(_QUESTION, ctx)

    assert hits
    assert _questions_embedded(embedder) == [_QUESTION]


async def test_a_failing_embed_warns_and_leaves_retrieval_working(spy_store, caplog):
    """The up-front embed is an optimisation. Losing it silently would hide a
    broken embedder behind three per-stage calls that also fail."""
    store, _embedder = spy_store
    ctx = SimpleNamespace(fts=None, vector_store=store)

    async def _boom(_texts):
        raise RuntimeError("embedding provider down")

    store.embed_texts = _boom  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="repowise.mcp.answer"):
        assert await pipeline.question_vector(ctx, _QUESTION) is None

    assert any("embed the question" in r.getMessage() for r in caplog.records), caplog.text


async def test_the_cache_is_bounded(spy_store):
    """A per-question entry that is never evicted is a leak in a long-lived
    server."""
    store, _embedder = spy_store
    ctx = SimpleNamespace(fts=None, vector_store=store)

    for i in range(pipeline._QUESTION_VECTOR_CACHE_MAX + 3):
        await pipeline.question_vector(ctx, f"question number {i}")

    assert len(pipeline._QUESTION_VECTORS) == pipeline._QUESTION_VECTOR_CACHE_MAX
