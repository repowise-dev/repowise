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


async def _seed_page(page_id, target_path, page_type="file_page"):
    """Insert a Page row into the setup_mcp DB so _load_page_info resolves its
    target_path. Reuses the seeded repository_id so the row is valid."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    import repowise.server.mcp_server as mcp_mod
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import Page

    now = datetime(2026, 1, 1, tzinfo=UTC)
    async with get_session(mcp_mod._session_factory) as session:
        rid = (await session.execute(select(Page.repository_id).limit(1))).scalar()
        session.add(
            Page(
                id=page_id,
                repository_id=rid,
                page_type=page_type,
                title="Test Service",
                content="tests",
                target_path=target_path,
                source_hash="seed",
                model_name="mock",
                provider_name="mock",
                generation_level=2,
                confidence=0.5,
                freshness_status="fresh",
                metadata_json="{}",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()


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
                    "file_page:src/auth/service.py",
                    "Auth Service",
                    "file_page",
                    "src/auth/service.py",
                    0.42,
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
                    "file_page:src/auth/service.py",
                    "Auth Service",
                    "file_page",
                    "src/auth/service.py",
                    0.42,
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
                "file_page:src/auth/service.py",
                "Auth Service",
                "file_page",
                "src/auth/service.py",
                0.35,
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
                "file_page:src/auth/service.py",
                "Auth Service",
                "file_page",
                "src/auth/service.py",
                0.35,
            )
        )

        async def fake_search(query, limit=10):
            return flood[:limit]

        async def empty_fts(query, limit=10):
            return []

        ctx = types.SimpleNamespace(
            vector_store=types.SimpleNamespace(search=fake_search),
            # Stub FTS: this test drives ranking through the vector list, and a
            # real FTS handle on the shared in-memory engine (per-connection
            # isolation) would shadow the seeded rows for the follow-up session.
            fts=types.SimpleNamespace(search=empty_fts),
            session_factory=mcp_mod._session_factory,
            vector_store_ready=None,
            path="/tmp/test-repo",
        )
        results = await _search_single_repo(
            ctx, "sqlite store", limit=5, page_type=None, kind="implementation"
        )
        assert [r["target_path"] for r in results] == ["src/auth/service.py"]
        # Fused retrieval tags each hit with the retrievers that surfaced it.
        assert results[0]["sources"] == ["vector"]


class TestNoiseDemotion:
    """Test file pages and near-duplicate decisions must not crowd the top ranks."""

    def test_is_test_query(self):
        from repowise.server.mcp_server.tool_search import _is_test_query

        assert _is_test_query("how is the auth service tested")
        assert _is_test_query("unit test for the parser")
        assert _is_test_query("where are the pytest fixtures")
        assert not _is_test_query("how does the auth service authenticate")
        assert not _is_test_query("where is the SQLite store")

    def test_is_test_page(self):
        from repowise.server.mcp_server.tool_search import _is_test_page

        assert _is_test_page({"page_type": "file_page", "target_path": "tests/unit/test_auth.py"})
        assert _is_test_page({"page_type": "file_page", "target_path": "src/auth/service_test.py"})
        assert not _is_test_page({"page_type": "file_page", "target_path": "src/auth/service.py"})
        # Only file pages classify as test pages, never decision/concept pages.
        assert not _is_test_page({"page_type": "decision_record", "target_path": ""})

    def test_is_test_page_keeps_support_and_lookalikes_ranked(self):
        """Demotion is tests only, and only real tests (#1103).

        The substring token list this used to run on demoted every production
        file whose path merely contained "test_", and demoted the fixtures a
        "where are the shared fixtures" query is asking for.
        """
        from repowise.server.mcp_server.tool_search import _is_test_page

        def page(path: str) -> dict:
            return {"page_type": "file_page", "target_path": path}

        # Test support keeps its rank: it is often the answer. Leading segments
        # on purpose — the token list this replaced anchored on "/tests/", so a
        # root-level path would pass for the wrong reason.
        assert not _is_test_page(page("packages/core/tests/conftest.py"))
        assert not _is_test_page(page("packages/core/tests/factories/user.py"))
        # Production code that merely spells "test".
        assert not _is_test_page(page("src/analysis/missing_test_signal.py"))
        assert not _is_test_page(page("src/latest/api.py"))
        # Real tests the token list missed.
        assert _is_test_page(page("myapp/tests.py"))
        assert _is_test_page(page("src/test/java/Foo.java"))
        assert _is_test_page(page("Foo.Tests/Bar.cs"))
        assert _is_test_page(page("e2e/login.ts"))

    def test_downweight_test_pages_scales_unless_test_query(self):
        from repowise.server.mcp_server.tool_search import _downweight_test_pages

        rows = [
            {"page_type": "file_page", "target_path": "tests/test_x.py", "relevance_score": 1.0},
            {"page_type": "file_page", "target_path": "src/x.py", "relevance_score": 1.0},
        ]
        _downweight_test_pages(rows, "how does x work")
        assert rows[0]["relevance_score"] < 1.0  # test page scaled down
        assert rows[1]["relevance_score"] == 1.0  # impl page untouched

        rows2 = [
            {"page_type": "file_page", "target_path": "tests/test_x.py", "relevance_score": 1.0},
        ]
        _downweight_test_pages(rows2, "how is x tested")
        assert rows2[0]["relevance_score"] == 1.0  # test-focused query: no scaling

    def test_sort_demoting_noise_ranks_impl_over_test(self):
        from repowise.server.mcp_server.tool_search import _sort_demoting_noise

        rows = [
            {"page_type": "file_page", "target_path": "tests/test_x.py", "relevance_score": 0.9},
            {"page_type": "file_page", "target_path": "src/x.py", "relevance_score": 0.4},
        ]
        _sort_demoting_noise(rows, "how does x work")
        assert rows[0]["target_path"] == "src/x.py"  # impl promoted over higher-scored test
        assert rows[1]["target_path"] == "tests/test_x.py"

    def test_dedup_decisions_collapses_near_duplicates(self):
        from repowise.server.mcp_server.tool_search import _dedup_decisions

        rows = [
            {
                "page_type": "decision_record",
                "title": "CLI incremental update regenerates only affected pages",
                "page_id": "d1",
            },
            {
                "page_type": "decision_record",
                "title": "CLI incremental update regenerates only affected pages.",
                "page_id": "d2",
            },
            {
                "page_type": "decision_record",
                "title": "CLI  incremental-update regenerates only affected pages",
                "page_id": "d3",
            },
            {"page_type": "file_page", "title": "x", "page_id": "f1"},
            {"page_type": "decision_record", "title": "Use repo-local SQLite", "page_id": "d4"},
        ]
        out = _dedup_decisions(rows)
        ids = [r["page_id"] for r in out]
        assert ids == ["d1", "f1", "d4"]  # three near-dups -> one; file + distinct decision kept

    @pytest.mark.asyncio
    async def test_impl_page_outranks_its_test_page(self, setup_mcp):
        # Wiring check: demotion runs AFTER target_path is attached, so a
        # higher-scored test page still falls below the impl file page. Uses the
        # pre-seeded src/auth/service.py plus a seeded test page so both page_ids
        # resolve a target_path in _load_page_info.
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        await _seed_page("file_page:tests/unit/test_service.py", "tests/unit/test_service.py")

        async def fake_search(query, limit=10):
            return [
                _mk_result(
                    "file_page:tests/unit/test_service.py",
                    "Test Service",
                    "file_page",
                    "tests/unit/test_service.py",
                    0.62,
                ),
                _mk_result(
                    "file_page:src/auth/service.py",
                    "Auth Service",
                    "file_page",
                    "src/auth/service.py",
                    0.41,
                ),
            ]

        mcp_mod._vector_store.search = fake_search
        result = await search_codebase("how does the auth service work")
        paths = [r["target_path"] for r in result["results"]]
        assert paths[0] == "src/auth/service.py"
        assert "tests/unit/test_service.py" in paths  # demoted, not dropped

    @pytest.mark.asyncio
    async def test_test_query_keeps_test_page_ranking(self, setup_mcp):
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        await _seed_page("file_page:tests/unit/test_service.py", "tests/unit/test_service.py")

        async def fake_search(query, limit=10):
            return [
                _mk_result(
                    "file_page:tests/unit/test_service.py",
                    "Test Service",
                    "file_page",
                    "tests/unit/test_service.py",
                    0.62,
                ),
                _mk_result(
                    "file_page:src/auth/service.py",
                    "Auth Service",
                    "file_page",
                    "src/auth/service.py",
                    0.41,
                ),
            ]

        mcp_mod._vector_store.search = fake_search
        result = await search_codebase("how is the auth service tested")
        assert result["results"][0]["target_path"] == "tests/unit/test_service.py"


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
        assert _classify_hit_kind("onboarding/how_it_works", "onboarding") == "doc"

    def test_file_page_paths_classify_by_role(self):
        from repowise.server.mcp_server.tool_search import _classify_hit_kind

        assert _classify_hit_kind("src/auth/service.py", "file_page") == "implementation"
        assert _classify_hit_kind("tests/unit/test_auth.py", "file_page") == "test"
        assert _classify_hit_kind("pyproject.toml", "file_page") == "config"
        assert _classify_hit_kind("docs/guide.md", "file_page") == "doc"

    def test_kind_test_covers_support_unlike_the_demotion(self):
        """``kind`` splits the repo in two, so a fixture lands on the test side.

        Deliberately a different answer from ``_is_test_page``: nobody asking
        for ``kind="implementation"`` wants a conftest back, but a fixture page
        should still rank normally on an ordinary query.
        """
        from repowise.server.mcp_server.tool_search import _classify_hit_kind, _is_test_page

        for path in ("packages/core/tests/conftest.py", "packages/core/tests/factories/user.py"):
            assert _classify_hit_kind(path, "file_page") == "test"
            assert not _is_test_page({"page_type": "file_page", "target_path": path})
        # Config beats test: a workflow named for tests is still a workflow.
        assert _classify_hit_kind(".github/workflows/tests.yml", "file_page") == "config"
        # Case-sensitive rules survive: the classifier must not see a lowercased
        # path, or FooTest.java and Foo.Tests/ stop matching.
        assert _classify_hit_kind("Foo.Tests/Bar.cs", "file_page") == "test"
        assert _classify_hit_kind("src/FooTest.java", "file_page") == "test"
        assert _classify_hit_kind("src/latest/api.py", "file_page") == "implementation"

    def test_module_page_is_doc(self):
        from repowise.server.mcp_server.tool_search import _classify_hit_kind

        assert _classify_hit_kind("src/auth", "module_page") == "doc"


class TestDecisionDemotionAndRescue:
    """B4: absolute demotion of decisions on non-why queries + window rescue."""

    @pytest.mark.asyncio
    async def test_decision_outscoring_file_page_still_ranks_below(self, setup_mcp):
        # The 0.6 down-weight alone is washed out when the decision score
        # margin exceeds it (0.9 * 0.6 = 0.54 > 0.42). Demotion must be
        # absolute for non-why queries.
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        async def fake_search(query, limit=10):
            return [
                _mk_result("decision:d1", "Cache prompts as SWR", "decision_record", "", 0.9),
                _mk_result(
                    "file_page:src/auth/service.py",
                    "Auth Service",
                    "file_page",
                    "src/auth/service.py",
                    0.42,
                ),
            ]

        mcp_mod._vector_store.search = fake_search
        result = await search_codebase("answer cache invalidation schema version")
        types = [r["page_type"] for r in result["results"]]
        assert types[0] == "file_page"

    @pytest.mark.asyncio
    async def test_all_decision_window_is_rescued_with_file_pages(self, setup_mcp):
        # E6 live failure: 5/5 decision records, zero file pages. The wider
        # re-fetch must surface non-decision pages.
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        decisions = [
            _mk_result(f"decision:d{i}", f"Decision {i}", "decision_record", "", 0.8 - i * 0.01)
            for i in range(20)
        ]
        wide = [*decisions, _mk_result("file_page:src/auth/service.py", "Auth Service", "file_page", "src/auth/service.py", 0.3)]

        async def fake_search(query, limit=10):
            # Narrow window: only decisions. Wide window: includes the file.
            return decisions[:limit] if limit <= 20 else wide[:limit]

        mcp_mod._vector_store.search = fake_search
        result = await search_codebase("answer cache invalidation schema version", limit=5)
        types = [r["page_type"] for r in result["results"]]
        assert "file_page" in types, "rescue must inject non-decision pages"
        assert types[0] == "file_page", "rescued file page ranks above demoted decisions"

    @pytest.mark.asyncio
    async def test_why_query_skips_rescue_and_demotion(self, setup_mcp):
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        async def fake_search(query, limit=10):
            return [
                _mk_result("decision:d1", "Use SQLite", "decision_record", "", 0.9),
                _mk_result(
                    "file_page:src/auth/service.py",
                    "Auth Service",
                    "file_page",
                    "src/auth/service.py",
                    0.42,
                ),
            ]

        mcp_mod._vector_store.search = fake_search
        result = await search_codebase("why did we choose SQLite?")
        assert result["results"][0]["page_type"] == "decision_record"


class TestSymbolTestPenalty:
    """The -5 a symbol takes for living in a test file (#1103)."""

    @staticmethod
    def _score(path: str, language: str = "python") -> float:
        from repowise.core.persistence.models import WikiSymbol
        from repowise.server.mcp_server.tool_search_symbols import _score_symbol

        row = WikiSymbol(
            name="build_index",
            qualified_name="build_index",
            file_path=path,
            language=language,
        )
        # No graph node: symbol nodes never carry `is_test`, so the path rules
        # are what decide here in practice.
        return _score_symbol(row, None, {"build", "index"}, "build_index")

    def test_tests_are_penalised_and_support_is_not(self):
        base = self._score("src/indexing/build.py")
        assert self._score("packages/core/tests/test_build.py") == base - 5.0
        assert self._score("myapp/tests.py") == base - 5.0
        # A fixture factory is often what the query was after.
        assert self._score("packages/core/tests/conftest.py") == base
        assert self._score("packages/core/tests/factories/user.py") == base
        # Production code that merely spells "test".
        assert self._score("src/analysis/missing_test_signal.py") == base

    def test_language_decides_the_ambiguous_spec_dir(self):
        base = self._score("src/indexing/build.py")
        # RSpec for Ruby, a specifications folder for anything else — the same
        # call the traverser made when it stamped the file's flag.
        assert self._score("spec/models/user.rb", language="ruby") == base - 5.0
        assert self._score("spec/openapi/users.py", language="python") == base


class TestSymbolSearch:
    """mode="symbol" / "auto" routing into the structural index (issue #484)."""

    @pytest.mark.asyncio
    async def test_exact_name_returns_symbol_shape(self, setup_mcp):
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("AuthService", mode="symbol")
        assert result["mode"] == "symbol"
        hits = result["results"]
        assert hits, "exact name must resolve to a symbol"
        top = hits[0]
        assert top["type"] == "symbol"
        assert top["symbol_id"] == "src/auth/service.py::AuthService"
        assert top["name"] == "AuthService"
        assert top["kind"] == "class"
        assert top["file"] == "src/auth/service.py"
        assert top["start_line"] == 10
        assert top["end_line"] == 100
        assert top["next"] == "get_symbol"

    @pytest.mark.asyncio
    async def test_auto_routes_bare_identifier_to_symbol(self, setup_mcp):
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("AuthService")
        assert result["mode"] == "symbol"
        assert any(r["symbol_id"] == "src/auth/service.py::AuthService" for r in result["results"])

    @pytest.mark.asyncio
    async def test_camelcase_multitoken_qualified_match(self, setup_mcp):
        # "AuthService login" must surface AuthService.login via token coverage.
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("AuthService login", mode="symbol")
        ids = [r["symbol_id"] for r in result["results"]]
        assert "src/auth/service.py::login" in ids

    @pytest.mark.asyncio
    async def test_symbol_kind_filter(self, setup_mcp):
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("login", mode="symbol", symbol_kind="class")
        # login is a method, not a class — the kind filter removes it.
        assert all(r["kind"] == "class" for r in result["results"])
        assert not any(r["name"] == "login" for r in result["results"])

    @pytest.mark.asyncio
    async def test_no_match_falls_back_to_grep_hint(self, setup_mcp):
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("NonexistentSymbol", mode="symbol")
        assert result["results"] == []
        assert "grep_hint" in result

    @pytest.mark.asyncio
    async def test_excluded_symbol_dropped(self, setup_mcp, monkeypatch):
        # Exclude the auth file at query time; the symbol must not surface.
        import pathspec

        import repowise.server.mcp_server.tool_search_symbols as ss

        spec = pathspec.PathSpec.from_lines("gitwildmatch", ["src/auth/**"])
        monkeypatch.setattr(ss, "_get_exclude_spec", lambda _p: spec)

        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("AuthService", mode="symbol")
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_tombstoned_symbol_dropped(self, setup_mcp, factory):
        # Tombstone the auth service page; its symbols must be filtered out.
        from sqlalchemy import update

        from repowise.core.persistence.models import Page

        async with factory() as s:
            await s.execute(
                update(Page)
                .where(Page.id == "file_page:src/auth/service.py")
                .values(freshness_status="tombstone")
            )
            await s.commit()

        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("AuthService", mode="symbol")
        assert not any(r["file"] == "src/auth/service.py" for r in result["results"])


class TestPathSearch:
    """mode="path" / "auto" routing into file pages."""

    @pytest.mark.asyncio
    async def test_path_query_resolves_file(self, setup_mcp):
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("src/auth/service.py", mode="path")
        assert result["mode"] == "path"
        files = [r["file"] for r in result["results"]]
        assert "src/auth/service.py" in files
        top = result["results"][0]
        assert top["type"] == "file"
        assert top["next"] == "get_context"

    @pytest.mark.asyncio
    async def test_auto_routes_path_shaped_query(self, setup_mcp):
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("src/db/models.py")
        assert result["mode"] == "path"
        assert any(r["file"] == "src/db/models.py" for r in result["results"])


class TestHybridSearch:
    """Mixed natural-language + identifier queries run hybrid."""

    @pytest.mark.asyncio
    async def test_auto_routes_mixed_query_to_hybrid(self, setup_mcp):
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("where is AuthService defined")
        assert result["mode"] == "hybrid"

    @pytest.mark.asyncio
    async def test_hybrid_puts_symbols_first(self, setup_mcp):
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        async def fake_search(query, limit=10):
            return [
                _mk_result(
                    "file_page:src/auth/service.py",
                    "Auth Service",
                    "file_page",
                    "src/auth/service.py",
                    0.42,
                ),
            ]

        mcp_mod._vector_store.search = fake_search
        result = await search_codebase("how does AuthService work", mode="hybrid")
        assert result["results"][0]["type"] == "symbol"

    @pytest.mark.asyncio
    async def test_hybrid_keeps_concept_page_alongside_symbol(self, setup_mcp):
        # A concept page for a DIFFERENT file (not the symbol's own file, which
        # would dedupe out) must survive the merge — hybrid is symbols AND pages.
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        async def fake_search(query, limit=10):
            return [
                _mk_result(
                    "file_page:src/db/models.py",
                    "DB Models",
                    "file_page",
                    "src/db/models.py",
                    0.6,
                ),
            ]

        mcp_mod._vector_store.search = fake_search
        result = await search_codebase("how does AuthService work", mode="hybrid", limit=5)
        types = {r["type"] for r in result["results"]}
        assert "symbol" in types, "symbol hit must be present"
        assert "page" in types, "concept page must survive the merge"


class TestHybridInterleave:
    """The hybrid block-interleave: symbol and concept scores are incomparable,
    so ordering is by block. Default symbols-lead, EXCEPT the score-scale trap -
    a mostly-prose query whose embedded identifier matches no symbol exactly must
    lead with concept pages, or fuzzy symbol hits bury the relevant page."""

    def _sym(self, name, file, score):
        return {"type": "symbol", "name": name, "file": file, "score": score}

    def _page(self, path, rel):
        return {"type": "page", "target_path": path, "relevance_score": rel}

    def test_prose_dominates(self):
        from repowise.server.mcp_server.tool_search import _prose_dominates

        # 6 prose tokens vs 1 identifier -> prose.
        assert _prose_dominates("how does retrieval feed synthesis in get_answer", ["get_answer"])
        # Balanced / symbol-heavy -> not prose-dominant.
        assert not _prose_dominates("compare FooBar and BazQux", ["FooBar", "BazQux"])
        # No embedded identifier -> never prose-dominant (nothing to demote).
        assert not _prose_dominates("plain english question here", [])

    def test_no_exact_prose_query_leads_with_concepts(self):
        from repowise.server.mcp_server.tool_search import _interleave_hybrid

        symbols = [self._sym("get", "a/registry.py", 43.8), self._sym("get", "b/store.py", 43.7)]
        concepts = [self._page("mcp/answer.py", 0.47)]
        out = _interleave_hybrid(
            "how does retrieval feed synthesis in get_answer",
            symbols,
            concepts,
            limit=5,
            exact=False,
        )
        assert out[0]["type"] == "page", "the answer.py page must lead, not fuzzy .get"
        # Nothing dropped - the fuzzy symbols still ride along at the tail.
        assert {s["file"] for s in symbols} <= {r.get("file") for r in out}

    def test_exact_match_keeps_symbols_first(self):
        from repowise.server.mcp_server.tool_search import _interleave_hybrid

        symbols = [self._sym("get_answer", "mcp/answer.py", 143.0)]
        concepts = [self._page("mcp/other.py", 0.5)]
        out = _interleave_hybrid(
            "where is get_answer defined", symbols, concepts, limit=5, exact=True
        )
        assert out[0]["type"] == "symbol"

    def test_symbol_heavy_query_keeps_symbols_first(self):
        from repowise.server.mcp_server.tool_search import _interleave_hybrid

        # Not prose-dominant, so default ordering even without an exact match.
        symbols = [self._sym("FooBar", "x.py", 40.0)]
        concepts = [self._page("y.py", 0.3)]
        out = _interleave_hybrid("compare FooBar BazQux", symbols, concepts, limit=5, exact=False)
        assert out[0]["type"] == "symbol"

    def test_no_concepts_returns_symbols(self):
        from repowise.server.mcp_server.tool_search import _interleave_hybrid

        symbols = [self._sym("get", "a.py", 43.0)]
        out = _interleave_hybrid(
            "how does retrieval feed synthesis in get_answer", symbols, [], limit=5, exact=False
        )
        assert out == symbols


class TestProtectedExactMatches:
    def test_canonical_symbol_id_routes_to_symbol_search(self):
        from repowise.server.mcp_server.tool_search import _resolve_mode

        query = "packages/server/src/repowise/server/mcp_server/tool_search.py::search_codebase"
        assert _resolve_mode(query, "auto") == "symbol"

    def test_canonical_symbol_id_is_stably_protected(self):
        from repowise.server.mcp_server.tool_search import _protect_exact_symbols

        exact_id = "src/auth/service.py::AuthService.run"
        symbols = [
            {"symbol_id": "src/jobs/runner.py::run", "name": "run", "score": 999.0},
            {"symbol_id": exact_id, "name": "run", "score": 1.0},
            {"symbol_id": "src/cli/main.py::main", "name": "main", "score": 500.0},
        ]

        out = _protect_exact_symbols(exact_id, symbols)

        assert out[0]["symbol_id"] == exact_id
        assert out[1:] == [symbols[0], symbols[2]]

    def test_exact_path_is_stably_protected(self):
        from repowise.server.mcp_server.tool_search import _protect_exact_paths

        exact_path = "src/auth/middleware.py"
        files = [
            {"file": "tests/auth/middleware.py", "score": 999.0},
            {"file": exact_path, "score": 1.0},
            {"file": "src/legacy/middleware.py", "score": 500.0},
        ]

        out = _protect_exact_paths(exact_path, files)

        assert out[0]["file"] == exact_path
        assert out[1:] == [files[0], files[2]]


class TestConceptModeUnchanged:
    """Forcing mode="concept" preserves the original semantic behavior."""

    @pytest.mark.asyncio
    async def test_concept_mode_runs_semantic(self, setup_mcp):
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        await mcp_mod._vector_store.embed_and_upsert(
            "file_page:src/auth/service.py",
            "Auth Service — Main authentication service class",
            {
                "title": "Auth Service",
                "page_type": "file_page",
                "target_path": "src/auth/service.py",
            },
        )
        result = await search_codebase("AuthService", mode="concept")
        # Concept mode does not set the structural "mode" routing key.
        assert "results" in result
        assert all(r.get("type") != "symbol" for r in result["results"])
        assert all(
            "_coverage" not in r
            and "_coverage_multiplier" not in r
            and "_confidence_score_factor" not in r
            and "_raw_score" not in r
            for r in result["results"]
        )


class TestIdentifierGrepHint:
    @pytest.mark.asyncio
    async def test_multiword_query_with_identifier_gets_hint(self, setup_mcp):
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("where is _DEFAULT_CO_CHANGE_MIN_COUNT defined")
        assert "grep_hint" in result
        assert "_DEFAULT_CO_CHANGE_MIN_COUNT" in result["grep_hint"]

    @pytest.mark.asyncio
    async def test_camelcase_identifier_gets_hint(self, setup_mcp):
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("how does LanguageRegistry resolve specs")
        assert "grep_hint" in result
        assert "LanguageRegistry" in result["grep_hint"]

    @pytest.mark.asyncio
    async def test_plain_english_query_gets_no_hint(self, setup_mcp):
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("authentication flow for the service")
        assert "grep_hint" not in result


class TestExactMatchSignal:
    """An identifier query says whether any hit is an EXACT symbol match, so
    the agent doesn't anchor on a fuzzy neighbour that ranks first (finding 1)."""

    def test_has_exact_symbol_matches_name(self):
        from repowise.server.mcp_server.tool_search import _has_exact_symbol

        syms = [{"name": "get_answer", "qualified_name": "get_answer"}]
        assert _has_exact_symbol(["get_answer"], syms)
        assert not _has_exact_symbol(["get_answerX"], syms)
        assert not _has_exact_symbol([], syms)
        assert not _has_exact_symbol(["get_answer"], [])

    def test_has_exact_symbol_normalizes_separators(self):
        # An agent's "Class.method" must match a "Class::method" qualified_name.
        from repowise.server.mcp_server.tool_search import _has_exact_symbol

        syms = [{"name": "method", "qualified_name": "Class::method"}]
        assert _has_exact_symbol(["Class.method"], syms)

    def test_identifier_candidates_by_mode(self):
        from repowise.server.mcp_server.tool_search import _identifier_candidates

        assert _identifier_candidates("AuthService", "symbol") == ["AuthService"]
        assert _identifier_candidates("where is AuthService defined", "hybrid") == ["AuthService"]
        assert _identifier_candidates(
            "where does OmissionStore.get expand a reference", "hybrid"
        ) == ["OmissionStore.get"]
        assert _identifier_candidates("for example, e.g. retrieval flow", "hybrid") == []
        assert _identifier_candidates("rate limiting", "concept") == []

    def test_qualified_member_embedded_in_prose_is_protected(self):
        from repowise.server.mcp_server.tool_search import _protect_named_symbols

        exact = {
            "name": "get",
            "qualified_name": "repowise.core.distill.store.OmissionStore.get",
            "score": 1.0,
        }
        symbols = [
            {"name": "get_record", "qualified_name": "OmissionStore::get_record", "score": 99.0},
            exact,
            {"name": "get", "qualified_name": "LanguageRegistry::get", "score": 50.0},
        ]

        out = _protect_named_symbols(["OmissionStore.get"], symbols)

        assert out[0] is exact
        assert out[1:] == [symbols[0], symbols[2]]

    @pytest.mark.asyncio
    async def test_hybrid_scores_symbols_on_the_identifier_not_the_prose(
        self, setup_mcp, monkeypatch
    ):
        # Regression: the symbol scorer ranks on token overlap, so handing it the
        # whole question let prose tokens ("is" -> is_ci, "filter" ->
        # FilterRegistry) outrank the identifier the question asks after. The
        # identifier then never reached _has_exact_symbol and the response
        # claimed an indexed symbol was absent.
        #
        # Asserted on the query the scorer RECEIVES, not on ranking: a fixture
        # small enough to unit-test has no decoys to lose to, so an end-to-end
        # assertion here passes against the bug.
        from repowise.server.mcp_server import search_codebase, tool_search

        seen: list[str] = []
        real = tool_search.search_symbols_single

        async def spy(ctx, query, limit, **kwargs):
            seen.append(query)
            return await real(ctx, query, limit, **kwargs)

        monkeypatch.setattr(tool_search, "search_symbols_single", spy)

        await search_codebase("where is AuthService defined")
        assert seen == ["AuthService"], f"symbol scorer got prose, not the identifier: {seen}"

        # A query carrying no identifier still scores on the raw text.
        seen.clear()
        await search_codebase("AuthService", mode="symbol")
        assert seen == ["AuthService"]

    @pytest.mark.asyncio
    async def test_exact_hit_sets_true_and_no_note(self, setup_mcp):
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("AuthService", mode="symbol")
        assert result["exact_match"] is True
        assert "note" not in result

    @pytest.mark.asyncio
    async def test_fuzzy_only_sets_false_with_note(self, setup_mcp):
        # "AuthServiceXyz" token-overlaps AuthService (a hit) but matches no
        # symbol exactly — the signal must fire even though results are non-empty.
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("AuthServiceXyz", mode="symbol")
        assert result["results"], "fuzzy neighbour should still be returned"
        assert result["exact_match"] is False
        assert "exactly matches" in result.get("note", "")

    @pytest.mark.asyncio
    async def test_concept_query_gets_no_signal(self, setup_mcp):
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("authentication flow for the service")
        assert "exact_match" not in result

    @pytest.mark.asyncio
    async def test_path_search_gets_no_exact_symbol_signal(self, setup_mcp):
        """A path query names no identifier, so there is no symbol to be exact
        about.

        Regression: the response builder computed the query's identifiers into
        ``candidates`` and then rebound the same name with the *file*
        candidates, so this signal's gate became "there are results at all" and
        a path search was told that no symbol matched it.
        """
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("service.py", mode="path")
        assert result["results"], "the path search should have found something"
        assert "exact_match" not in result
        assert "note" not in result

    @pytest.mark.asyncio
    async def test_the_fuzzy_note_quotes_the_query_not_the_file_list(self, setup_mcp):
        """Same regression from the other side: the note names what the caller
        asked after, and the rebind made it name file paths instead."""
        from repowise.server.mcp_server import search_codebase

        result = await search_codebase("AuthServiceXyz", mode="symbol")
        assert "'AuthServiceXyz'" in result["note"]
        assert "'path'" not in result["note"]


class TestFusion:
    """search_codebase fuses FTS + vector via RRF, tagging each hit's sources.

    The prior path ran vector search and fell back to FTS only on zero vector
    results, so a page FTS ranked highly but vector missed never surfaced.
    """

    @pytest.mark.asyncio
    async def test_fts_only_and_vector_only_hits_both_fuse(self, setup_mcp):
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        for path in ("src/both.py", "src/vec.py", "src/fts.py"):
            await _seed_page(f"file_page:{path}", path)

        async def fake_vec(query, limit=10):
            return [
                _mk_result("file_page:src/both.py", "Both", "file_page", "src/both.py", 0.9),
                _mk_result("file_page:src/vec.py", "Vec", "file_page", "src/vec.py", 0.5),
            ]

        async def fake_fts(query, limit=10):
            return [
                _mk_result("file_page:src/both.py", "Both", "file_page", "src/both.py", 4.0),
                _mk_result("file_page:src/fts.py", "Fts", "file_page", "src/fts.py", 3.0),
            ]

        mcp_mod._vector_store.search = fake_vec
        mcp_mod._fts.search = fake_fts

        result = await search_codebase("session cache layer", limit=10)
        by_path = {r["target_path"]: r for r in result["results"]}
        # All three surface — including the FTS-only page the old path dropped.
        assert {"src/both.py", "src/vec.py", "src/fts.py"} <= set(by_path)
        assert by_path["src/vec.py"]["sources"] == ["vector"]
        assert by_path["src/fts.py"]["sources"] == ["fts"]
        assert by_path["src/both.py"]["sources"] == ["fts", "vector"]
        # A page both retrievers rank #1 fuses to the top.
        assert result["results"][0]["target_path"] == "src/both.py"

    @pytest.mark.asyncio
    async def test_vector_miss_falls_through_to_fts(self, setup_mcp):
        """Vector returning nothing must not blank the result — FTS still feeds."""
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        await _seed_page("file_page:src/fts_rescue.py", "src/fts_rescue.py")

        async def empty_vec(query, limit=10):
            return []

        async def fake_fts(query, limit=10):
            return [
                _mk_result(
                    "file_page:src/fts_rescue.py", "Rescue", "file_page", "src/fts_rescue.py", 3.5
                )
            ]

        mcp_mod._vector_store.search = empty_vec
        mcp_mod._fts.search = fake_fts

        result = await search_codebase("session cache layer", limit=10)
        by_path = {r["target_path"]: r for r in result["results"]}
        assert by_path["src/fts_rescue.py"]["sources"] == ["fts"]


class TestSearchCandidates:
    """`candidates`: every slot the caller paid for resolves to an openable file.

    The unit-level rules live in ``test_search_candidates.py``. These drive the
    real tool, because the defect this fixes was not in the path logic — it was
    in which branch of ``search_codebase`` ever called it.
    """

    @pytest.mark.asyncio
    async def test_pages_that_are_not_files_do_not_cost_the_caller_a_slot(self, setup_mcp):
        """A module page and an onboarding page rank, and candidates still names
        two files, reached from below the caller's cut."""
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        await _seed_page("module_page:pkg/cmd/release", "pkg/cmd/release", "module_page")
        await _seed_page(
            "onboarding:onboarding/how_it_works", "onboarding/how_it_works", "onboarding"
        )
        await _seed_page("file_page:pkg/cmd/release/list.go", "pkg/cmd/release/list.go")
        await _seed_page("file_page:pkg/cmd/release/http.go", "pkg/cmd/release/http.go")

        async def fake_search(query, limit=10):
            return [
                _mk_result(
                    "module_page:pkg/cmd/release", "Release", "module_page", "pkg/cmd/release", 0.90
                ),
                _mk_result(
                    "onboarding:onboarding/how_it_works",
                    "Guided Tour",
                    "onboarding",
                    "onboarding/how_it_works",
                    0.80,
                ),
                _mk_result(
                    "file_page:pkg/cmd/release/list.go",
                    "list.go",
                    "file_page",
                    "pkg/cmd/release/list.go",
                    0.70,
                ),
                _mk_result(
                    "file_page:pkg/cmd/release/http.go",
                    "http.go",
                    "file_page",
                    "pkg/cmd/release/http.go",
                    0.60,
                ),
            ]

        mcp_mod._vector_store.search = fake_search
        result = await search_codebase("listing releases in order", limit=10)

        # The module page is still a legitimate ranked result.
        assert "module_page" in [r["page_type"] for r in result["results"]]
        # ...and candidates names only things that can be opened.
        assert result["candidates"] == [
            {"path": "pkg/cmd/release/list.go"},
            {"path": "pkg/cmd/release/http.go"},
        ]

    @pytest.mark.asyncio
    async def test_a_symbol_page_is_served_as_its_file(self, setup_mcp):
        """The half of A14 that never reached this branch.

        ``target_path`` keeps the page id, because callers pipe it into
        get_symbol. ``file`` and ``candidates`` carry the openable path.
        """
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        await _seed_page(
            "symbol_spotlight:api/client.go::HTTP", "api/client.go::HTTP", "symbol_spotlight"
        )

        async def fake_search(query, limit=10):
            return [
                _mk_result(
                    "symbol_spotlight:api/client.go::HTTP",
                    "HTTP",
                    "symbol_spotlight",
                    "api/client.go::HTTP",
                    0.90,
                )
            ]

        mcp_mod._vector_store.search = fake_search
        result = await search_codebase("how are requests issued", limit=10)

        hit = result["results"][0]
        assert hit["target_path"] == "api/client.go"
        assert hit["file"] == "api/client.go"
        assert hit["symbol_id"] == "api/client.go::HTTP"
        assert hit["file"] == "api/client.go"
        assert result["candidates"] == [{"path": "api/client.go"}]

    @pytest.mark.asyncio
    async def test_no_block_when_nothing_openable_was_reached(self, setup_mcp):
        import repowise.server.mcp_server as mcp_mod
        from repowise.server.mcp_server import search_codebase

        await _seed_page("repo_overview:cli", "cli", "repo_overview")

        async def fake_search(query, limit=10):
            return [_mk_result("repo_overview:cli", "Overview", "repo_overview", "cli", 0.90)]

        mcp_mod._vector_store.search = fake_search
        result = await search_codebase("what is this repository", limit=10)
        assert "candidates" not in result
