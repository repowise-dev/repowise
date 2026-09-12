"""A retrieval leg that did not run has to say so (finding A18).

Every leg is best-effort with a timeout, deliberately: a slow vector store must
not be able to block an answer. The defect was that the fallback was silent.
An embed that misses its 8s budget returns no vector, retrieval continues
lexical-only, and every health signal still reports green. ``embedder_live``
in particular stays true, correctly, because a configured embedder *is* live
whether or not one call beat the clock. Those are different claims and only
one of them is about the answer the caller is holding.

Measured: five queries in one bake-off run were answered without their vector
leg. Nothing in the response, the logs, or the per-cell record said so, and the
cells were written as clean.
"""

from __future__ import annotations

import asyncio

import pytest

from repowise.server.mcp_server import _answer_pipeline as pipeline


class _Store:
    """A vector store whose embed call never returns in time."""

    def __init__(self, *, embed_hangs=False, search_hangs=False):
        self._embed_hangs = embed_hangs
        self._search_hangs = search_hangs

    async def embed_texts(self, texts, *, kind="document"):
        del kind
        if self._embed_hangs:
            await asyncio.sleep(60)
        return [[0.1, 0.2, 0.3]]

    async def search_by_vector(self, vector, limit):
        return None

    async def search(self, query, limit):
        if self._search_hangs:
            await asyncio.sleep(60)
        return []


class _Fts:
    def __init__(self, *, hangs=False):
        self._hangs = hangs

    async def search(self, query, limit):
        if self._hangs:
            await asyncio.sleep(60)
        return []


class _Ctx:
    def __init__(self, *, store=None, fts=None):
        self.vector_store = store
        self.fts = fts
        self.vector_store_ready = None
        self.session_factory = None
        self.path = "."


@pytest.fixture(autouse=True)
def fresh_record():
    pipeline.begin_leg_record()
    yield


@pytest.mark.asyncio
async def test_a_healthy_leg_records_ok(monkeypatch):
    monkeypatch.setattr(pipeline, "_EMBED_TIMEOUT_S", 0.5)
    await pipeline._safe_vector_search(_Ctx(store=_Store()), "how does retrieval work")
    assert pipeline.retrieval_legs()["vector"] == "ok"
    assert pipeline.degraded_legs(pipeline.retrieval_legs()) == []


@pytest.mark.asyncio
async def test_an_embed_timeout_is_named_and_does_not_fail_the_call(monkeypatch):
    """The exact A18 shape: the answer still comes back, lexical-only."""
    monkeypatch.setattr(pipeline, "_EMBED_TIMEOUT_S", 0.05)
    ctx = _Ctx(store=_Store(embed_hangs=True))

    results = await pipeline._safe_vector_search(ctx, "how does retrieval work")

    assert results == []  # the leg degraded rather than raising
    legs = pipeline.retrieval_legs()
    assert legs["embed"] == "timeout"
    assert "embed" in pipeline.degraded_legs(legs)


@pytest.mark.asyncio
async def test_a_timeout_is_distinguished_from_an_error():
    """They want different responses. A timeout is a budget problem and may
    pass on a retry; an error is a broken backend and will not."""
    ctx = _Ctx(store=_Store(search_hangs=True))
    await pipeline._safe_vector_search(ctx, "q")
    assert pipeline.retrieval_legs()["vector"] == "timeout"


@pytest.mark.asyncio
async def test_an_absent_backend_is_recorded_rather_than_passed_over():
    await pipeline._safe_vector_search(_Ctx(store=None), "q")
    await pipeline._safe_fts_search(_Ctx(fts=None), "q")
    legs = pipeline.retrieval_legs()
    assert legs["vector"] == "absent"
    assert legs["fts"] == "absent"


@pytest.mark.asyncio
async def test_a_slow_full_text_leg_is_named_too():
    await pipeline._safe_fts_search(_Ctx(fts=_Fts(hangs=True)), "q")
    assert pipeline.retrieval_legs()["fts"] == "timeout"


def test_degraded_legs_names_only_what_broke():
    legs = {"fts": "ok", "vector": "timeout", "symbol": "ok", "embed": "error"}
    assert pipeline.degraded_legs(legs) == ["embed", "vector"]


@pytest.mark.asyncio
async def test_the_record_does_not_leak_between_questions():
    """A stale record would report a previous question's failure against this
    one, which is worse than reporting nothing."""
    await pipeline._safe_vector_search(_Ctx(store=None), "q")
    assert pipeline.retrieval_legs()["vector"] == "absent"
    pipeline.begin_leg_record()
    assert pipeline.retrieval_legs() == {}
