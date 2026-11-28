"""The first semantic search of a server process keeps its vector hits.

A fresh server loads its vector store in the background. A search that starts
mid-load used to hold the keyless placeholder and drop the vector leg with no
flag; one that starts after load paid the first embed's client setup inside
the semantic grace and was flagged degraded.
"""

from __future__ import annotations

import asyncio

import pytest

from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import (
    KeylessEmbedder,
    MockEmbedder,
    warm_query_embedder,
)
from repowise.server.mcp_server import _state, tool_search
from repowise.server.mcp_server._answer_pipeline.leg_record import (
    begin_leg_record,
    retrieval_legs,
)
from repowise.server.mcp_server._helpers import (
    _SEMANTIC_GRACE_ENV,
    _resolve_repo_context,
    wait_for_vector_store,
)


@pytest.fixture
def loading_server(monkeypatch, tmp_path):
    """Single-repo ``_state`` as the lifespan leaves it before the store loads."""
    placeholder = InMemoryVectorStore(embedder=KeylessEmbedder())
    monkeypatch.setattr(_state, "_registry", None)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    monkeypatch.setattr(_state, "_vector_store", placeholder)
    monkeypatch.setattr(_state, "_decision_store", placeholder)
    monkeypatch.setattr(_state, "_vector_store_ready", None)
    return placeholder


def _finish_loading(store) -> None:
    _state._vector_store = store
    _state._decision_store = store
    _state._vector_store_ready.set()


def test_a_context_taken_mid_load_is_pointed_at_the_loaded_store(loading_server) -> None:
    loaded = InMemoryVectorStore(embedder=MockEmbedder())

    async def go():
        _state._vector_store_ready = asyncio.Event()
        ctx = await _resolve_repo_context(None)
        assert ctx.vector_store is loading_server
        asyncio.get_running_loop().call_later(0.05, _finish_loading, loaded)
        await wait_for_vector_store(ctx)
        return ctx

    ctx = asyncio.run(go())
    assert ctx.vector_store is loaded
    assert ctx.decision_store is loaded


def test_a_context_without_readiness_is_left_alone(loading_server) -> None:
    class _Ctx:
        vector_store_ready = None
        vector_store = "own"

    ctx = _Ctx()
    asyncio.run(wait_for_vector_store(ctx))
    assert ctx.vector_store == "own"


class _Hit:
    page_id = "file_page:a.py"
    title = "a.py"
    score = 1.0
    snippet = ""
    page_type = "file_page"
    target_path = "a.py"


def test_the_grace_starts_once_the_store_has_loaded(loading_server, monkeypatch) -> None:
    """Lexical legs finish long before the store loads; the vector hits still count."""
    loaded = InMemoryVectorStore(embedder=MockEmbedder())

    async def vector(ctx, query, limit):
        return [_Hit()] if ctx.vector_store is loaded else []

    async def nothing(*args, **kwargs):
        return []

    monkeypatch.setenv(_SEMANTIC_GRACE_ENV, "0.05")
    monkeypatch.setattr(tool_search, "_safe_vector", vector)
    monkeypatch.setattr(tool_search, "_safe_fts", nothing)
    monkeypatch.setattr(tool_search, "_safe_symbol_search", nothing)
    monkeypatch.setattr(tool_search, "_safe_filename_search", nothing)

    async def go():
        begin_leg_record()
        _state._vector_store_ready = asyncio.Event()
        ctx = await _resolve_repo_context(None)
        asyncio.get_running_loop().call_later(0.3, _finish_loading, loaded)
        out = await tool_search._fused_retrieve(ctx, "q", 5, None)
        return out, retrieval_legs()

    out, legs = asyncio.run(go())
    assert [(r["page_id"], r["sources"]) for r in out] == [("file_page:a.py", ["vector"])]
    assert legs == {}


class _Store:
    _embedder = MockEmbedder()

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def search(self, query, limit=10):
        raise self._exc


@pytest.mark.parametrize(
    ("exc", "outcome", "timeout_env"),
    [(RuntimeError("401"), "error", None), (TimeoutError(), "timeout", "0.01")],
)
def test_a_dropped_vector_leg_is_recorded(monkeypatch, exc, outcome, timeout_env) -> None:
    if timeout_env:
        monkeypatch.setenv("REPOWISE_VECTOR_SEARCH_TIMEOUT_S", timeout_env)

    class _Ctx:
        vector_store = _Store(exc)

    async def go():
        begin_leg_record()
        hits = await tool_search._safe_vector(_Ctx(), "q", 5)
        return hits, retrieval_legs()

    hits, legs = asyncio.run(go())
    assert hits == []
    assert legs == {"vector": outcome}


class _CountingEmbedder(MockEmbedder):
    def __init__(self, fail: bool = False) -> None:
        super().__init__()
        self.calls: list[str] = []
        self._fail = fail

    async def embed(self, texts, *, kind="document"):
        self.calls.append(kind)
        if self._fail:
            raise RuntimeError("401")
        return await super().embed(texts, kind=kind)


def test_warm_up_embeds_once() -> None:
    embedder = _CountingEmbedder()
    asyncio.run(warm_query_embedder(embedder))
    assert embedder.calls == ["document"]


def test_a_failed_warm_up_leaves_query_embeds_on() -> None:
    """A start-up blip must not pause the first searches behind the cool-down."""
    from repowise.core.providers.embedding.caching import CachingEmbedder

    inner = _CountingEmbedder(fail=True)
    embedder = CachingEmbedder(inner)
    asyncio.run(warm_query_embedder(embedder))  # never raises
    inner._fail = False
    asyncio.run(embedder.embed(["q"], kind="query"))
    assert inner.calls == ["document", "query"]


def test_warm_up_skips_a_keyless_or_missing_embedder() -> None:
    asyncio.run(warm_query_embedder(None))
    asyncio.run(warm_query_embedder(KeylessEmbedder()))


def test_the_store_is_ready_only_after_the_warm_up(loading_server, tmp_path, monkeypatch) -> None:
    pytest.importorskip("lancedb")  # imported up front so the warm-up is the slow step
    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.server.mcp_server import _server

    order: list[str] = []

    async def connected(self):
        order.append("connect")

    async def warm(embedder):
        await asyncio.sleep(0.3)
        order.append(f"warm:{type(embedder).__name__}")

    monkeypatch.setattr(LanceDBVectorStore, "_ensure_connected", connected)
    monkeypatch.setattr(_server, "warm_query_embedder", warm)
    monkeypatch.setattr(_server, "_query_embedder", MockEmbedder)
    monkeypatch.setattr(_state, "_vector_store_errors", {}, raising=False)
    (tmp_path / ".repowise" / "lancedb").mkdir(parents=True)

    async def go():
        _state._vector_store_ready = asyncio.Event()
        await _server._load_vector_stores(str(tmp_path))
        return _state._vector_store_ready.is_set()

    assert asyncio.run(go())
    assert order == ["connect", "warm:MockEmbedder"]
    assert isinstance(_state._vector_store, LanceDBVectorStore)
