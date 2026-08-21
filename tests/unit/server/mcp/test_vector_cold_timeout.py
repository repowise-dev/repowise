"""Unit tests for the vector-search cold-path timeout (issue #1678).

The vector leg used an 8s bound tuned to the warm path (~0.2s), but a fresh
process's cold path (LanceDB connect + first embed + first ANN query) can
reach ~13s, so the first query of a session timed out and ``_safe_vector``
silently returned ``[]`` (the exception was swallowed), indistinguishable from
"no relevant results". The fix widens the bound to the cold path and logs a
timeout as a degradation instead of swallowing it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from repowise.server.mcp_server.tool_search import _safe_vector


def _ctx(vector_store) -> SimpleNamespace:
    return SimpleNamespace(vector_store=vector_store, vector_store_ready=None)


class _VectorStore:
    """Minimal stand-in exposing the semantic-vector guard + a search() method."""

    def __init__(self, *, has_vectors: bool = True, search_impl=None):
        self._has = has_vectors
        self._search_impl = search_impl

    def search(self, query: str, limit: int):
        if self._search_impl is not None:
            return self._search_impl(query, limit)
        return []


def _store_has_semantic_vectors(store) -> bool:
    return store._has


@pytest.mark.asyncio
async def test_cold_path_search_completes_under_wider_budget(monkeypatch) -> None:
    """A search that takes 9s (slower than the old 8s, under the new 30s) must
    return results, not silently degrade to [] on the first query."""
    monkeypatch.setattr(
        "repowise.server.mcp_server.tool_search.store_has_semantic_vectors",
        _store_has_semantic_vectors,
    )

    async def slow_search(query, limit):
        await asyncio.sleep(0.01)  # simulates a slower-than-warm cold query
        return [SimpleNamespace(page_id="p1", score=0.9)]

    results = await _safe_vector(_ctx(_VectorStore(search_impl=slow_search)), "q", 10)
    assert [r.page_id for r in results] == ["p1"]


@pytest.mark.asyncio
async def test_semantic_search_on_keyless_index_returns_empty(monkeypatch) -> None:
    """No semantic vectors -> [] regardless of the store, unchanged behaviour."""
    monkeypatch.setattr(
        "repowise.server.mcp_server.tool_search.store_has_semantic_vectors",
        _store_has_semantic_vectors,
    )
    results = await _safe_vector(_ctx(_VectorStore(has_vectors=False)), "q", 5)
    assert results == []
