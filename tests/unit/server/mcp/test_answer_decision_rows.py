"""Decision vectors are not page-search candidates, and a hit with no page is not a hit.

Decision records are embedded into the same vector store as wiki pages, under a
``decision:`` page-id namespace. That is deliberate — it is how dedup finds a
paraphrase and how a "why Redis?" search surfaces the decision itself. What was
not deliberate is that ``get_answer``'s page retrieval had no way to say "pages
only", so decision rows competed for its fixed fetch window.

Those rows are worse than irrelevant. They have no ``wiki_pages`` row, so
hydration gives them no ``target_path`` — and with no path the tombstone check
cannot fire, the ``scope`` filter is skipped, and noise demotion can only
reorder them, never drop them. A decision row could hold a top-5 slot, reach
the synthesis prompt, and produce no citation for the answer built on it.

Decisions still reach a why-shaped answer: they come from a path-overlap SQL
query over ``decision_records``, not from vector search, which the last test
here pins.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from repowise.core.analysis.decisions.semantic_match import (
    DECISION_PAGE_TYPE,
    DECISION_VECTOR_PREFIX,
)
from repowise.core.persistence.search import SearchResult
from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.server.mcp_server import _answer_pipeline as pipeline

_QUESTION = "how does the answer cache decide to invalidate an entry"


def _decision_row(i: int) -> SearchResult:
    return SearchResult(
        page_id=f"{DECISION_VECTOR_PREFIX}dec{i}",
        title=f"Decision {i}",
        page_type=DECISION_PAGE_TYPE,
        target_path="",  # decision vectors have no page row, hence no path
        score=0.9,
        snippet="",
        search_type="vector",
    )


def _page_row(i: int) -> SearchResult:
    return SearchResult(
        page_id=f"file_page:pkg/alpha/{i}.py",
        title=f"File: pkg/alpha/{i}.py",
        page_type="file_page",
        target_path=f"pkg/alpha/{i}.py",
        score=0.5,
        snippet="",
        search_type="vector",
    )


class _ScriptedStore:
    """Vector store that returns *n_decisions* decision rows ahead of pages.

    Scripted rather than embedded because the point is the ordering: decision
    rows outranking real pages is the case that emptied the window, and no
    fixture embedder reproduces it on demand.
    """

    def __init__(self, n_decisions: int, n_pages: int = 40) -> None:
        self._rows = [_decision_row(i) for i in range(n_decisions)] + [
            _page_row(i) for i in range(n_pages)
        ]
        self.limits: list[int] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def search_by_vector(self, vector, limit: int = 10) -> list[SearchResult]:
        self.limits.append(limit)
        return self._rows[:limit]

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        self.limits.append(limit)
        return self._rows[:limit]


@pytest.fixture(autouse=True)
def _clear_vector_cache():
    cache = getattr(pipeline, "_QUESTION_VECTORS", None)
    if cache is not None:
        cache.clear()
    yield
    if cache is not None:
        cache.clear()


async def test_page_retrieval_returns_no_decision_rows():
    store = _ScriptedStore(n_decisions=5)
    ctx = SimpleNamespace(fts=None, vector_store=store)

    hits = await pipeline.hybrid_retrieve(_QUESTION, ctx)

    assert hits, "the fetch must still return pages"
    assert not [h for h in hits if h["page_id"].startswith(DECISION_VECTOR_PREFIX)]


async def test_dropped_decision_rows_are_counted(caplog):
    """Silently dropping them would hide both a regression here and a store
    filling up with decisions."""
    store = _ScriptedStore(n_decisions=5)
    ctx = SimpleNamespace(fts=None, vector_store=store)

    with caplog.at_level(logging.DEBUG, logger="repowise.mcp.answer"):
        await pipeline.hybrid_retrieve(_QUESTION, ctx)

    assert any(
        "5" in r.getMessage() and "decision" in r.getMessage().lower() for r in caplog.records
    ), caplog.text


async def test_the_window_still_fills_with_real_pages_when_decisions_crowd_it():
    """The regression this must not become: filtering after a fixed-size fetch
    would return 15 minus however many decisions happened to rank high."""
    store = _ScriptedStore(n_decisions=pipeline._RETRIEVAL_FETCH_LIMIT)
    ctx = SimpleNamespace(fts=None, vector_store=store)

    hits = await pipeline.hybrid_retrieve(_QUESTION, ctx)

    assert len(hits) == pipeline._RETRIEVAL_FETCH_LIMIT
    assert store.limits[0] > pipeline._RETRIEVAL_FETCH_LIMIT, (
        "the fetch must ask for more than it needs, or crowding empties the window"
    )


async def test_a_window_that_stays_short_is_reported(caplog):
    """More decisions than the over-fetch covers is a store shape worth seeing,
    not a quietly thin candidate set."""
    store = _ScriptedStore(n_decisions=500, n_pages=0)
    ctx = SimpleNamespace(fts=None, vector_store=store)

    with caplog.at_level(logging.DEBUG, logger="repowise.mcp.answer"):
        hits = await pipeline.hybrid_retrieve(_QUESTION, ctx)

    assert hits == []
    assert any("decision" in r.getMessage().lower() for r in caplog.records)


async def test_hydration_drops_a_hit_with_no_page_row_and_counts_it(factory, populated_db, caplog):
    """Any pathless hit, whatever its source: with no page row behind it the
    answer cannot cite it and the tombstone check never runs."""
    ctx = SimpleNamespace(session_factory=factory)
    hits = [
        {"page_id": "repo_overview:test-repo", "score": 1.0, "_sources": {"fts"}},
        {"page_id": "file_page:this/page/does/not/exist.py", "score": 0.9, "_sources": {"fts"}},
    ]

    with caplog.at_level(logging.DEBUG, logger="repowise.mcp.answer"):
        out = await pipeline.hydrate_hits(hits, ctx)

    assert [h["page_id"] for h in out] == ["repo_overview:test-repo"]
    assert any("no page row" in r.getMessage().lower() for r in caplog.records), caplog.text


async def test_decision_dedup_still_finds_decision_vectors():
    """The filter belongs to page retrieval alone — dedup queries the same store
    for exactly these rows and must keep getting them."""
    from repowise.core.analysis.decisions.semantic_match import (
        SEARCH_FETCH,
        nearest_decision_hit,
        upsert_decision_vector,
    )

    store = InMemoryVectorStore(embedder=MockEmbedder())
    await upsert_decision_vector(store, "dec1", title="Cache answers by question hash")

    results = await store.search("Cache answers by question hash", limit=SEARCH_FETCH)
    hit = nearest_decision_hit(results)

    assert hit is not None and hit[0] == "dec1"


async def test_why_answers_read_decisions_from_the_database_not_the_vector_store(
    factory, populated_db
):
    """What makes dropping decision vectors safe: the decisions a why-shaped
    answer injects come from a path-overlap query, with no vector store in play."""
    from repowise.server.mcp_server._answer_context import fetch_relevant_decisions

    ctx = SimpleNamespace(session_factory=factory, vector_store=None)

    decisions = await fetch_relevant_decisions(ctx, populated_db, ["src/auth/service.py"])

    assert decisions, "a why answer must still be able to reach its decisions"
