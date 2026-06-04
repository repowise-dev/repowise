"""Batched RAG prefetch for level-2 file pages.

The level-2 builder resolves RAG context for every tier-1 page in ONE
``search_many`` call before the level starts (one embedder round-trip
instead of one per page, outside the LLM semaphore). The per-page search
in ``_generate_file_page_from_ctx`` is skipped when prefetch succeeded and
must still run as the fallback when it didn't.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.generation.page_generator.levels import _prefetch_rag_context
from repowise.core.persistence.search import SearchResult
from repowise.core.persistence.vector_store import InMemoryVectorStore, VectorStore
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.core.providers.llm.mock import MockProvider

from .conftest import _make_file_info, _make_symbol

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(page_id: str) -> SearchResult:
    return SearchResult(
        page_id=page_id,
        title="t",
        page_type="file_page",
        target_path=page_id.split(":", 1)[-1],
        score=0.9,
        snippet=f"snippet for {page_id}",
        search_type="vector",
    )


class _SpyStore(VectorStore):
    """Counts search/search_many/list_page_ids calls; canned results."""

    def __init__(self, page_count: int = 50, fail_batch: bool = False) -> None:
        self.search_calls: list[str] = []
        self.search_many_calls: list[list[str]] = []
        self.page_count = page_count
        self.fail_batch = fail_batch

    async def embed_and_upsert(self, page_id, text, metadata) -> None:  # pragma: no cover
        pass

    async def search(self, query: str, limit: int = 10):
        self.search_calls.append(query)
        return [_result("file_page:other/file.py")]

    async def search_many(self, queries, limit: int = 10):
        if self.fail_batch:
            raise RuntimeError("batch search down")
        self.search_many_calls.append(list(queries))
        return [[_result("file_page:other/file.py")] for _ in queries]

    async def delete(self, page_id) -> None:  # pragma: no cover
        pass

    async def close(self) -> None:  # pragma: no cover
        pass

    async def list_page_ids(self) -> set[str]:
        return {f"p{i}" for i in range(self.page_count)}


class _CountingEmbedder(MockEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.embed_calls: list[list[str]] = []

    async def embed(self, texts):
        self.embed_calls.append(list(texts))
        return await super().embed(texts)


def _parsed(path: str = "pkg/mod.py", exports: list[str] | None = None):
    from repowise.core.ingestion.models import ParsedFile

    return ParsedFile(
        file_info=_make_file_info(path=path),
        symbols=[_make_symbol(name="Thing", file_path=path)],
        imports=[],
        exports=exports if exports is not None else ["Thing"],
        docstring="mod",
        parse_errors=[],
        content_hash="h",
    )


def _ctx_for(parsed):
    config = GenerationConfig()
    assembler = ContextAssembler(config)
    import networkx as nx

    g = nx.DiGraph()
    g.add_node(parsed.file_info.path)
    return assembler.assemble_file_page(parsed, g, {}, {}, {}, b"x = 1\n")


def _run_obj(store, **config_overrides):
    config = GenerationConfig(**config_overrides)
    return SimpleNamespace(vector_store=store, config=config)


# ---------------------------------------------------------------------------
# VectorStore.search_many
# ---------------------------------------------------------------------------


async def test_search_many_default_aligns_with_per_query_search() -> None:
    embedder = _CountingEmbedder()
    store = InMemoryVectorStore(embedder)
    await store.embed_batch(
        [
            ("p1", "alpha beta", {"target_path": "a.py", "content": "alpha"}),
            ("p2", "gamma delta", {"target_path": "b.py", "content": "gamma"}),
        ]
    )

    singles = [await store.search(q, limit=2) for q in ("alpha", "gamma")]
    batched = await store.search_many(["alpha", "gamma"], limit=2)

    assert [[r.page_id for r in rs] for rs in batched] == [
        [r.page_id for r in rs] for rs in singles
    ]


async def test_in_memory_search_many_embeds_once() -> None:
    embedder = _CountingEmbedder()
    store = InMemoryVectorStore(embedder)
    await store.embed_batch([("p1", "alpha", {"target_path": "a.py"})])
    embedder.embed_calls.clear()

    await store.search_many(["q1", "q2", "q3"], limit=2)

    assert len(embedder.embed_calls) == 1
    assert embedder.embed_calls[0] == ["q1", "q2", "q3"]


async def test_search_many_empty_queries() -> None:
    store = InMemoryVectorStore(MockEmbedder())
    assert await store.search_many([]) == []


# ---------------------------------------------------------------------------
# _prefetch_rag_context
# ---------------------------------------------------------------------------


async def test_prefetch_populates_rag_context_with_self_exclusion() -> None:
    store = _SpyStore()
    run = _run_obj(store)
    parsed = _parsed("other/file.py")  # search result IS this page → excluded
    ctx = _ctx_for(parsed)
    parsed2 = _parsed("pkg/two.py")
    ctx2 = _ctx_for(parsed2)

    ok = await _prefetch_rag_context(run, [(parsed, ctx), (parsed2, ctx2)])

    assert ok is True
    assert store.search_many_calls == [["Thing", "Thing"]]
    assert ctx.rag_context == []  # self-hit filtered out
    assert ctx2.rag_context == ["[file_page:other/file.py]\nsnippet for file_page:other/file.py"]


async def test_prefetch_skips_when_store_below_min_size() -> None:
    store = _SpyStore(page_count=3)
    run = _run_obj(store, rag_min_store_size=10)
    parsed = _parsed()
    ctx = _ctx_for(parsed)

    ok = await _prefetch_rag_context(run, [(parsed, ctx)])

    assert ok is True  # per-page gate would skip every search too
    assert store.search_many_calls == []
    assert ctx.rag_context == []


async def test_prefetch_returns_false_without_store_or_on_batch_failure() -> None:
    parsed = _parsed()
    ctx = _ctx_for(parsed)

    assert await _prefetch_rag_context(_run_obj(None), [(parsed, ctx)]) is False

    failing = _SpyStore(fail_batch=True)
    assert await _prefetch_rag_context(_run_obj(failing), [(parsed, ctx)]) is False
    assert ctx.rag_context == []


async def test_prefetch_disabled_flag_returns_false() -> None:
    store = _SpyStore()
    run = _run_obj(store, enable_rag_context=False)
    parsed = _parsed()
    ctx = _ctx_for(parsed)
    assert await _prefetch_rag_context(run, [(parsed, ctx)]) is False
    assert store.search_many_calls == []


# ---------------------------------------------------------------------------
# Per-page fallback in _generate_file_page_from_ctx
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rag_prefetched", "expected_searches"),
    [(True, 0), (False, 1)],
)
async def test_per_page_search_skipped_iff_prefetched(
    rag_prefetched: bool, expected_searches: int
) -> None:
    store = _SpyStore()
    config = GenerationConfig()
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config, vector_store=store)
    parsed = _parsed()
    ctx = _ctx_for(parsed)

    page = await gen._generate_file_page_from_ctx(parsed, ctx, rag_prefetched=rag_prefetched)

    assert page.page_type == "file_page"
    assert len(store.search_calls) == expected_searches
