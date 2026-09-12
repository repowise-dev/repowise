"""``kind="query"|"document"`` through the embedding stack.

Sits alongside ``test_embedding_timeout_resolution.py`` for the same reason:
one cross-cutting contract — here, which side of retrieval a text is on —
that every embedder and every store must honour identically, so it belongs
in one place rather than duplicated per provider.

The empty-by-default behaviour (``resolve_embed_prefix`` returns ``""`` for
both kinds until an operator opts in) is what makes threading ``kind``
through ``embed()``/``embed_texts()``/``search()`` safe to ship: every
existing request stays byte-identical unless ``REPOWISE_EMBED_QUERY_PREFIX``
/ ``REPOWISE_EMBED_DOC_PREFIX`` is set.
"""

from __future__ import annotations

import hashlib
import math

import pytest

from repowise.core.analysis.decisions.semantic_match import (
    decision_vector_item,
    find_duplicate_decision,
    find_related_decisions,
    find_related_decisions_many,
    upsert_decision_vectors,
)
from repowise.core.persistence.vector_store.in_memory import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder, resolve_embed_prefix

# ---------------------------------------------------------------------------
# resolve_embed_prefix
# ---------------------------------------------------------------------------


def _clear(monkeypatch):
    monkeypatch.delenv("REPOWISE_EMBED_QUERY_PREFIX", raising=False)
    monkeypatch.delenv("REPOWISE_EMBED_DOC_PREFIX", raising=False)


def test_empty_by_default_for_both_kinds(monkeypatch):
    _clear(monkeypatch)
    assert resolve_embed_prefix("query") == ""
    assert resolve_embed_prefix("document") == ""


def test_reads_the_matching_env_var(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("REPOWISE_EMBED_QUERY_PREFIX", "query: ")
    monkeypatch.setenv("REPOWISE_EMBED_DOC_PREFIX", "passage: ")
    assert resolve_embed_prefix("query") == "query: "
    assert resolve_embed_prefix("document") == "passage: "


def test_query_prefix_does_not_leak_into_document(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("REPOWISE_EMBED_QUERY_PREFIX", "query: ")
    assert resolve_embed_prefix("document") == ""


def test_invalid_kind_raises():
    with pytest.raises(ValueError, match="kind must be 'query' or 'document'"):
        resolve_embed_prefix("passage")


# ---------------------------------------------------------------------------
# MockEmbedder — kind is accepted but never changes the vector
# ---------------------------------------------------------------------------


async def test_mock_embedder_ignores_kind():
    emb = MockEmbedder()
    doc_vec = (await emb.embed(["hello"], kind="document"))[0]
    query_vec = (await emb.embed(["hello"], kind="query"))[0]
    default_vec = (await emb.embed(["hello"]))[0]
    assert doc_vec == query_vec == default_vec


# ---------------------------------------------------------------------------
# VectorStore wiring — upsert stays "document", search moves to "query"
# ---------------------------------------------------------------------------


class _KindSpyEmbedder(MockEmbedder):
    """Records the ``kind`` every ``embed()`` call was made with."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        self.calls.append(kind)
        return await super().embed(texts, kind=kind)


async def test_upsert_embeds_as_document():
    spy = _KindSpyEmbedder()
    store = InMemoryVectorStore(spy)
    await store.embed_and_upsert("p1", "some content", {})
    assert spy.calls == ["document"]


async def test_search_embeds_the_query_as_query():
    spy = _KindSpyEmbedder()
    store = InMemoryVectorStore(spy)
    await store.embed_and_upsert("p1", "some content", {})
    spy.calls.clear()

    await store.search("some question", limit=5)
    assert spy.calls == ["query"]


async def test_search_many_embeds_every_query_as_query():
    spy = _KindSpyEmbedder()
    store = InMemoryVectorStore(spy)
    await store.embed_and_upsert("p1", "some content", {})
    spy.calls.clear()

    await store.search_many(["q1", "q2"], limit=5)
    assert spy.calls == ["query"]  # one batched embedder call for both queries


async def test_search_accepts_an_explicit_document_kind():
    # The escape hatch decision dedup uses — see the tests below.
    spy = _KindSpyEmbedder()
    store = InMemoryVectorStore(spy)
    await store.embed_and_upsert("p1", "some content", {})
    spy.calls.clear()

    await store.search("some content", limit=5, kind="document")
    assert spy.calls == ["document"]


async def test_embed_texts_defaults_to_document():
    spy = _KindSpyEmbedder()
    store = InMemoryVectorStore(spy)
    await store.embed_texts(["some text"])
    assert spy.calls == ["document"]


async def test_embed_texts_forwards_an_explicit_query_kind():
    spy = _KindSpyEmbedder()
    store = InMemoryVectorStore(spy)
    await store.embed_texts(["some text"], kind="query")
    assert spy.calls == ["query"]


# ---------------------------------------------------------------------------
# Decision dedup — the symmetric near-duplicate path stays on "document",
# even though it goes through the same VectorStore.search()/search_many()
# every asymmetric document-search caller uses.
#
# MockEmbedder can't prove this: it deliberately ignores kind, so a bug that
# silently reverted every call site below to the default "query" kind would
# not fail with it. This directional double makes kind change the vector — a
# strong, orthogonal shift, not a subtle one — so a kind mismatch is
# guaranteed to fall below DEFAULT_DEDUP_TAU (0.83) while a kind match on
# identical text is guaranteed to be an exact 1.0.
# ---------------------------------------------------------------------------


class _DirectionalEmbedder:
    """Deterministic embedder whose vector depends on both *text* and *kind*.

    Vector = [10, 0, f1, f2] for "document", [0, 10, f1, f2] for "query",
    L2-normalised. (f1, f2) is a small deterministic fingerprint of *text*.
    The kind axes dominate (weight 10 vs. |f| <= 1), so two vectors for the
    same text but different kind are nearly orthogonal (cosine ~0.01-0.02),
    while two vectors for the same text and the same kind are identical.
    """

    dimensions: int = 4

    async def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        axis = 0 if kind == "document" else 1
        out: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            f1 = digest[0] / 127.5 - 1.0
            f2 = digest[1] / 127.5 - 1.0
            raw = [0.0, 0.0, f1, f2]
            raw[axis] = 10.0
            norm = math.sqrt(sum(x * x for x in raw))
            out.append([x / norm for x in raw])
        return out


async def _seeded_store() -> InMemoryVectorStore:
    store = InMemoryVectorStore(_DirectionalEmbedder())
    item = decision_vector_item("d1", title="Use Redis for session cache")
    assert item is not None
    await upsert_decision_vectors(store, [item])
    return store


async def test_find_duplicate_decision_matches_identical_text():
    store = await _seeded_store()
    found = await find_duplicate_decision(store, title="Use Redis for session cache")
    assert found == "d1"


async def test_find_duplicate_decision_would_miss_under_the_query_kind():
    # Proves the double actually discriminates kind, and pins the failure
    # mode a regression to the pre-fix hardcoded kind="query" would hit.
    store = await _seeded_store()
    missed = await store.search("Use Redis for session cache", limit=50, kind="query")
    assert missed == [] or missed[0].score < 0.83


async def test_find_related_decisions_matches_identical_text():
    store = await _seeded_store()
    related = await find_related_decisions(
        store, title="Use Redis for session cache", lo=0.5, hi=1.01
    )
    assert ("d1", pytest.approx(1.0)) in [(rid, pytest.approx(score)) for rid, score in related]


async def test_find_related_decisions_many_matches_identical_text():
    store = await _seeded_store()
    [related] = await find_related_decisions_many(
        store, [("Use Redis for session cache", "", set())], lo=0.5, hi=1.01
    )
    assert related and related[0][0] == "d1"
    assert related[0][1] == pytest.approx(1.0)
