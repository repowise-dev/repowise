"""Tests for keyless concept search note (#5049)."""

from __future__ import annotations

import pytest

from repowise.server.mcp_server import _state
from repowise.server.mcp_server._meta import KEYLESS_CONCEPT_SEARCH_NOTE


@pytest.fixture
def mock_keyless_embedder(monkeypatch):
    """Set embedder state to mock/keyless so semantic_search_state() is False."""
    monkeypatch.setattr(_state, "_embedder_status", {"active": "mock", "degraded": False})


@pytest.fixture
def mock_active_embedder(monkeypatch):
    """Set embedder state to real/active embedder."""
    monkeypatch.setattr(_state, "_embedder_status", {"active": "openai", "degraded": False})


@pytest.mark.asyncio
async def test_keyless_concept_search_with_rows_carries_note(setup_mcp, mock_keyless_embedder):
    from repowise.server.mcp_server import search_codebase

    res = await search_codebase("database query connection", mode="concept")
    if res.get("results"):
        assert res["_meta"].get("keyless_note") == KEYLESS_CONCEPT_SEARCH_NOTE
        assert "keyword matches only" in res["_meta"]["keyless_note"]
        assert 'mode="symbol"' in res["_meta"]["keyless_note"]


@pytest.mark.asyncio
async def test_federated_keyless_concept_search_carries_note(setup_mcp, mock_keyless_embedder):
    from repowise.server.mcp_server import search_codebase

    res = await search_codebase("database query connection", mode="concept", repo="all")
    if res.get("results"):
        assert res["_meta"].get("keyless_note") == KEYLESS_CONCEPT_SEARCH_NOTE
        assert "keyword matches only" in res["_meta"]["keyless_note"]
        assert 'mode="symbol"' in res["_meta"]["keyless_note"]


@pytest.mark.asyncio
async def test_symbol_mode_search_does_not_carry_keyless_note(setup_mcp, mock_keyless_embedder):
    from repowise.server.mcp_server import search_codebase

    res = await search_codebase("get_session", mode="symbol")
    assert "keyless_note" not in res.get("_meta", {})


@pytest.mark.asyncio
async def test_active_embedder_concept_search_does_not_carry_keyless_note(
    setup_mcp, mock_active_embedder
):
    from repowise.server.mcp_server import search_codebase

    res = await search_codebase("database query connection", mode="concept")
    assert "keyless_note" not in res.get("_meta", {})
