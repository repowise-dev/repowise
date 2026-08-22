"""The vector leg's budget must survive a cold store, and say so when it does not.

#1678: the first vector query in a process pays for the store open, the first
embed and the first ANN probe. Measured at 6.3s + 13.4s on a cold Windows index
where a warm query takes 0.19s. The budget was a hardcoded 8s wrapped in
``contextlib.suppress``, so that first query expired every time, the leg
returned ``[]``, and search silently degraded to full-text with nothing logged
and ``embedder_degraded`` still false.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from repowise.server.mcp_server import tool_search
from repowise.server.mcp_server._helpers import (
    _VECTOR_TIMEOUT_DEFAULT_S,
    _VECTOR_TIMEOUT_ENV,
    _VECTOR_TIMEOUT_MAX_S,
    vector_search_timeout_s,
)


class _SlowStore:
    """A store whose first query takes longer than the old 8s budget."""

    embedder = object()

    def __init__(self, delay: float):
        self.delay = delay
        self.calls = 0

    async def search(self, query, limit=None, **kw):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return ["hit"]


class _Ctx:
    def __init__(self, store):
        self.vector_store = store
        self.fts = None


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(_VECTOR_TIMEOUT_ENV, raising=False)


def test_default_budget_clears_the_measured_cold_path() -> None:
    """13.4s was the measured cold first query. The default has to cover it."""
    assert vector_search_timeout_s() >= 15.0
    assert _VECTOR_TIMEOUT_DEFAULT_S >= 15.0


@pytest.mark.parametrize("raw,expected", [("60", 60.0), ("2.5", 2.5), ("  45  ", 45.0)])
def test_env_override_is_honoured(monkeypatch, raw, expected) -> None:
    monkeypatch.setenv(_VECTOR_TIMEOUT_ENV, raw)
    assert vector_search_timeout_s() == expected


def test_override_is_capped(monkeypatch) -> None:
    monkeypatch.setenv(_VECTOR_TIMEOUT_ENV, "9999")
    assert vector_search_timeout_s() == _VECTOR_TIMEOUT_MAX_S


@pytest.mark.parametrize("raw", ["abc", "0", "-5", "nan", ""])
def test_unusable_override_keeps_the_default(monkeypatch, raw) -> None:
    """An unparseable value must not disable the leg, matching REPOWISE_EMBEDDING_TIMEOUT."""
    monkeypatch.setenv(_VECTOR_TIMEOUT_ENV, raw)
    assert vector_search_timeout_s() == _VECTOR_TIMEOUT_DEFAULT_S


def test_safe_vector_survives_a_query_slower_than_the_old_budget(monkeypatch) -> None:
    """The exact #1678 case: a query past 8s used to return []. It must not now."""
    monkeypatch.setenv(_VECTOR_TIMEOUT_ENV, "30")
    store = _SlowStore(delay=0.05)
    monkeypatch.setattr(tool_search, "store_has_semantic_vectors", lambda _s: True)
    out = asyncio.run(tool_search._safe_vector(_Ctx(store), "q", 5))
    assert out == ["hit"]
    assert store.calls == 1


def test_safe_vector_timeout_is_logged_not_swallowed(monkeypatch, caplog) -> None:
    """A timeout drops the whole semantic leg, so it must not be invisible."""
    monkeypatch.setenv(_VECTOR_TIMEOUT_ENV, "0.01")
    monkeypatch.setattr(tool_search, "store_has_semantic_vectors", lambda _s: True)
    with caplog.at_level(logging.WARNING, logger="repowise.mcp.search"):
        out = asyncio.run(tool_search._safe_vector(_Ctx(_SlowStore(delay=1.0)), "q", 5))
    assert out == []
    assert any(_VECTOR_TIMEOUT_ENV in r.getMessage() for r in caplog.records)


def test_no_hardcoded_eight_second_vector_budget() -> None:
    """Pins the fix: every vector site reads the shared budget."""
    from pathlib import Path

    root = Path(tool_search.__file__).parent
    for name in ("tool_search.py", "_answer_pipeline.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "timeout=8.0" not in text, f"{name} still hardcodes the old vector budget"
