"""Long pages are embedded whole, as pieces, instead of losing their tail.

``EMBED_TEXT_MAX_CHARS`` used to cut every page at the cap, so a fact past it
could not be found by meaning at all (#3302). A page over the cap is now split
into pieces that each fit one input, stored as ``(page_id, chunk_index)`` rows,
and scored at search time by its best piece. Its vectors are current only when
every piece was written: a failed or interrupted write keeps the previous ones.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.vector_store._base import (
    EMBED_BATCH_MAX_ITEMS,
    EMBED_TEXT_MAX_CHARS,
    BatchEmbeddingError,
    cap_embed_text,
    cosine_similarity,
    iter_page_chunk_groups,
    split_embed_text,
)
from repowise.core.persistence.vector_store.in_memory import InMemoryVectorStore
from repowise.core.persistence.vector_store.lancedb_store import LanceDBVectorStore

VOCAB = ("operations", "handshake", "replay", "cursor", "kanban", "summary", "routes")


class KeywordEmbedder:
    """Counts vocabulary words, plus a constant so no vector is zero.

    Deterministic and semantic enough to tell which part of a page a vector
    saw, which is all these tests need to know.
    """

    dimensions = len(VOCAB) + 1

    def __init__(self, fail_on_call: int | None = None) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call

    async def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        del kind
        self.calls += 1
        if self.fail_on_call is not None and self.calls == self.fail_on_call:
            raise TimeoutError("provider timed out")
        return [self.vector(t) for t in texts]

    @staticmethod
    def vector(text: str) -> list[float]:
        words = text.lower().split()
        return [float(words.count(w)) for w in VOCAB] + [0.01]


def _filler(chars: int) -> str:
    line = "lorem ipsum dolor sit amet consectetur\n"
    return (line * (chars // len(line) + 1))[:chars]


def _long_page() -> str:
    """Like the Kanban plugin API page in #3302: the opening lists the
    operations, and the reconnect behaviour is described far past the cap."""
    head = "kanban operations summary routes\n"
    tail = "\nhandshake cursor replay handshake cursor replay\n"
    return head + _filler(EMBED_TEXT_MAX_CHARS * 2) + tail


def _meta(title: str) -> dict:
    return {"title": title, "page_type": "api_contract", "target_path": f"{title}.py"}


@pytest.fixture(params=["memory", "lancedb"])
def make_store(request, tmp_path):
    def make(embedder):
        if request.param == "memory":
            return InMemoryVectorStore(embedder)
        return LanceDBVectorStore(str(tmp_path / "lancedb"), embedder)

    return make


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------


def test_a_page_that_fits_is_one_piece():
    assert split_embed_text("p", "short body") == ["short body"]
    exact = "x" * EMBED_TEXT_MAX_CHARS
    assert split_embed_text("p", exact) == [exact]


def test_a_long_page_splits_losslessly_into_pieces_that_fit():
    text = _long_page()
    pieces = split_embed_text("p", text)

    assert len(pieces) == 3
    assert "".join(pieces) == text
    assert all(len(p) <= EMBED_TEXT_MAX_CHARS for p in pieces)
    # Cut at a line break, so no piece starts mid-line.
    assert all(p.endswith("\n") for p in pieces[:-1])


def test_a_long_line_with_no_break_is_cut_at_the_cap():
    text = "x" * (EMBED_TEXT_MAX_CHARS + 10)
    assert [len(p) for p in split_embed_text("p", text)] == [EMBED_TEXT_MAX_CHARS, 10]


def test_groups_never_split_a_page_and_fill_about_one_request():
    long_text = "x" * (EMBED_TEXT_MAX_CHARS * 3)  # three pieces
    items = [(f"p{i}", long_text, {}) for i in range(6)]

    groups = list(iter_page_chunk_groups(items))

    assert [[item[0] for item, _ in g] for g in groups] == [
        ["p0", "p1", "p2", "p3", "p4"],
        ["p5"],
    ]
    assert all(sum(len(pieces) for _, pieces in g) <= EMBED_BATCH_MAX_ITEMS for g in groups)


def test_a_page_with_more_pieces_than_one_request_travels_alone():
    huge = "x" * (EMBED_TEXT_MAX_CHARS * (EMBED_BATCH_MAX_ITEMS + 2))
    groups = list(iter_page_chunk_groups([("small", "a", {}), ("huge", huge, {})]))

    assert [[item[0] for item, _ in g] for g in groups] == [["small"], ["huge"]]
    assert len(groups[1][0][1]) == EMBED_BATCH_MAX_ITEMS + 2


# ---------------------------------------------------------------------------
# Storage, scoring and coverage (both chunking stores)
# ---------------------------------------------------------------------------


async def test_a_page_that_fits_is_stored_as_one_vector(make_store):
    store = make_store(KeywordEmbedder())
    await store.embed_batch([("short", "kanban operations", _meta("short"))])

    coverage = await store.vector_coverage()
    assert (coverage.pages, coverage.chunked_pages, coverage.incomplete) == (1, 0, frozenset())


async def test_a_long_page_is_stored_whole_and_found_by_its_tail(make_store):
    embedder = KeywordEmbedder()
    store = make_store(embedder)
    await store.embed_batch(
        [
            ("long", _long_page(), _meta("long")),
            ("other", "summary routes " * 5, _meta("other")),
        ]
    )

    coverage = await store.vector_coverage()
    assert (coverage.pages, coverage.chunked_pages, coverage.incomplete) == (2, 1, frozenset())

    tail_hits = await store.search("handshake replay cursor", limit=2)
    assert [r.page_id for r in tail_hits] == ["long", "other"]  # one result per page
    assert "handshake" in tail_hits[0].snippet  # evidence comes from the piece that matched

    head_hits = await store.search("kanban operations", limit=1)
    assert [r.page_id for r in head_hits] == ["long"]


async def test_before_and_after_for_queries_on_the_beginning_and_end_of_a_long_page():
    """The comparison asked for on #3302, on one long page.

    Before: the one vector embeds the capped prefix. After: the page scores as
    its best piece. A query on the opening scores the same either way; a query
    on the tail scores nothing before and fully after.
    """
    text = _long_page()
    head_q = KeywordEmbedder.vector("kanban operations")
    tail_q = KeywordEmbedder.vector("handshake replay cursor")

    before = KeywordEmbedder.vector(cap_embed_text("long", text))
    after = [KeywordEmbedder.vector(p) for p in split_embed_text("long", text)]

    def best(q):
        return max(cosine_similarity(q, v) for v in after)

    assert best(head_q) == pytest.approx(cosine_similarity(head_q, before))
    assert cosine_similarity(tail_q, before) < 0.01
    assert best(tail_q) > 0.9


async def test_search_still_returns_limit_pages_when_long_pages_crowd_the_top(make_store):
    store = make_store(KeywordEmbedder())
    # Every piece of these mentions the query term, so they hold the top rows.
    crowded = ("replay\n" + _filler(EMBED_TEXT_MAX_CHARS - 10) + "\n") * 6
    await store.embed_batch(
        [
            ("crowded", crowded, _meta("crowded")),
            *[(f"p{i}", f"routes {i}", _meta(f"p{i}")) for i in range(3)],
        ]
    )

    hits = await store.search("replay", limit=3)
    assert len(hits) == 3
    assert len({h.page_id for h in hits}) == 3
    assert hits[0].page_id == "crowded"


async def test_an_interrupted_write_keeps_the_previous_vectors(make_store):
    store = make_store(KeywordEmbedder())
    await store.embed_batch([("long", _long_page(), _meta("long"))])
    before = await store.search("handshake replay", limit=1)

    # The rewrite spans two requests and the second one fails.
    huge = "kanban\n" + _filler(EMBED_TEXT_MAX_CHARS * (EMBED_BATCH_MAX_ITEMS + 1))
    store._embedder = KeywordEmbedder(fail_on_call=2)
    # The in-memory store raises the provider's error as it always has; the
    # persistent store reports which pages failed.
    with pytest.raises((BatchEmbeddingError, TimeoutError)) as excinfo:
        await store.embed_batch([("long", huge, _meta("long"))])

    if isinstance(excinfo.value, BatchEmbeddingError):
        assert [item[0] for item in excinfo.value.failed_items] == ["long"]
    after = await store.search("handshake replay", limit=1)
    assert [(r.page_id, r.score) for r in after] == [(r.page_id, r.score) for r in before]
    coverage = await store.vector_coverage()
    assert (coverage.chunked_pages, coverage.incomplete) == (1, frozenset())


async def test_a_page_that_shrinks_drops_its_surplus_pieces(make_store):
    store = make_store(KeywordEmbedder())
    await store.embed_batch([("long", _long_page(), _meta("long"))])
    await store.embed_and_upsert("long", "kanban summary", _meta("long"))

    coverage = await store.vector_coverage()
    assert (coverage.pages, coverage.chunked_pages, coverage.incomplete) == (1, 0, frozenset())
    hits = await store.search("handshake replay", limit=5)
    assert [h.page_id for h in hits] == ["long"]
    assert "handshake" not in hits[0].snippet  # the old tail is gone


async def test_summary_lookup_reads_the_opening_piece(make_store):
    store = make_store(KeywordEmbedder())
    await store.embed_batch([("long", _long_page(), {**_meta("long"), "content": "OPENER"})])

    summary = await store.get_page_summary_by_path("long.py")
    assert summary is not None and summary["summary"].startswith("OPENER")
    summaries = await store.get_page_summaries_by_paths(["long.py"])
    assert summaries["long.py"]["summary"].startswith("OPENER")


# ---------------------------------------------------------------------------
# LanceDB: a table written before chunking
# ---------------------------------------------------------------------------


async def test_a_table_written_before_chunking_is_read_and_then_upgraded(tmp_path):
    import lancedb
    import pyarrow as pa

    path = str(tmp_path / "lancedb")
    dim = KeywordEmbedder.dimensions
    db = await lancedb.connect_async(path)
    legacy = pa.schema(
        [
            pa.field("page_id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dim)),
            pa.field("title", pa.string()),
            pa.field("page_type", pa.string()),
            pa.field("target_path", pa.string()),
            pa.field("content_snippet", pa.string()),
        ]
    )
    table = await db.create_table("wiki_pages", schema=legacy)
    await table.add(
        [
            {
                "page_id": "old",
                "vector": KeywordEmbedder.vector("kanban"),
                "title": "old",
                "page_type": "file_page",
                "target_path": "old.py",
                "content_snippet": "old opener",
            }
        ]
    )

    store = LanceDBVectorStore(path, KeywordEmbedder())
    # Reads work before anything is written.
    assert [r.page_id for r in await store.search("kanban", limit=1)] == ["old"]
    assert (await store.get_page_summary_by_path("old.py"))["summary"] == "old opener"
    assert (await store.vector_coverage()).pages == 1

    await store.embed_batch([("long", _long_page(), _meta("long"))])

    coverage = await store.vector_coverage()
    assert (coverage.pages, coverage.chunked_pages, coverage.incomplete) == (2, 1, frozenset())
    assert (await store.get_page_summary_by_path("old.py"))["summary"] == "old opener"
