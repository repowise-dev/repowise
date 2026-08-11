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
# The CLI used to carry its own retrieval and its own copy of this guard. It is
# now an adapter over ``search_codebase``, so the guard above is the one that
# runs. What is left on the CLI side is the *telling*: rendering full-text rows
# without saying so is how someone concludes semantic retrieval is bad when what
# they have is no embedder. These pin the chain that carries that signal across
# the seam — the bridge publishes the embedder status, ``_meta`` turns it into
# ``semantic_search: false``, and the command warns on it.


@pytest.fixture(autouse=True)
def _reset_embedder_status():
    """``_embedder_status`` is process-global, and these tests set it.

    Left keyless, it would put ``semantic_search: false`` into the ``_meta`` of
    every later test in the session that builds one.
    """
    from repowise.server.mcp_server import _state

    before = getattr(_state, "_embedder_status", None)
    yield
    _state._embedder_status = before


class _Notices:
    def __init__(self) -> None:
        self.said: list[str] = []

    def print(self, *args: object) -> None:
        self.said.append(" ".join(str(a) for a in args))


def _meta_for(embedder, requested: str) -> dict:
    """``_meta`` as a real call would carry it, via the bridge's own publisher."""
    from repowise.cli.tool_bridge import _publish_embedder_status
    from repowise.server.mcp_server._meta import build_meta

    _publish_embedder_status(requested, embedder)
    return build_meta()


def test_a_keyless_index_reports_no_semantic_search_in_meta() -> None:
    """The signal the CLI notice reads. Nothing is broken and nothing was
    misconfigured, so it is not flagged as degraded — but retrieval really is
    full-text-only and the payload has to say so."""
    meta = _meta_for(KeylessEmbedder(), "mock")

    assert meta["semantic_search"] is False
    assert meta.get("embedder_degraded") is not True


def test_a_failed_pinned_embedder_reports_degraded_and_names_itself() -> None:
    meta = _meta_for(KeylessEmbedder(), "ollama")

    assert meta["semantic_search"] is False
    assert meta["embedder_degraded"] is True
    assert "ollama" in meta["embedder_warning"]


def test_a_real_embedder_is_not_flagged() -> None:
    """The guard must not over-fire: a working embedder is not warned about."""
    meta = _meta_for(_RealisticEmbedder(), "openai")

    assert "semantic_search" not in meta
    assert "embedder_degraded" not in meta


def test_cli_semantic_search_says_full_text_answered() -> None:
    from repowise.cli.commands.search_cmd import _warn_if_lexical_only

    notices = _Notices()
    _warn_if_lexical_only({"_meta": _meta_for(KeylessEmbedder(), "mock")}, "semantic", notices)

    said = " ".join(notices.said).lower()
    assert "no embedder configured" in said
    assert "full-text results instead" in said


def test_cli_semantic_notice_names_a_configured_embedder() -> None:
    """A repo pinned to a real embedder whose key has gone away lands here too.
    Telling that user "no embedder configured" contradicts their own config."""
    from repowise.cli.commands.search_cmd import _warn_if_lexical_only

    notices = _Notices()
    _warn_if_lexical_only({"_meta": _meta_for(KeylessEmbedder(), "ollama")}, "semantic", notices)

    said = " ".join(notices.said)
    assert "ollama" in said
    assert "No embedder configured" not in said


def test_cli_does_not_warn_when_semantic_search_really_ran() -> None:
    from repowise.cli.commands.search_cmd import _warn_if_lexical_only

    notices = _Notices()
    _warn_if_lexical_only({"_meta": _meta_for(_RealisticEmbedder(), "openai")}, "semantic", notices)

    assert notices.said == []


@pytest.mark.parametrize("requested", ["fulltext", "concept", "auto", "symbol", "path"])
def test_only_the_mode_that_promised_semantics_is_warned_about(requested: str) -> None:
    """``fulltext`` never claimed semantic matching, and the tool's own mode
    spellings are the tool's contract, where ``_meta`` already says this."""
    from repowise.cli.commands.search_cmd import _warn_if_lexical_only

    notices = _Notices()
    _warn_if_lexical_only({"_meta": _meta_for(KeylessEmbedder(), "mock")}, requested, notices)

    assert notices.said == []
