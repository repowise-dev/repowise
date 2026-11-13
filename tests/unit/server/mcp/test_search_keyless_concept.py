"""A natural-language search on an index with no embedder (#5049)."""

from __future__ import annotations

import pytest

from repowise.server.mcp_server import _state
from repowise.server.mcp_server._meta import KEYLESS_SEARCH_NOTE
from repowise.server.mcp_server.tool_search import _sort_demoting_noise, search_codebase


@pytest.fixture
def keyless(monkeypatch):
    monkeypatch.setattr(_state, "_embedder_status", {"active": "mock", "degraded": False})
    monkeypatch.setattr(_state, "_keyless_search_noted", False)


@pytest.fixture
def embedder(monkeypatch):
    monkeypatch.setattr(_state, "_embedder_status", {"active": "openai", "degraded": False})
    monkeypatch.setattr(_state, "_keyless_search_noted", False)


@pytest.mark.asyncio
async def test_the_first_keyless_concept_search_says_keyword_match_only_once(setup_mcp, keyless):
    first = await search_codebase("how does the auth service work")
    second = await search_codebase("how does the auth service work")

    assert first["results"]
    assert first["_meta"]["keyless_note"] == KEYLESS_SEARCH_NOTE
    assert "keyword match only" in KEYLESS_SEARCH_NOTE
    assert "keyless_note" not in second["_meta"]
    assert second["_meta"]["semantic_search"] is False


@pytest.mark.asyncio
async def test_a_name_search_carries_no_keyless_note(setup_mcp, keyless):
    out = await search_codebase("AuthService", mode="symbol")

    assert "keyless_note" not in out["_meta"]
    assert _state._keyless_search_noted is False


@pytest.mark.asyncio
async def test_a_search_with_an_embedder_carries_no_keyless_note(setup_mcp, embedder):
    out = await search_codebase("how does the auth service work")

    assert "keyless_note" not in out["_meta"]


def _page(page_type: str, score: float) -> dict:
    return {"page_id": f"{page_type}:x", "page_type": page_type, "relevance_score": score}


@pytest.mark.parametrize(
    "query, overview_first",
    [("how are sessions stored", False), ("repository overview", True)],
)
def test_keyless_ranks_repo_wide_pages_below_file_pages(keyless, query, overview_first):
    rows = [_page("repo_overview", 9.0), _page("onboarding", 8.0), _page("file_page", 1.0)]
    _sort_demoting_noise(rows, query)

    assert (rows[0]["page_type"] == "repo_overview") is overview_first


def test_with_an_embedder_repo_wide_pages_keep_their_rank(embedder):
    rows = [_page("repo_overview", 9.0), _page("file_page", 1.0)]
    _sort_demoting_noise(rows, "how are sessions stored")

    assert rows[0]["page_type"] == "repo_overview"


@pytest.mark.asyncio
async def test_a_path_query_naming_a_directory_leads_with_it(setup_mcp):
    out = await search_codebase("src/auth", mode="path")

    assert out["results"][0] == {"type": "directory", "path": "src/auth", "next": "get_context"}
    assert out["next_actions"][0].startswith('get_context(targets=["src/auth"]')


@pytest.mark.asyncio
async def test_a_bare_directory_name_with_no_symbol_leads_with_the_directory(setup_mcp):
    out = await search_codebase("auth")

    assert out["results"][0]["type"] == "directory"
    assert out["results"][0]["path"] == "src/auth"
