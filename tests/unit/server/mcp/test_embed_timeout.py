"""The question-embed budget must be raisable, and say so when it isn't enough.

``_EMBED_TIMEOUT_S`` used to be a hardcoded 8.0 in ``_answer_pipeline.py``.
That suits a warm hosted embedding endpoint. A locally served model that has
just been swapped in pays a cold load first — measured elsewhere in this repo
at several seconds for the analogous vector-store cold path (#1678) — and 8s
is not enough headroom. The call then raises ``TimeoutError``,
``question_vector`` returns ``None``, and ``get_answer`` still succeeds: the
run exits 0 with a lexical-only answer and nothing marking the semantic leg
as lost, unless someone is tailing ``repowise.mcp.answer`` at WARNING.

Mirrors ``test_vector_search_timeout.py`` for the sibling budget
(``vector_search_timeout_s``), which this file's ``embed_timeout_s`` copies
the shape of on purpose: same env-override / cap / malformed-value contract.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.server.mcp_server import _answer_pipeline as pipeline
from repowise.server.mcp_server._helpers import (
    _EMBED_TIMEOUT_DEFAULT_S,
    _EMBED_TIMEOUT_ENV,
    _EMBED_TIMEOUT_MAX_S,
    embed_timeout_s,
)

_QUESTION = "why does the retrieval pipeline embed the question up front?"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(_EMBED_TIMEOUT_ENV, raising=False)


@pytest.fixture(autouse=True)
def _clear_vector_cache():
    """Cross-test isolation: the cache is module-level, as the store it keys on
    is process-lived."""
    cache = pipeline._QUESTION_VECTORS
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------------
# embed_timeout_s() — resolution contract, mirrors vector_search_timeout_s()
# ---------------------------------------------------------------------------


def test_default_budget_matches_the_documented_default() -> None:
    assert embed_timeout_s() == _EMBED_TIMEOUT_DEFAULT_S == 8.0


@pytest.mark.parametrize("raw,expected", [("60", 60.0), ("2.5", 2.5), ("  45  ", 45.0)])
def test_env_override_is_honoured(monkeypatch, raw, expected) -> None:
    monkeypatch.setenv(_EMBED_TIMEOUT_ENV, raw)
    assert embed_timeout_s() == expected


def test_override_is_capped(monkeypatch) -> None:
    monkeypatch.setenv(_EMBED_TIMEOUT_ENV, "9999")
    assert embed_timeout_s() == _EMBED_TIMEOUT_MAX_S


@pytest.mark.parametrize("raw", ["abc", "0", "-5", "nan", ""])
def test_unusable_override_keeps_the_default(monkeypatch, raw) -> None:
    """An unparseable value must not disable the leg, matching REPOWISE_EMBEDDING_TIMEOUT."""
    monkeypatch.setenv(_EMBED_TIMEOUT_ENV, raw)
    assert embed_timeout_s() == _EMBED_TIMEOUT_DEFAULT_S


def test_no_hardcoded_eight_second_embed_budget() -> None:
    """Pins the fix: the embed budget is resolved live, not baked in at import."""
    from pathlib import Path

    text = Path(pipeline.__file__).read_text(encoding="utf-8")
    assert "_EMBED_TIMEOUT_S = 8.0" not in text
    assert "_EMBED_TIMEOUT_S =" not in text  # no hardcoded module-level constant at all


# ---------------------------------------------------------------------------
# question_vector() — the actual call site
# ---------------------------------------------------------------------------


class _SlowEmbedTextsStore(InMemoryVectorStore):
    """A store whose embed_texts() takes longer than the old hardcoded budget."""

    def __init__(self, delay: float) -> None:
        super().__init__(embedder=MockEmbedder())
        self._delay = delay

    async def embed_texts(self, texts):
        await asyncio.sleep(self._delay)
        return await super().embed_texts(texts)


async def test_question_vector_gives_up_at_the_default_budget(monkeypatch, caplog) -> None:
    """The exact silent-degradation case this override exists for: a slow
    local embed used to blow the hardcoded 8s. Proven here at a tight budget
    so the test doesn't itself take 8s."""
    monkeypatch.setenv(_EMBED_TIMEOUT_ENV, "0.01")
    store = _SlowEmbedTextsStore(delay=1.0)
    ctx = SimpleNamespace(vector_store=store)

    with caplog.at_level(logging.WARNING, logger="repowise.mcp.answer"):
        result = await pipeline.question_vector(ctx, _QUESTION)

    assert result is None
    assert any("embed the question" in r.getMessage() for r in caplog.records)
    assert any(_EMBED_TIMEOUT_ENV in r.getMessage() for r in caplog.records), caplog.text


async def test_question_vector_survives_a_cold_embed_once_raised(monkeypatch) -> None:
    """The fix in one assertion: the same slow embed that timed out above
    succeeds once the operator raises the budget past it."""
    monkeypatch.setenv(_EMBED_TIMEOUT_ENV, "2")
    store = _SlowEmbedTextsStore(delay=0.05)
    ctx = SimpleNamespace(vector_store=store)

    result = await pipeline.question_vector(ctx, _QUESTION)

    assert result is not None
    assert len(result) == store._embedder.dimensions
