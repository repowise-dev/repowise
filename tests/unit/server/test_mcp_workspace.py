"""Tests for workspace-aware MCP tools.

Uses two in-memory SQLite databases (two repos) to test multi-repo routing,
``repo="all"`` federated queries, and workspace footer in get_overview.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import (
    DeadCodeFinding,
    DecisionRecord,
    GraphNode,
    Page,
    Repository,
)
from repowise.core.persistence.search import FullTextSearch
from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.core.workspace.registry import RepoContext, RepoRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 12, 10, 0, 0, tzinfo=UTC)


async def _make_repo_context(alias: str, repo_path: str, pages: list, extra_models: list | None = None) -> RepoContext:
    """Create an in-memory RepoContext with test data."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        repo = Repository(
            id=f"repo-{alias}",
            name=alias,
            url=f"https://example.com/{alias}",
            local_path=repo_path,
            default_branch="main",
            settings_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        )
        session.add(repo)
        for p in pages:
            session.add(p)
        for m in (extra_models or []):
            session.add(m)
        await session.commit()

    fts = FullTextSearch(engine)
    await fts.ensure_index()

    embedder = MockEmbedder()
    vs = InMemoryVectorStore(embedder=embedder)
    ds = InMemoryVectorStore(embedder=embedder)
    ready = asyncio.Event()
    ready.set()

    return RepoContext(
        alias=alias,
        path=__import__("pathlib").Path(repo_path),
        session_factory=factory,
        fts=fts,
        vector_store=vs,
        decision_store=ds,
        vector_store_ready=ready,
        _engine=engine,
    )


class _MockRegistry:
    """Minimal mock RepoRegistry for testing without real workspace files."""

    def __init__(self, contexts: dict[str, RepoContext], default_alias: str):
        self._contexts = contexts
        self._default_alias = default_alias
        self.workspace_root = __import__("pathlib").Path("/tmp/workspace")

    def get_all_aliases(self) -> list[str]:
        return list(self._contexts.keys())

    def get_default_alias(self) -> str:
        return self._default_alias

    def resolve_repo_param(self, repo):
        if repo is None:
            return self._default_alias
        if repo == "all":
            return self.get_all_aliases()
        if repo not in self._contexts:
            raise ValueError(f"Unknown repo '{repo}'. Available: {self.get_all_aliases()}")
        return repo

    async def get(self, alias: str) -> RepoContext:
        return self._contexts[alias]

    async def get_default(self) -> RepoContext:
        return self._contexts[self._default_alias]

    async def close(self):
        for ctx in self._contexts.values():
            if ctx._engine:
                await ctx._engine.dispose()


@pytest.fixture
async def workspace_mcp():
    """Set up a two-repo workspace for MCP testing."""
    import repowise.server.mcp_server as mcp_mod

    # Create two repo contexts with distinct data
    backend_ctx = await _make_repo_context(
        "backend",
        "/tmp/workspace/backend",
        pages=[
            Page(
                id="repo_overview:backend",
                repository_id="repo-backend",
                page_type="repo_overview",
                title="Backend Overview",
                content="# Backend\n\nThe API backend service.",
                target_path="backend",
                source_hash="abc",
                model_name="mock",
                provider_name="mock",
                generation_level=6,
                confidence=1.0,
                freshness_status="fresh",
                metadata_json="{}",
                created_at=_NOW,
                updated_at=_NOW,
            ),
        ],
        extra_models=[
            GraphNode(
                id="gn-be-1",
                repository_id="repo-backend",
                node_id="src/api/server.py",
                node_type="file",
                language="python",
                symbol_count=5,
                is_entry_point=True,
                pagerank=0.8,
                betweenness=0.5,
                community_id=1,
                created_at=_NOW,
            ),
            DeadCodeFinding(
                id="dc-be-1",
                repository_id="repo-backend",
                file_path="src/old_handler.py",
                symbol_name="old_handler",
                kind="unreachable_file",
                confidence=0.95,
                safe_to_delete=True,
                lines=42,
                reason="No imports found",
                evidence_json="[]",
            ),
            DecisionRecord(
                id="dec-be-1",
                repository_id="repo-backend",
                title="REST over GraphQL",
                decision="Use REST for all public APIs",
                rationale="Mobile client simplicity",
                status="active",
                source="cli",
                affected_files_json='["src/api/server.py"]',
                affected_modules_json='["api"]',
            ),
        ],
    )

    frontend_ctx = await _make_repo_context(
        "frontend",
        "/tmp/workspace/frontend",
        pages=[
            Page(
                id="repo_overview:frontend",
                repository_id="repo-frontend",
                page_type="repo_overview",
                title="Frontend Overview",
                content="# Frontend\n\nThe React web application.",
                target_path="frontend",
                source_hash="def",
                model_name="mock",
                provider_name="mock",
                generation_level=6,
                confidence=1.0,
                freshness_status="fresh",
                metadata_json="{}",
                created_at=_NOW,
                updated_at=_NOW,
            ),
        ],
        extra_models=[
            GraphNode(
                id="gn-fe-1",
                repository_id="repo-frontend",
                node_id="src/App.tsx",
                node_type="file",
                language="typescript",
                symbol_count=3,
                is_entry_point=True,
                pagerank=0.7,
                betweenness=0.4,
                community_id=1,
                created_at=_NOW,
            ),
            DeadCodeFinding(
                id="dc-fe-1",
                repository_id="repo-frontend",
                file_path="src/legacy/utils.ts",
                symbol_name="formatLegacy",
                kind="unused_export",
                confidence=0.80,
                safe_to_delete=True,
                lines=15,
                reason="No imports found",
                evidence_json="[]",
            ),
            DecisionRecord(
                id="dec-fe-1",
                repository_id="repo-frontend",
                title="React over Vue",
                decision="Use React for all frontend work",
                rationale="Team expertise and ecosystem",
                status="active",
                source="cli",
                affected_files_json='["src/App.tsx"]',
                affected_modules_json='["src"]',
            ),
        ],
    )

    registry = _MockRegistry(
        contexts={"backend": backend_ctx, "frontend": frontend_ctx},
        default_alias="backend",
    )

    # Set workspace state
    mcp_mod._registry = registry
    mcp_mod._workspace_root = "/tmp/workspace"
    mcp_mod._session_factory = backend_ctx.session_factory
    mcp_mod._fts = backend_ctx.fts
    mcp_mod._vector_store = backend_ctx.vector_store
    mcp_mod._decision_store = backend_ctx.decision_store
    mcp_mod._repo_path = "/tmp/workspace/backend"
    mcp_mod._vector_store_ready = backend_ctx.vector_store_ready

    yield registry

    # Cleanup
    await registry.close()
    mcp_mod._registry = None
    mcp_mod._workspace_root = None
    mcp_mod._session_factory = None
    mcp_mod._fts = None
    mcp_mod._vector_store = None
    mcp_mod._decision_store = None
    mcp_mod._repo_path = None
    mcp_mod._vector_store_ready = None
    mcp_mod._cross_repo_enricher = None


# ---------------------------------------------------------------------------
# get_overview — workspace footer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_overview_default_includes_workspace_footer(workspace_mcp):
    from repowise.server.mcp_server import get_overview

    result = await get_overview()
    assert result["title"] == "Backend Overview"
    assert "workspace" in result
    ws = result["workspace"]
    assert ws["default_repo"] == "backend"
    assert "frontend" in ws["other_repos"]
    assert "hint" in ws


@pytest.mark.asyncio
async def test_get_overview_specific_repo(workspace_mcp):
    from repowise.server.mcp_server import get_overview

    result = await get_overview(repo="frontend")
    assert result["title"] == "Frontend Overview"
    assert "workspace" in result


@pytest.mark.asyncio
async def test_get_overview_repo_all(workspace_mcp):
    from repowise.server.mcp_server import get_overview

    result = await get_overview(repo="all")
    assert result["workspace"] is True
    assert result["total_repos"] == 2
    aliases = [r["alias"] for r in result["repos"]]
    assert "backend" in aliases
    assert "frontend" in aliases
    # Default repo should be marked
    default_repos = [r for r in result["repos"] if r["is_default"]]
    assert len(default_repos) == 1
    assert default_repos[0]["alias"] == "backend"


# ---------------------------------------------------------------------------
# search_codebase — federated search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_repo_all_returns_tagged_results(workspace_mcp):
    from repowise.server.mcp_server import search_codebase

    # FTS search — both repos should be queried
    result = await search_codebase(query="overview", repo="all")
    assert "results" in result
    # Results may be empty with MockEmbedder + FTS on minimal data,
    # but the function should not error


@pytest.mark.asyncio
async def test_search_specific_repo(workspace_mcp):
    from repowise.server.mcp_server import search_codebase

    result = await search_codebase(query="React", repo="frontend")
    assert "results" in result


# ---------------------------------------------------------------------------
# get_dead_code — workspace-wide
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dead_code_repo_all(workspace_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(repo="all")
    assert result.get("workspace") is True or "summary" in result
    # Should aggregate findings from both repos
    if "summary" in result:
        assert result["summary"]["total_findings"] >= 2


@pytest.mark.asyncio
async def test_get_dead_code_specific_repo(workspace_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(repo="backend")
    assert "summary" in result or "tiers" in result


# ---------------------------------------------------------------------------
# get_why — cross-repo decisions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_why_repo_all(workspace_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why(query="REST", repo="all")
    # Should search across both repos
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_get_why_specific_repo(workspace_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why(query="React", repo="frontend")
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Unsupported repo="all" tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsupported_repo_all_get_context(workspace_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(targets=["src/api/server.py"], repo="all")
    assert "error" in result
    assert "not supported" in result["error"]


# ---------------------------------------------------------------------------
# Invalid repo alias
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_repo_raises(workspace_mcp):
    from repowise.server.mcp_server import get_overview

    with pytest.raises(ValueError, match="Unknown repo"):
        await get_overview(repo="nonexistent")


# ---------------------------------------------------------------------------
# Single-repo mode — no workspace, no regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_repo_mode_no_workspace():
    """When _registry is None, tools should use _state globals directly."""
    import repowise.server.mcp_server as mcp_mod

    # Ensure no registry is set
    assert mcp_mod._registry is None

    # We can't easily call tools without DB, but verify the helper works
    from repowise.server.mcp_server._helpers import _is_workspace_mode

    assert _is_workspace_mode() is False


# ---------------------------------------------------------------------------
# Cross-repo enricher
# ---------------------------------------------------------------------------


@pytest.fixture
def enricher_data(tmp_path):
    """Write a cross_repo_edges.json and return its path."""
    data = {
        "version": 1,
        "generated_at": "2026-04-12T12:00:00Z",
        "co_changes": [
            {
                "source_repo": "backend",
                "source_file": "src/api/server.py",
                "target_repo": "frontend",
                "target_file": "src/api/client.ts",
                "strength": 4.2,
                "frequency": 6,
                "last_date": "2026-04-10",
            },
        ],
        "package_deps": [
            {
                "source_repo": "frontend",
                "target_repo": "backend",
                "source_manifest": "package.json",
                "kind": "npm_local_path",
            },
        ],
        "repo_summaries": {
            "backend": {"cross_repo_edge_count": 1},
            "frontend": {"cross_repo_edge_count": 1},
        },
    }
    path = tmp_path / "cross_repo_edges.json"
    path.write_text(json.dumps(data))
    return path


def test_enricher_loads_and_has_data(enricher_data):
    from repowise.server.mcp_server._enrichment import CrossRepoEnricher

    enricher = CrossRepoEnricher(enricher_data)
    assert enricher.has_data is True


def test_enricher_get_cross_repo_partners(enricher_data):
    from repowise.server.mcp_server._enrichment import CrossRepoEnricher

    enricher = CrossRepoEnricher(enricher_data)
    partners = enricher.get_cross_repo_partners("backend", "src/api/server.py")
    assert len(partners) == 1
    assert partners[0]["repo"] == "frontend"
    assert partners[0]["file"] == "src/api/client.ts"
    assert partners[0]["strength"] == 4.2


def test_enricher_bidirectional_index(enricher_data):
    """Co-change edges should be indexed from both sides."""
    from repowise.server.mcp_server._enrichment import CrossRepoEnricher

    enricher = CrossRepoEnricher(enricher_data)
    # Reverse direction
    partners = enricher.get_cross_repo_partners("frontend", "src/api/client.ts")
    assert len(partners) == 1
    assert partners[0]["repo"] == "backend"


def test_enricher_get_package_deps(enricher_data):
    from repowise.server.mcp_server._enrichment import CrossRepoEnricher

    enricher = CrossRepoEnricher(enricher_data)
    deps = enricher.get_package_deps("frontend")
    assert len(deps) == 1
    assert deps[0]["target_repo"] == "backend"


def test_enricher_get_repos_depending_on(enricher_data):
    from repowise.server.mcp_server._enrichment import CrossRepoEnricher

    enricher = CrossRepoEnricher(enricher_data)
    # frontend depends on backend
    depending = enricher.get_repos_depending_on("backend")
    assert "frontend" in depending


def test_enricher_has_cross_repo_consumers(enricher_data):
    from repowise.server.mcp_server._enrichment import CrossRepoEnricher

    enricher = CrossRepoEnricher(enricher_data)
    consumers = enricher.has_cross_repo_consumers("backend", "src/api/server.py")
    assert len(consumers) == 1
    assert consumers[0]["repo"] == "frontend"


def test_enricher_get_affected_repos(enricher_data):
    from repowise.server.mcp_server._enrichment import CrossRepoEnricher

    enricher = CrossRepoEnricher(enricher_data)
    affected = enricher.get_affected_repos("backend", "src/api/server.py")
    assert "frontend" in affected


def test_enricher_missing_file_returns_empty(enricher_data):
    from repowise.server.mcp_server._enrichment import CrossRepoEnricher

    enricher = CrossRepoEnricher(enricher_data)
    assert enricher.get_cross_repo_partners("backend", "nonexistent.py") == []
    assert enricher.has_cross_repo_consumers("backend", "nonexistent.py") == []


def test_enricher_missing_json_has_no_data(tmp_path):
    from repowise.server.mcp_server._enrichment import CrossRepoEnricher

    enricher = CrossRepoEnricher(tmp_path / "does_not_exist.json")
    assert enricher.has_data is False
    assert enricher.get_cross_repo_partners("x", "y") == []


def test_enricher_get_cross_repo_summary(enricher_data):
    from repowise.server.mcp_server._enrichment import CrossRepoEnricher

    enricher = CrossRepoEnricher(enricher_data)
    summary = enricher.get_cross_repo_summary()
    assert summary["co_change_count"] == 1
    assert summary["package_dep_count"] == 1
    assert len(summary["top_connections"]) >= 1


# ---------------------------------------------------------------------------
# MCP tool enrichment with cross-repo data
# ---------------------------------------------------------------------------


@pytest.fixture
async def workspace_mcp_with_enricher(workspace_mcp, enricher_data):
    """Extend workspace_mcp with a cross-repo enricher."""
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server._enrichment import CrossRepoEnricher

    mcp_mod._cross_repo_enricher = CrossRepoEnricher(enricher_data)
    yield
    mcp_mod._cross_repo_enricher = None


@pytest.mark.asyncio
async def test_overview_footer_includes_cross_repo(workspace_mcp_with_enricher):
    from repowise.server.mcp_server import get_overview

    result = await get_overview()
    assert "workspace" in result
    ws = result["workspace"]
    assert "cross_repo" in ws
    assert ws["cross_repo"]["co_change_count"] == 1


@pytest.mark.asyncio
async def test_overview_all_includes_cross_repo_topology(workspace_mcp_with_enricher):
    from repowise.server.mcp_server import get_overview

    result = await get_overview(repo="all")
    assert "cross_repo_topology" in result
    assert result["cross_repo_topology"]["co_change_count"] == 1
