"""Unit tests for FullTextSearch.

All tests use an in-memory SQLite engine with the FTS5 virtual table
created by init_db().
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.search import FullTextSearch, SearchResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def fts(async_engine):
    """FullTextSearch instance tied to the in-memory engine."""
    fs = FullTextSearch(async_engine)
    await fs.ensure_index()
    return fs


# ---------------------------------------------------------------------------
# FTS5 table creation
# ---------------------------------------------------------------------------


async def test_fts5_table_created_after_ensure_index(async_engine):
    """The page_fts virtual table must exist after ensure_index()."""
    from sqlalchemy.sql import text

    fts = FullTextSearch(async_engine)
    await fts.ensure_index()

    async with async_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='page_fts'")
        )
        row = result.fetchone()
    assert row is not None, "page_fts virtual table was not created"


async def test_ensure_index_is_idempotent(async_engine):
    """Calling ensure_index() twice must not raise."""
    fts = FullTextSearch(async_engine)
    await fts.ensure_index()
    await fts.ensure_index()  # should not raise


# ---------------------------------------------------------------------------
# Search behaviour
# ---------------------------------------------------------------------------


async def test_full_text_search_exact_title_match(fts):
    await fts.index("p1", "Python Decorator Pattern", "This module explains decorators.")
    results = await fts.search("Decorator Pattern")
    assert len(results) >= 1
    assert results[0].page_id == "p1"


async def test_full_text_search_content_body_match(fts):
    await fts.index("p2", "Graph Builder", "The GraphBuilder class builds dependency graphs.")
    results = await fts.search("dependency graphs")
    assert len(results) >= 1
    assert results[0].page_id == "p2"


async def test_full_text_search_no_match_returns_empty(fts):
    await fts.index("p3", "File Traverser", "Handles gitignore-aware file traversal.")
    results = await fts.search("blockchain cryptocurrency")
    assert results == []


async def test_full_text_search_limit_respected(fts):
    for i in range(5):
        await fts.index(f"page{i}", f"repowise Module {i}", "Module documentation content here.")
    results = await fts.search("repowise Module", limit=3)
    assert len(results) <= 3


async def test_full_text_search_empty_query_returns_empty(fts):
    await fts.index("p", "Title", "Content")
    results = await fts.search("")
    assert results == []


async def test_full_text_search_score_is_positive(fts):
    """FTS5 rank is negative; FullTextSearch must negate it for SearchResult.score."""
    await fts.index("p", "Python async await", "Coroutines and event loops explained.")
    results = await fts.search("Python async")
    assert len(results) >= 1
    assert results[0].score > 0.0


async def test_full_text_search_snippet_truncated_to_200(fts):
    long_content = "word " * 200
    await fts.index("p", "Title", long_content)
    results = await fts.search("word")
    assert len(results) >= 1
    assert len(results[0].snippet) <= 200


async def test_full_text_search_returns_search_result_type(fts):
    await fts.index("p", "Symbol Spotlight", "High-pagerank symbol documentation.")
    results = await fts.search("Symbol Spotlight")
    assert len(results) >= 1
    assert isinstance(results[0], SearchResult)
    assert results[0].search_type == "fulltext"


async def test_full_text_search_index_update_reflects_new_content(fts):
    """Re-indexing a page with new content should make new terms searchable."""
    await fts.index("p1", "OldTitle", "nothing useful here")
    results_before = await fts.search("async decorators")
    count_before = len(results_before)

    await fts.index("p1", "NewTitle", "async decorators in Python are powerful")
    results_after = await fts.search("async decorators")
    assert len(results_after) > count_before


async def test_full_text_search_delete_removes_from_index(fts):
    await fts.index("to-remove", "Removable Page", "removable unique xylophone content")
    results_before = await fts.search("xylophone")
    assert len(results_before) >= 1

    await fts.delete("to-remove")
    results_after = await fts.search("xylophone")
    assert results_after == []


async def test_full_text_search_multiple_pages_relevance_ordered(fts):
    """Page with title match should rank higher than page with only content match."""
    await fts.index("exact", "Python Asyncio", "asyncio event loop management")
    await fts.index(
        "content-only", "Event Loop Guide", "Python asyncio is great for concurrent tasks"
    )
    results = await fts.search("Python Asyncio")
    # Both should appear; 'exact' should rank first (title match)
    assert len(results) >= 1
    assert results[0].page_id == "exact"


# ---------------------------------------------------------------------------
# Batch indexing
# ---------------------------------------------------------------------------


async def _indexed_ids(fts) -> set[str]:
    return await fts.list_indexed_ids()


async def test_index_many_writes_the_same_rows_as_indexing_one_at_a_time(async_engine):
    """The batch path is the single-page path, so the two must not diverge."""
    pages = [
        (f"p{i}", f"Title {i}", f"content about widgets number {i}", f"sum {i}", f"a/b{i}.py")
        for i in range(20)
    ]

    one_at_a_time = FullTextSearch(async_engine)
    await one_at_a_time.ensure_index()
    for page in pages:
        await one_at_a_time.index(page[0], page[1], page[2], summary=page[3], target_path=page[4])
    sequential = sorted(
        (r.page_id, r.title) for r in await one_at_a_time.search("widgets", limit=50)
    )

    await one_at_a_time.delete_many([p[0] for p in pages])
    await one_at_a_time.index_many(pages)
    batched = sorted((r.page_id, r.title) for r in await one_at_a_time.search("widgets", limit=50))

    assert batched == sequential
    assert len(batched) == 20


async def test_index_many_spans_more_ids_than_one_statement_can_bind(fts):
    """The delete half chunks; 1,200 ids is past both chunk boundaries.

    A wiki is thousands of pages, so the first real corpus would have been the
    first test of this path.
    """
    pages = [(f"p{i}", f"Title {i}", "a page about chunking", "", f"{i}.py") for i in range(1200)]

    await fts.index_many(pages)
    assert len(await _indexed_ids(fts)) == 1200

    # Re-indexing the same ids must replace rather than duplicate them.
    await fts.index_many(pages)
    assert len(await _indexed_ids(fts)) == 1200


async def test_a_page_id_repeated_in_one_batch_keeps_its_last_entry(fts):
    """Every id is deleted before any is inserted, so a duplicate would double."""
    await fts.index_many(
        [
            ("p1", "First", "a page about alpacas", "", "a.py"),
            ("p1", "Second", "a page about zebras", "", "a.py"),
        ]
    )

    assert await _indexed_ids(fts) == {"p1"}
    assert [r.page_id for r in await fts.search("zebras")] == ["p1"]
    assert await fts.search("alpacas") == []


async def test_index_many_of_nothing_is_a_no_op(fts):
    await fts.index_many([])
    assert await _indexed_ids(fts) == set()
