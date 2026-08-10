"""A keyless index must not rank on its own vectors.

``MockEmbedder``'s components are every one non-negative, so all of its vectors
live in the positive orthant of an 8-dimensional space and no two of them can
point meaningfully different ways: two *unrelated* strings score ~0.75 cosine on
average and never below ~0.21. They are distinct, which is what its test callers
need, and not discriminative, which is what a retriever needs.

Every documented description of the keyless mode says it is full-text-only
(``docs/reference/CONFIG.md``, ``ui/mode_selection.py``, ``init_cmd/command.py``,
``README.md``). These tests pin that.
"""

from __future__ import annotations

import asyncio
import math
import statistics
from typing import ClassVar

import pytest

from repowise.core.analysis.decisions import semantic_match
from repowise.core.persistence.vector_store.in_memory import InMemoryVectorStore
from repowise.core.providers.embedding.base import (
    KeylessEmbedder,
    MockEmbedder,
    is_semantic_embedder,
    store_has_semantic_vectors,
)
from repowise.server.mcp_server import _answer_pipeline as pipeline
from repowise.server.mcp_server import tool_search


class _RealisticEmbedder:
    """Stand-in for a real embedder: distinct texts get distinct directions."""

    dimensions = 4

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            raw = [float(text.count(c)) for c in "abcd"]
            norm = math.sqrt(sum(x * x for x in raw)) or 1.0
            out.append([x / norm for x in raw])
        return out


class _Ctx:
    def __init__(self, store, fts=None):
        self.vector_store = store
        self.fts = fts
        self.vector_store_ready = None


# --------------------------------------------------------------------------
# The property that motivates the whole change
# --------------------------------------------------------------------------


def test_mock_vectors_are_not_discriminative():
    """Unrelated texts are near-identical under the mock, so ranking is noise."""
    embedder = KeylessEmbedder()
    vectors = asyncio.run(embedder.embed([f"unrelated-document-{i}" for i in range(200)]))
    pairs = [
        sum(x * y for x, y in zip(vectors[i], vectors[j], strict=True))
        for i in range(len(vectors))
        for j in range(i + 1, len(vectors))
    ]
    # Two texts with nothing in common should not look alike. Under the mock
    # they do, which is why the vector leg has to be skipped rather than
    # thresholded: there is no cutoff that separates signal from noise.
    assert statistics.mean(pairs) > 0.6
    assert min(pairs) > 0.15


# --------------------------------------------------------------------------
# The predicate
# --------------------------------------------------------------------------


def test_predicate_rejects_keyless_and_accepts_real():
    assert is_semantic_embedder(_RealisticEmbedder()) is True
    assert is_semantic_embedder(KeylessEmbedder()) is False


def test_predicate_accepts_the_bare_test_double():
    """MockEmbedder stands in for a real embedder in the suite and must rank.

    The two classes share their arithmetic, so only the type separates them.
    If this ever flips, ~25 existing tests lose their vector leg silently.
    """
    assert is_semantic_embedder(MockEmbedder()) is True
    assert isinstance(KeylessEmbedder(), MockEmbedder), "width checks rely on this"


def test_predicate_reads_the_embedder_off_the_store():
    assert store_has_semantic_vectors(InMemoryVectorStore(embedder=_RealisticEmbedder())) is True
    assert store_has_semantic_vectors(InMemoryVectorStore(embedder=KeylessEmbedder())) is False


def test_predicate_fails_open_on_a_store_it_cannot_inspect():
    """Only positively-identified keyless stores are refused.

    Failing closed would silently disable semantic search for any store holding
    its embedder somewhere else, including out-of-tree ones. It also breaks the
    several test doubles in this suite that carry no ``_embedder`` at all.
    """
    assert store_has_semantic_vectors(object()) is True
    assert is_semantic_embedder(None) is True
    # None means "no store", which is a different thing from "cannot rank".
    assert store_has_semantic_vectors(None) is False


# --------------------------------------------------------------------------
# search_codebase
# --------------------------------------------------------------------------


def _seed(store, n=20):
    async def go():
        for i in range(n):
            await store.embed_and_upsert(
                f"page-{i}",
                f"page {i} about an entirely unrelated subject",
                {"title": f"Page {i}", "page_type": "file_page"},
            )

    asyncio.run(go())
    return store


def test_search_vector_leg_is_silent_on_a_keyless_store():
    """The regression test. Fails on unfixed code, which returns a full window."""
    store = _seed(InMemoryVectorStore(embedder=KeylessEmbedder()))
    hits = asyncio.run(tool_search._safe_vector(_Ctx(store), "a totally unrelated query", 5))
    assert hits == []


def test_search_vector_leg_still_runs_on_a_real_store():
    store = _seed(InMemoryVectorStore(embedder=_RealisticEmbedder()))
    hits = asyncio.run(tool_search._safe_vector(_Ctx(store), "aaa bbb", 5))
    assert hits, "a real embedder must still retrieve"


def test_fused_retrieve_returns_nothing_when_keyless_and_fts_misses():
    """Keyless + no lexical match must mean no results, not nearest neighbours."""

    class _EmptyFts:
        async def search(self, query, limit=10):
            return []

    store = _seed(InMemoryVectorStore(embedder=KeylessEmbedder()))
    out = asyncio.run(
        tool_search._fused_retrieve(_Ctx(store, _EmptyFts()), "unrelated query", 15, None)
    )
    assert out == []


# --------------------------------------------------------------------------
# get_answer
# --------------------------------------------------------------------------


def test_answer_vector_leg_is_silent_and_says_why():
    store = _seed(InMemoryVectorStore(embedder=KeylessEmbedder()))
    pipeline.begin_leg_record()
    hits = asyncio.run(pipeline._safe_vector_search(_Ctx(store), "an unrelated question"))
    assert hits == []
    assert pipeline.retrieval_legs()["vector"] == "keyless"


def test_keyless_is_reported_but_not_as_a_failure():
    """A permanent configuration is not a leg that fell over."""
    assert pipeline.degraded_legs({"fts": "ok", "vector": "keyless"}) == []
    assert pipeline.degraded_legs({"fts": "ok", "vector": "timeout"}) == ["vector"]


# --------------------------------------------------------------------------
# Decision dedup
# --------------------------------------------------------------------------


@pytest.mark.parametrize("n_existing", [10, 60])
def test_dedup_refuses_rather_than_guessing_on_a_keyless_store(n_existing):
    """Unfixed, this merges an unrelated decision ~100% of the time at n=60."""
    store = InMemoryVectorStore(embedder=KeylessEmbedder())

    async def go():
        for i in range(n_existing):
            await store.embed_and_upsert(
                f"{semantic_match.DECISION_VECTOR_PREFIX}d{i}",
                f"decision {i} on an entirely separate topic",
                {"title": f"Decision {i}"},
            )
        return await semantic_match.find_duplicate_decision(
            store, title="A brand new and unrelated decision"
        )

    assert asyncio.run(go()) is None


def test_related_decisions_are_empty_on_a_keyless_store():
    store = InMemoryVectorStore(embedder=KeylessEmbedder())

    async def go():
        for i in range(30):
            await store.embed_and_upsert(
                f"{semantic_match.DECISION_VECTOR_PREFIX}d{i}",
                f"decision {i}",
                {"title": f"Decision {i}"},
            )
        single = await semantic_match.find_related_decisions(store, title="Unrelated", lo=0.5)
        many = await semantic_match.find_related_decisions_many(
            store, [("Unrelated", "", set()), ("Also unrelated", "", set())], lo=0.5
        )
        return single, many

    single, many = asyncio.run(go())
    assert single == []
    assert many == [[], []]


def test_dedup_by_vector_ignores_the_pending_index_too():
    """Pending vectors come from the same embedder, so they are equally blind."""
    store = InMemoryVectorStore(embedder=KeylessEmbedder())
    vector = asyncio.run(KeylessEmbedder().embed(["a new decision"]))[0]

    class _Pending:
        ids: ClassVar[set[str]] = {"decision:other"}

        def best(self, _v):
            return ("decision:other", 0.99)

    got = asyncio.run(
        semantic_match.find_duplicate_decision_by_vector(store, vector, pending=_Pending())
    )
    assert got is None


# --------------------------------------------------------------------------
# `repowise search --mode semantic` (CLI)
# --------------------------------------------------------------------------
#
# The two CLI search paths were never wired to the predicate at all. Everything
# else that reads vectors was swept and guarded; these build their own store
# inline, so the sweep did not reach them and a keyless user got a window of
# nearest-neighbour noise rendered as semantic results, with the full-text
# fallback sitting unused directly below.


class _Row:
    """The shape the CLI result renderer reads off a hit."""

    def __init__(self, tag: str):
        self.tag = tag
        self.score = 0.9
        self.page_id = tag
        self.title = tag
        self.snippet = tag
        self.page_type = "file_page"
        self.target_path = tag


class _FakeLanceStore:
    """Stands in for LanceDBVectorStore: records whether it was ranked on."""

    def __init__(self, _path, embedder):
        self._embedder = embedder
        self.searched = False
        self.closed = False

    async def search(self, query, limit=10):
        self.searched = True
        return [_Row("vector-noise")]

    async def close(self):
        self.closed = True


def _patch_cli_search(monkeypatch, tmp_path, embedder):
    """Point both CLI search paths at a fake store and a known FTS result."""
    from repowise.cli.commands import search_cmd

    built: dict[str, _FakeLanceStore] = {}

    def _make(path, embedder=None):
        store = _FakeLanceStore(path, embedder)
        built["store"] = store
        return store

    (tmp_path / ".repowise" / "lancedb").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "repowise.core.persistence.vector_store.LanceDBVectorStore", _make, raising=False
    )
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.build_embedder", lambda _n: embedder
    )
    # "mock" is what a genuinely keyless repo resolves to; individual tests
    # override this to cover the "pinned a real embedder that then failed" case.
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.resolve_embedder_for_repo", lambda _p: "mock"
    )

    class _FTS:
        def __init__(self, _engine):
            pass

        async def search(self, query, limit=10):
            return [_Row("fts-result")]

    class _Engine:
        async def dispose(self):
            return None

    monkeypatch.setattr("repowise.core.persistence.FullTextSearch", _FTS, raising=False)
    monkeypatch.setattr("repowise.core.persistence.create_engine", lambda _u: _Engine(), raising=False)
    monkeypatch.setattr(search_cmd, "get_db_url_for_repo", lambda _p: "sqlite+aiosqlite:///:memory:")
    # _search_semantic renders rather than returns, so capture what it would
    # show AND the title, which is itself part of the contract: full-text rows
    # must not be labelled "Semantic search".
    shown: dict = {"rows": [], "title": None, "notices": []}
    monkeypatch.setattr(
        search_cmd,
        "_display_results",
        # `fmt` and the keyword-only query/mode arrived with `--format json`;
        # this test only exercises the table path.
        lambda results, title, fmt="table", **kw: (
            shown["rows"].extend(r.tag for r in results),
            shown.__setitem__("title", title),
        ),
    )
    monkeypatch.setattr(
        search_cmd.console,
        "print",
        lambda *a, **k: shown["notices"].append(" ".join(str(x) for x in a)),
    )
    return search_cmd, built, shown


def test_cli_semantic_search_does_not_rank_on_a_keyless_store(monkeypatch, tmp_path):
    search_cmd, built, shown = _patch_cli_search(monkeypatch, tmp_path, KeylessEmbedder())

    search_cmd._search_semantic(tmp_path, "how does auth work", 5)

    assert shown["rows"] == ["fts-result"], "keyless must serve full text, not vector noise"
    assert built["store"].searched is False
    assert built["store"].closed is True, "the store is still closed on the guarded path"


def test_cli_semantic_search_says_full_text_answered(monkeypatch, tmp_path):
    """Rendering full-text rows under a "Semantic search" heading is how someone
    concludes semantic retrieval is bad when what they have is no embedder."""
    search_cmd, _built, shown = _patch_cli_search(monkeypatch, tmp_path, KeylessEmbedder())

    search_cmd._search_semantic(tmp_path, "how does auth work", 5)

    assert "Full-text" in shown["title"]
    assert "Semantic" not in shown["title"]
    said = " ".join(shown["notices"]).lower()
    assert "no embedder configured" in said
    assert "full-text" in said


def test_cli_semantic_search_notice_names_a_configured_embedder(monkeypatch, tmp_path):
    """A repo pinned to a real embedder whose key has gone away lands here too.
    Telling that user "no embedder configured" contradicts their own config."""
    search_cmd, _built, shown = _patch_cli_search(monkeypatch, tmp_path, KeylessEmbedder())
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.resolve_embedder_for_repo", lambda _p: "ollama"
    )

    search_cmd._search_semantic(tmp_path, "how does auth work", 5)

    said = " ".join(shown["notices"])
    assert "ollama" in said
    assert "No embedder configured" not in said


def test_cli_semantic_search_still_ranks_on_a_real_store(monkeypatch, tmp_path):
    """The guard must not over-fire: a real embedder still gets semantic search."""
    search_cmd, built, shown = _patch_cli_search(monkeypatch, tmp_path, _RealisticEmbedder())

    search_cmd._search_semantic(tmp_path, "how does auth work", 5)

    assert shown["rows"] == ["vector-noise"]
    assert shown["title"] is not None and "Semantic" in shown["title"]
    assert built["store"].searched is True
    assert shown["notices"] == [], "a working embedder must not be warned about"


def test_cli_workspace_semantic_collect_skips_a_keyless_repo(monkeypatch, tmp_path):
    search_cmd, built, _shown = _patch_cli_search(monkeypatch, tmp_path, KeylessEmbedder())

    results, served_fulltext = search_cmd._collect_semantic(tmp_path, "how does auth work", 5)

    assert [r.tag for r in results] == ["fts-result"]
    assert built["store"].searched is False
    # The flag is what stops the fan-out sorting these full-text scores against
    # another repo's cosine scores, which are not the same quantity.
    assert served_fulltext is True


def test_cli_workspace_semantic_collect_reports_a_real_store_as_semantic(monkeypatch, tmp_path):
    search_cmd, built, _shown = _patch_cli_search(monkeypatch, tmp_path, _RealisticEmbedder())

    results, served_fulltext = search_cmd._collect_semantic(tmp_path, "how does auth work", 5)

    assert [r.tag for r in results] == ["vector-noise"]
    assert built["store"].searched is True
    assert served_fulltext is False
