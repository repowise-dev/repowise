"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_search_codebase(setup_mcp):
    # Index pages in the MCP module's vector store (which is the InMemoryVectorStore)
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server import search_codebase

    await mcp_mod._vector_store.embed_and_upsert(
        "file_page:src/auth/service.py",
        "Auth Service — Main authentication service class",
        {"title": "Auth Service", "page_type": "file_page", "target_path": "src/auth/service.py"},
    )
    await mcp_mod._vector_store.embed_and_upsert(
        "file_page:src/db/models.py",
        "DB Models — SQLAlchemy ORM models",
        {"title": "DB Models", "page_type": "file_page", "target_path": "src/db/models.py"},
    )

    result = await search_codebase("authentication service")
    assert "results" in result
    assert len(result["results"]) >= 1


class TestClassifyHitKind:
    """The ``kind`` filter's path heuristic."""

    def test_decision_record_is_doc(self):
        # Regression: decision records carry an empty target_path and used
        # to fall through the path heuristics into "implementation", so
        # kind="implementation" returned decision pages instead of code.
        from repowise.server.mcp_server.tool_search import _classify_hit_kind

        assert _classify_hit_kind("", "decision_record") == "doc"

    def test_overview_and_onboarding_are_doc(self):
        from repowise.server.mcp_server.tool_search import _classify_hit_kind

        assert _classify_hit_kind("", "repo_overview") == "doc"
        assert _classify_hit_kind("onboarding/guided_tour", "onboarding") == "doc"

    def test_file_page_paths_classify_by_role(self):
        from repowise.server.mcp_server.tool_search import _classify_hit_kind

        assert _classify_hit_kind("src/auth/service.py", "file_page") == "implementation"
        assert _classify_hit_kind("tests/unit/test_auth.py", "file_page") == "test"
        assert _classify_hit_kind("pyproject.toml", "file_page") == "config"
        assert _classify_hit_kind("docs/guide.md", "file_page") == "doc"

    def test_module_page_is_doc(self):
        from repowise.server.mcp_server.tool_search import _classify_hit_kind

        assert _classify_hit_kind("src/auth", "module_page") == "doc"
