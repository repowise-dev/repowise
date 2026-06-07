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


def _mk_result(page_id, title, page_type, target_path, score):
    from repowise.core.persistence.search import SearchResult

    return SearchResult(
        page_id=page_id,
        title=title,
        page_type=page_type,
        target_path=target_path,
        score=score,
        snippet=title,
        search_type="vector",
    )


class TestDecisionDownweight:
    """Decision records must not crowd file pages out of the top ranks."""

    def test_why_shaped_queries(self):
        from repowise.server.mcp_server.tool_search import _is_why_shaped

        assert _is_why_shaped("why is auth using JWT?")
        assert _is_why_shaped("when did we switch to SQLite")
        assert _is_why_shaped("who decided on LanceDB")
        assert _is_why_shaped("what was the rationale for WAL mode")
        assert _is_why_shaped("show me the decision about persistence")
        assert not _is_why_shaped("where is the SQLite store for distilled output")
        assert not _is_why_shaped("authentication flow")

    def test_fetch_limit_always_overfetches(self):
        from repowise.server.mcp_server.tool_search import _fetch_limit_for

        # Down-weighting can only promote file pages that are inside the
        # fetched window, so even unfiltered queries over-fetch.
        assert _fetch_limit_for(5, None) == 15
        assert _fetch_limit_for(5, "implementation") == 30

    @pytest.mark.asyncio
    async def test_file_page_outranks_downweighted_decision(self, setup_mcp):
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        async def fake_search(query, limit=10):
            return [
                _mk_result("decision:d1", "Use repo-local SQLite", "decision_record", "", 0.57),
                _mk_result(
                    "file_page:src/auth/service.py", "Auth Service", "file_page",
                    "src/auth/service.py", 0.42,
                ),
            ]

        mcp_mod._vector_store.search = fake_search
        result = await search_codebase("where is the SQLite store")
        types = [r["page_type"] for r in result["results"]]
        assert types[0] == "file_page"
        # The decision page survives, just demoted below the file page.
        assert "decision_record" in types

    @pytest.mark.asyncio
    async def test_why_query_keeps_decision_ranking(self, setup_mcp):
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        async def fake_search(query, limit=10):
            return [
                _mk_result("decision:d1", "Use repo-local SQLite", "decision_record", "", 0.57),
                _mk_result(
                    "file_page:src/auth/service.py", "Auth Service", "file_page",
                    "src/auth/service.py", 0.42,
                ),
            ]

        mcp_mod._vector_store.search = fake_search
        result = await search_codebase("why did we choose SQLite for persistence?")
        assert result["results"][0]["page_type"] == "decision_record"

    @pytest.mark.asyncio
    async def test_kind_filter_survives_decision_flood(self, setup_mcp):
        # 15 decision records ahead of one file page: with the old 3x
        # over-fetch the window held only decisions and kind="implementation"
        # returned []. The 6x window must surface the file page.
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        flood = [
            _mk_result(f"decision:d{i}", f"Decision {i}", "decision_record", "", 0.6 - i * 0.01)
            for i in range(15)
        ]
        flood.append(
            _mk_result(
                "file_page:src/auth/service.py", "Auth Service", "file_page",
                "src/auth/service.py", 0.35,
            )
        )

        async def fake_search(query, limit=10):
            return flood[:limit]

        mcp_mod._vector_store.search = fake_search
        result = await search_codebase("sqlite store", limit=5, kind="implementation")
        paths = [r["target_path"] for r in result["results"]]
        assert paths == ["src/auth/service.py"]

    @pytest.mark.asyncio
    async def test_federated_path_filters_kind_before_truncation(self, setup_mcp):
        # _search_single_repo used to truncate to ``limit`` before the
        # aggregate kind filter ran, so federated kind searches under-filled.
        import types

        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server.tool_search import _search_single_repo

        flood = [
            _mk_result(f"decision:d{i}", f"Decision {i}", "decision_record", "", 0.6 - i * 0.01)
            for i in range(15)
        ]
        flood.append(
            _mk_result(
                "file_page:src/auth/service.py", "Auth Service", "file_page",
                "src/auth/service.py", 0.35,
            )
        )

        async def fake_search(query, limit=10):
            return flood[:limit]

        ctx = types.SimpleNamespace(
            vector_store=types.SimpleNamespace(search=fake_search),
            fts=mcp_mod._fts,
            session_factory=mcp_mod._session_factory,
            vector_store_ready=None,
            path="/tmp/test-repo",
        )
        results, method = await _search_single_repo(
            ctx, "sqlite store", limit=5, page_type=None, kind="implementation"
        )
        assert method == "embedding"
        assert [r["target_path"] for r in results] == ["src/auth/service.py"]


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
