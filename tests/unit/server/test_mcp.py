"""Unit tests for repowise MCP server tools.

Tests all 8 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.database import init_db
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.core.persistence.models import (
    DeadCodeFinding,
    DecisionRecord,
    GitMetadata,
    GraphEdge,
    GraphNode,
    Page,
    Repository,
    WikiSymbol,
)
from repowise.core.persistence.search import FullTextSearch
from repowise.core.persistence.vector_store import InMemoryVectorStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
async def factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(factory):
    async with factory() as s:
        yield s
        await s.commit()


@pytest.fixture
async def fts(engine):
    f = FullTextSearch(engine)
    await f.ensure_index()
    return f


@pytest.fixture
async def vector_store():
    embedder = MockEmbedder()
    vs = InMemoryVectorStore(embedder=embedder)
    yield vs
    await vs.close()


@pytest.fixture
async def repo_id(session: AsyncSession) -> str:
    """Create a test repository and return its ID."""
    repo = Repository(
        id="repo1",
        name="test-repo",
        url="https://github.com/example/test-repo",
        local_path="/tmp/test-repo",
        default_branch="main",
        settings_json="{}",
        created_at=_NOW,
        updated_at=_NOW,
    )
    session.add(repo)
    await session.flush()
    return repo.id


@pytest.fixture
async def populated_db(session: AsyncSession, repo_id: str) -> str:
    """Populate the database with test data for all MCP tools."""
    rid = repo_id

    # ---- Pages ----
    pages = [
        Page(
            id="repo_overview:test-repo",
            repository_id=rid,
            page_type="repo_overview",
            title="Test Repo Overview",
            content="# Test Repo\n\nA comprehensive test repository.",
            target_path="test-repo",
            source_hash="abc123",
            model_name="mock",
            provider_name="mock",
            generation_level=6,
            confidence=1.0,
            freshness_status="fresh",
            metadata_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Page(
            id="architecture_diagram:test-repo",
            repository_id=rid,
            page_type="architecture_diagram",
            title="Architecture Diagram",
            content="graph TD\n    A[Main] --> B[Auth]\n    A --> C[DB]",
            target_path="test-repo",
            source_hash="abc124",
            model_name="mock",
            provider_name="mock",
            generation_level=6,
            confidence=1.0,
            freshness_status="fresh",
            metadata_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Page(
            id="module_page:src/auth",
            repository_id=rid,
            page_type="module_page",
            title="Auth Module",
            content="# Auth Module\n\nHandles authentication and authorization.",
            target_path="src/auth",
            source_hash="mod1",
            model_name="mock",
            provider_name="mock",
            generation_level=4,
            confidence=0.95,
            freshness_status="fresh",
            metadata_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Page(
            id="module_page:src/db",
            repository_id=rid,
            page_type="module_page",
            title="Database Module",
            content="# Database Module\n\nDatabase access and ORM layer.",
            target_path="src/db",
            source_hash="mod2",
            model_name="mock",
            provider_name="mock",
            generation_level=4,
            confidence=0.90,
            freshness_status="fresh",
            metadata_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Page(
            id="file_page:src/auth/service.py",
            repository_id=rid,
            page_type="file_page",
            title="Auth Service",
            content="# AuthService\n\nMain authentication service class.",
            target_path="src/auth/service.py",
            source_hash="file1",
            model_name="mock",
            provider_name="mock",
            generation_level=2,
            confidence=0.85,
            freshness_status="fresh",
            metadata_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Page(
            id="file_page:src/auth/middleware.py",
            repository_id=rid,
            page_type="file_page",
            title="Auth Middleware",
            content="# Auth Middleware\n\nRequest authentication middleware.",
            target_path="src/auth/middleware.py",
            source_hash="file2",
            model_name="mock",
            provider_name="mock",
            generation_level=2,
            confidence=0.50,
            freshness_status="stale",
            metadata_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Page(
            id="file_page:src/db/models.py",
            repository_id=rid,
            page_type="file_page",
            title="DB Models",
            content="# Database Models\n\nSQLAlchemy ORM models.",
            target_path="src/db/models.py",
            source_hash="file3",
            model_name="mock",
            provider_name="mock",
            generation_level=2,
            confidence=0.40,
            freshness_status="stale",
            metadata_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        ),
    ]
    for p in pages:
        session.add(p)

    # ---- Symbols ----
    symbols = [
        WikiSymbol(
            id="sym1",
            repository_id=rid,
            file_path="src/auth/service.py",
            symbol_id="src/auth/service.py::AuthService",
            name="AuthService",
            qualified_name="auth.service.AuthService",
            kind="class",
            signature="class AuthService",
            start_line=10,
            end_line=100,
            docstring="Main authentication service.",
            visibility="public",
            is_async=False,
            complexity_estimate=15,
            language="python",
            parent_name=None,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        WikiSymbol(
            id="sym2",
            repository_id=rid,
            file_path="src/auth/service.py",
            symbol_id="src/auth/service.py::login",
            name="login",
            qualified_name="auth.service.AuthService.login",
            kind="method",
            signature="async def login(self, username: str, password: str) -> Token",
            start_line=20,
            end_line=40,
            docstring="Authenticate a user.",
            visibility="public",
            is_async=True,
            complexity_estimate=5,
            language="python",
            parent_name="AuthService",
            created_at=_NOW,
            updated_at=_NOW,
        ),
        WikiSymbol(
            id="sym3",
            repository_id=rid,
            file_path="src/db/models.py",
            symbol_id="src/db/models.py::User",
            name="User",
            qualified_name="db.models.User",
            kind="class",
            signature="class User(Base)",
            start_line=5,
            end_line=30,
            docstring="User ORM model.",
            visibility="public",
            is_async=False,
            complexity_estimate=2,
            language="python",
            parent_name=None,
            created_at=_NOW,
            updated_at=_NOW,
        ),
    ]
    for s in symbols:
        session.add(s)

    # ---- Graph Nodes ----
    nodes = [
        GraphNode(
            id="gn1",
            repository_id=rid,
            node_id="src/auth/service.py",
            node_type="file",
            language="python",
            symbol_count=2,
            is_entry_point=True,
            pagerank=0.85,
            betweenness=0.5,
            community_id=1,
            created_at=_NOW,
        ),
        GraphNode(
            id="gn2",
            repository_id=rid,
            node_id="src/auth/middleware.py",
            node_type="file",
            language="python",
            symbol_count=1,
            is_entry_point=False,
            pagerank=0.4,
            betweenness=0.2,
            community_id=1,
            created_at=_NOW,
        ),
        GraphNode(
            id="gn3",
            repository_id=rid,
            node_id="src/db/models.py",
            node_type="file",
            language="python",
            symbol_count=1,
            is_entry_point=False,
            pagerank=0.6,
            betweenness=0.3,
            community_id=2,
            created_at=_NOW,
        ),
    ]
    for n in nodes:
        session.add(n)

    # ---- Graph Edges ----
    edges = [
        GraphEdge(
            id="ge1",
            repository_id=rid,
            source_node_id="src/auth/service.py",
            target_node_id="src/db/models.py",
            imported_names_json='["User"]',
            created_at=_NOW,
        ),
        GraphEdge(
            id="ge2",
            repository_id=rid,
            source_node_id="src/auth/middleware.py",
            target_node_id="src/auth/service.py",
            imported_names_json='["AuthService"]',
            created_at=_NOW,
        ),
    ]
    for e in edges:
        session.add(e)

    # ---- Git Metadata ----
    git_metas = [
        GitMetadata(
            id="gm1",
            repository_id=rid,
            file_path="src/auth/service.py",
            commit_count_total=42,
            commit_count_90d=8,
            commit_count_30d=3,
            first_commit_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            last_commit_at=datetime(2026, 3, 15, tzinfo=timezone.utc),
            primary_owner_name="Alice",
            primary_owner_email="alice@example.com",
            primary_owner_commit_pct=0.65,
            top_authors_json=json.dumps([
                {"name": "Alice", "count": 27},
                {"name": "Bob", "count": 15},
            ]),
            significant_commits_json=json.dumps([
                {"sha": "abc1234", "date": "2026-03-15", "message": "Refactor auth flow", "author": "Alice"},
                {"sha": "def5678", "date": "2026-02-10", "message": "Add JWT support", "author": "Bob"},
            ]),
            co_change_partners_json=json.dumps([
                {"file_path": "src/auth/middleware.py", "count": 5},
                {"file_path": "src/db/models.py", "count": 3},
            ]),
            is_hotspot=True,
            is_stable=False,
            churn_percentile=0.92,
            age_days=443,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        GitMetadata(
            id="gm2",
            repository_id=rid,
            file_path="src/db/models.py",
            commit_count_total=15,
            commit_count_90d=0,
            commit_count_30d=0,
            first_commit_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            last_commit_at=datetime(2025, 9, 1, tzinfo=timezone.utc),
            primary_owner_name="Bob",
            primary_owner_email="bob@example.com",
            primary_owner_commit_pct=0.90,
            top_authors_json=json.dumps([{"name": "Bob", "count": 13}]),
            significant_commits_json=json.dumps([
                {"sha": "111aaa", "date": "2025-09-01", "message": "Add migration helper", "author": "Bob"},
            ]),
            co_change_partners_json=json.dumps([
                {"file_path": "src/auth/service.py", "count": 3},
            ]),
            is_hotspot=False,
            is_stable=True,
            churn_percentile=0.15,
            age_days=443,
            created_at=_NOW,
            updated_at=_NOW,
        ),
    ]
    for g in git_metas:
        session.add(g)

    # ---- Dead Code Findings ----
    findings = [
        DeadCodeFinding(
            id="dc1",
            repository_id=rid,
            kind="unreachable_file",
            file_path="src/legacy/old_auth.py",
            symbol_name=None,
            symbol_kind=None,
            confidence=0.9,
            reason="No imports found; file not referenced by any other module",
            lines=150,
            safe_to_delete=True,
            primary_owner="Alice",
            age_days=365,
            status="open",
            analyzed_at=_NOW,
        ),
        DeadCodeFinding(
            id="dc2",
            repository_id=rid,
            kind="unused_export",
            file_path="src/auth/service.py",
            symbol_name="deprecated_login",
            symbol_kind="function",
            confidence=0.7,
            reason="Exported but no external callers found",
            lines=20,
            safe_to_delete=True,
            primary_owner="Bob",
            age_days=120,
            status="open",
            analyzed_at=_NOW,
        ),
        DeadCodeFinding(
            id="dc3",
            repository_id=rid,
            kind="unused_export",
            file_path="src/db/models.py",
            symbol_name="OldModel",
            symbol_kind="class",
            confidence=0.5,
            reason="Exported but no external callers found",
            lines=40,
            safe_to_delete=False,
            primary_owner="Bob",
            age_days=200,
            status="open",
            analyzed_at=_NOW,
        ),
    ]
    for f in findings:
        session.add(f)

    # ---- Decision Records ----
    decisions = [
        DecisionRecord(
            id="dec1",
            repository_id=rid,
            title="Use JWT for authentication",
            status="proposed",
            context="Need stateless auth for microservices",
            decision="Use JWT tokens for all API authentication",
            rationale="Stateless, scalable, works across services",
            alternatives_json=json.dumps(["Session-based auth", "OAuth2 only"]),
            consequences_json=json.dumps(["Must handle token refresh", "Token size overhead"]),
            affected_files_json=json.dumps(["src/auth/service.py", "src/auth/middleware.py"]),
            affected_modules_json=json.dumps(["src/auth"]),
            tags_json=json.dumps(["auth", "security"]),
            source="readme_mining",
            confidence=0.6,
            staleness_score=0.1,
            created_at=_NOW,
            updated_at=_NOW,
        ),
        DecisionRecord(
            id="dec2",
            repository_id=rid,
            title="SQLAlchemy as ORM",
            status="proposed",
            context="Need an async-compatible ORM for Python",
            decision="Use SQLAlchemy 2.0 with async support",
            rationale="Mature, well-documented, async support in 2.0",
            alternatives_json=json.dumps(["Tortoise ORM", "Django ORM"]),
            consequences_json=json.dumps(["Learning curve for async patterns"]),
            affected_files_json=json.dumps(["src/db/models.py"]),
            affected_modules_json=json.dumps(["src/db"]),
            tags_json=json.dumps(["database"]),
            source="git_archaeology",
            confidence=0.7,
            staleness_score=0.0,
            created_at=_NOW,
            updated_at=_NOW,
        ),
    ]
    for d in decisions:
        session.add(d)

    await session.flush()
    return rid


# ---------------------------------------------------------------------------
# MCP tool tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def setup_mcp(factory, fts, vector_store, populated_db):
    """Configure the MCP module's global state for testing."""
    import repowise.server.mcp_server as mcp_mod

    mcp_mod._session_factory = factory
    mcp_mod._fts = fts
    mcp_mod._vector_store = vector_store
    mcp_mod._decision_store = InMemoryVectorStore(embedder=MockEmbedder())
    mcp_mod._repo_path = "/tmp/test-repo"

    yield populated_db

    # Reset globals
    mcp_mod._session_factory = None
    mcp_mod._fts = None
    mcp_mod._vector_store = None
    mcp_mod._decision_store = None
    mcp_mod._repo_path = None


# ---- Tool 1: get_overview ----


@pytest.mark.asyncio
async def test_get_overview(setup_mcp):
    from repowise.server.mcp_server import get_overview

    result = await get_overview()
    assert result["title"] == "Test Repo Overview"
    assert "comprehensive test" in result["content_md"]
    assert result["architecture_diagram_mermaid"] is not None
    assert "graph TD" in result["architecture_diagram_mermaid"]
    assert len(result["key_modules"]) == 2
    assert any(m["name"] == "Auth Module" for m in result["key_modules"])
    assert "src/auth/service.py" in result["entry_points"]


@pytest.mark.asyncio
async def test_get_overview_with_repo_path(setup_mcp):
    from repowise.server.mcp_server import get_overview

    result = await get_overview(repo="/tmp/test-repo")
    assert result["title"] == "Test Repo Overview"


@pytest.mark.asyncio
async def test_get_overview_repo_not_found(setup_mcp):
    from repowise.server.mcp_server import get_overview

    with pytest.raises(LookupError, match="not found"):
        await get_overview(repo="/nonexistent")


# ---- Tool 2: get_context ----


@pytest.mark.asyncio
async def test_get_context_single_file(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(["src/auth/service.py"])
    targets = result["targets"]
    assert "src/auth/service.py" in targets
    t = targets["src/auth/service.py"]
    assert t["type"] == "file"
    # Docs
    assert t["docs"]["title"] == "Auth Service"
    assert "AuthService" in t["docs"]["content_md"]
    assert len(t["docs"]["symbols"]) == 2
    assert any(s["name"] == "AuthService" for s in t["docs"]["symbols"])
    assert "src/auth/middleware.py" in t["docs"]["imported_by"]
    # Ownership
    assert t["ownership"]["primary_owner"] == "Alice"
    assert t["ownership"]["owner_pct"] == 0.65
    assert t["ownership"]["contributor_count"] == 2
    # Last change
    assert t["last_change"]["author"] == "Alice"
    assert t["last_change"]["days_ago"] == 443
    # Decisions
    assert len(t["decisions"]) >= 1
    assert any(d["title"] == "Use JWT for authentication" for d in t["decisions"])
    # Freshness
    assert t["freshness"]["confidence_score"] == 0.85
    assert t["freshness"]["freshness_status"] == "fresh"
    assert t["freshness"]["is_stale"] is False


@pytest.mark.asyncio
async def test_get_context_single_module(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(["src/auth"])
    targets = result["targets"]
    assert "src/auth" in targets
    t = targets["src/auth"]
    assert t["type"] == "module"
    assert t["docs"]["title"] == "Auth Module"
    assert "authentication" in t["docs"]["content_md"].lower()
    assert len(t["docs"]["files"]) == 2  # service.py and middleware.py
    # Freshness from module page
    assert t["freshness"]["confidence_score"] == 0.95


@pytest.mark.asyncio
async def test_get_context_single_symbol(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(["AuthService"])
    targets = result["targets"]
    assert "AuthService" in targets
    t = targets["AuthService"]
    assert t["type"] == "symbol"
    assert t["docs"]["name"] == "AuthService"
    assert t["docs"]["kind"] == "class"
    assert t["docs"]["signature"] == "class AuthService"
    assert t["docs"]["file_path"] == "src/auth/service.py"
    assert t["docs"]["documentation"]  # Has content from file page


@pytest.mark.asyncio
async def test_get_context_multiple_targets(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(["src/auth/service.py", "src/auth", "AuthService"])
    targets = result["targets"]
    assert len(targets) == 3
    assert targets["src/auth/service.py"]["type"] == "file"
    assert targets["src/auth"]["type"] == "module"
    assert targets["AuthService"]["type"] == "symbol"


@pytest.mark.asyncio
async def test_get_context_include_filter(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(["src/auth/service.py"], include=["docs"])
    t = result["targets"]["src/auth/service.py"]
    assert "docs" in t
    assert "ownership" not in t
    assert "last_change" not in t
    assert "decisions" not in t
    assert "freshness" not in t


@pytest.mark.asyncio
async def test_get_context_not_found(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(["nonexistent_thing_xyz"])
    t = result["targets"]["nonexistent_thing_xyz"]
    assert "error" in t


# ---- Tool 3: get_risk ----


@pytest.mark.asyncio
async def test_get_risk_single_target(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py"])
    targets = result["targets"]
    assert "src/auth/service.py" in targets
    t = targets["src/auth/service.py"]
    assert t["hotspot_score"] == 0.92
    assert t["dependents_count"] >= 1  # middleware imports it
    assert len(t["co_change_partners"]) == 2
    assert t["primary_owner"] == "Alice"
    assert t["owner_pct"] == 0.65
    assert "risk_summary" in t
    assert "hotspot score" in t["risk_summary"]

    # Trend: 30d=3, 90d=8 → baseline_rate=0.083, recent=0.1 → stable
    assert t["trend"] in ("increasing", "stable", "decreasing")

    # Risk type: churn_percentile=0.92, no fix keywords → churn-heavy
    assert t["risk_type"] == "churn-heavy"

    # Impact surface: middleware.py depends on service.py
    assert len(t["impact_surface"]) >= 1
    impact_files = [s["file_path"] for s in t["impact_surface"]]
    assert "src/auth/middleware.py" in impact_files
    # Each entry has pagerank and is_entry_point
    for s in t["impact_surface"]:
        assert "pagerank" in s
        assert "is_entry_point" in s


@pytest.mark.asyncio
async def test_get_risk_multiple_targets(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py", "src/db/models.py"])
    targets = result["targets"]
    assert len(targets) == 2
    assert "global_hotspots" in result
    # Both targets should have trend and risk_type
    for t in targets.values():
        assert "trend" in t
        assert "risk_type" in t


@pytest.mark.asyncio
async def test_get_risk_global_hotspots_exclude_targets(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py"])
    # service.py is a hotspot but should NOT appear in global_hotspots
    for h in result["global_hotspots"]:
        assert h["file_path"] != "src/auth/service.py"


@pytest.mark.asyncio
async def test_get_risk_no_git_metadata(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/middleware.py"])
    t = result["targets"]["src/auth/middleware.py"]
    assert t["hotspot_score"] == 0.0  # No git metadata for this file
    assert t["trend"] == "unknown"
    assert "risk_summary" in t
    # Impact surface and risk_type still computed from graph data
    assert "risk_type" in t
    assert "impact_surface" in t


@pytest.mark.asyncio
async def test_get_risk_stable_file(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/db/models.py"])
    t = result["targets"]["src/db/models.py"]
    # 0 commits in 30d and 90d → stable
    assert t["trend"] == "stable"
    # churn_percentile=0.15, dep_count=1, no fix keywords → stable
    assert t["risk_type"] == "stable"


# ---- Tool 4: get_why ----


@pytest.mark.asyncio
async def test_get_why_natural_language(setup_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why("why is JWT used for authentication")
    assert result["mode"] == "search"
    assert result["query"] == "why is JWT used for authentication"
    assert len(result["decisions"]) >= 1
    assert any("JWT" in d["title"] for d in result["decisions"])


@pytest.mark.asyncio
async def test_get_why_file_path(setup_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why("src/auth/service.py")
    assert result["mode"] == "path"
    assert result["path"] == "src/auth/service.py"
    assert len(result["decisions"]) >= 1
    assert any(d["title"] == "Use JWT for authentication" for d in result["decisions"])

    # Origin story
    origin = result["origin_story"]
    assert origin["available"] is True
    assert origin["primary_author"] == "Alice"
    assert origin["total_commits"] == 42
    assert len(origin["key_commits"]) >= 1
    assert len(origin["contributors"]) >= 1
    assert "Alice" in origin["summary"]

    # Alignment — dec1 is "proposed", both service.py and middleware.py share it
    alignment = result["alignment"]
    assert alignment["score"] in ("high", "medium", "low", "none")
    assert alignment["governing_count"] >= 1
    assert "explanation" in alignment


@pytest.mark.asyncio
async def test_get_why_file_path_commit_decision_linkage(setup_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why("src/auth/service.py")
    origin = result["origin_story"]

    # "Add JWT support" commit should link to "Use JWT for authentication" decision
    # because "JWT" appears in both the commit message and decision title
    linked = origin["linked_decisions"]
    assert len(linked) >= 1
    jwt_decision = next((d for d in linked if d["title"] == "Use JWT for authentication"), None)
    assert jwt_decision is not None
    assert len(jwt_decision["evidence_commits"]) >= 1
    ec = jwt_decision["evidence_commits"][0]
    assert "JWT" in ec["message"] or "jwt" in ec["message"].lower()
    assert "matching_keywords" in ec


@pytest.mark.asyncio
async def test_get_why_natural_language_with_targets(setup_mcp):
    from repowise.server.mcp_server import get_why

    # Search with targets — decisions governing service.py should be boosted
    result = await get_why(
        "authentication approach",
        targets=["src/auth/service.py"],
    )
    assert result["mode"] == "search"
    assert len(result["decisions"]) >= 1

    # target_context should be present
    assert "target_context" in result
    ctx = result["target_context"]["src/auth/service.py"]
    assert len(ctx["governing_decisions"]) >= 1
    assert ctx["origin"]["available"] is True
    assert ctx["origin"]["primary_author"] == "Alice"


@pytest.mark.asyncio
async def test_get_why_expanded_keyword_search(setup_mcp):
    from repowise.server.mcp_server import get_why

    # Search for "security" — should match via tags_json on dec1
    result = await get_why("security")
    assert result["mode"] == "search"
    # dec1 has tags=["auth", "security"], should be found
    assert len(result["decisions"]) >= 1
    assert any(d.get("title") == "Use JWT for authentication" for d in result["decisions"])


@pytest.mark.asyncio
async def test_get_why_file_no_git_metadata(setup_mcp):
    from repowise.server.mcp_server import get_why

    # middleware.py has no GitMetadata in the fixture
    result = await get_why("src/auth/middleware.py")
    assert result["mode"] == "path"
    origin = result["origin_story"]
    assert origin["available"] is False
    assert "No git history" in origin["summary"]

    # But it still has decisions (dec1 affects middleware.py)
    assert len(result["decisions"]) >= 1
    alignment = result["alignment"]
    assert alignment["governing_count"] >= 1


@pytest.mark.asyncio
async def test_get_why_file_ungoverned(setup_mcp):
    from repowise.server.mcp_server import get_why

    # Use a path that has no decisions — triggers git archaeology fallback
    result = await get_why("src/other/utils.py")
    assert result["mode"] == "path"
    assert result["alignment"]["score"] == "none"
    assert "ungoverned" in result["alignment"]["explanation"]

    # Git archaeology fallback should be triggered
    assert "git_archaeology" in result
    arch = result["git_archaeology"]
    assert arch["triggered"] is True
    assert "summary" in arch
    assert "file_commits" in arch
    assert "cross_references" in arch
    assert "git_log" in arch


@pytest.mark.asyncio
async def test_get_why_fallback_with_cross_references(setup_mcp):
    from repowise.server.mcp_server import get_why

    # src/auth/service.py has git metadata with commits mentioning "auth"
    # Query a nonexistent auth file — cross-references should find commits
    # from service.py that mention "auth" terms
    result = await get_why("src/auth/new_handler.py")
    assert result["mode"] == "path"
    assert len(result["decisions"]) == 0  # No decisions for this file

    arch = result["git_archaeology"]
    assert arch["triggered"] is True
    # Cross-references may find commits from service.py whose messages
    # contain "auth" (matching the path stem "new_handler" won't match,
    # but the file_commits will still be empty since no git metadata exists)
    assert isinstance(arch["cross_references"], list)


@pytest.mark.asyncio
async def test_get_why_targets_fallback(setup_mcp):
    from repowise.server.mcp_server import get_why

    # Search with a target that has no governing decisions
    result = await get_why(
        "why does this exist",
        targets=["src/other/unknown.py"],
    )
    assert result["mode"] == "search"
    ctx = result["target_context"]["src/other/unknown.py"]
    assert len(ctx["governing_decisions"]) == 0
    # Fallback should trigger
    assert "git_archaeology" in ctx
    assert ctx["git_archaeology"]["triggered"] is True


@pytest.mark.asyncio
async def test_get_why_no_args(setup_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why()
    assert result["mode"] == "health"
    assert "summary" in result
    assert "counts" in result
    assert "proposed_awaiting_review" in result
    assert "ungoverned_hotspots" in result


@pytest.mark.asyncio
async def test_get_why_module_path(setup_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why("src/db")
    assert result["mode"] == "path"
    assert len(result["decisions"]) >= 1
    assert any(d["title"] == "SQLAlchemy as ORM" for d in result["decisions"])


# ---- Tool 5: search_codebase ----


@pytest.mark.asyncio
async def test_search_codebase(setup_mcp):
    from repowise.server.mcp_server import search_codebase

    # Index pages in the MCP module's vector store (which is the InMemoryVectorStore)
    import repowise.server.mcp_server as mcp_mod

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


# ---- Tool 6: get_architecture_diagram ----


@pytest.mark.asyncio
async def test_get_architecture_diagram_repo(setup_mcp):
    from repowise.server.mcp_server import get_architecture_diagram

    result = await get_architecture_diagram(scope="repo")
    assert result["diagram_type"] in ("flowchart", "auto")
    assert "mermaid_syntax" in result
    assert "graph TD" in result["mermaid_syntax"]


@pytest.mark.asyncio
async def test_get_architecture_diagram_module(setup_mcp):
    from repowise.server.mcp_server import get_architecture_diagram

    result = await get_architecture_diagram(scope="module", path="src/auth")
    assert "mermaid_syntax" in result
    assert result["description"]


# ---- Tool 7: get_dependency_path ----


@pytest.mark.asyncio
async def test_get_dependency_path(setup_mcp):
    from repowise.server.mcp_server import get_dependency_path

    result = await get_dependency_path("src/auth/service.py", "src/db/models.py")
    assert result["distance"] == 1
    assert len(result["path"]) == 2


@pytest.mark.asyncio
async def test_get_dependency_path_multi_hop(setup_mcp):
    from repowise.server.mcp_server import get_dependency_path

    result = await get_dependency_path("src/auth/middleware.py", "src/db/models.py")
    assert result["distance"] == 2
    assert len(result["path"]) == 3


@pytest.mark.asyncio
async def test_get_dependency_path_no_path(setup_mcp):
    from repowise.server.mcp_server import get_dependency_path

    # Reverse direction — no path from models to middleware
    result = await get_dependency_path("src/db/models.py", "src/auth/middleware.py")
    assert result["distance"] == -1
    assert result["path"] == []

    # Visual context should be present
    ctx = result["visual_context"]
    assert ctx is not None

    # Reverse path exists (middleware -> service -> models)
    assert ctx["reverse_path"]["exists"] is True

    # Nearest common ancestor should be service.py (connects both via undirected)
    ancestors = ctx["nearest_common_ancestors"]
    assert len(ancestors) >= 1
    assert ancestors[0]["node"] == "src/auth/service.py"

    # Community analysis — models is community 2, middleware is community 1
    assert ctx["community"]["same_community"] is False

    # Not disconnected — they are reachable in undirected graph
    assert ctx["disconnected"] is False
    assert "suggestion" in ctx


@pytest.mark.asyncio
async def test_get_dependency_path_node_not_found(setup_mcp):
    from repowise.server.mcp_server import get_dependency_path

    result = await get_dependency_path("nonexistent.py", "src/auth/service.py")
    assert result["distance"] == -1
    assert result["path"] == []
    assert "not found" in result["explanation"]


# ---- Tool 8: get_dead_code ----


@pytest.mark.asyncio
async def test_get_dead_code(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code()
    assert result["summary"]["total_findings"] == 3
    assert result["summary"]["safe_to_delete_count"] == 2

    # Tiered structure: dc1 (0.9) in high, dc2 (0.7) + dc3 (0.5) in medium
    tiers = result["tiers"]
    assert tiers["high"]["count"] == 1
    assert tiers["medium"]["count"] == 2
    assert tiers["low"]["count"] == 0

    # High tier findings sorted by confidence desc
    high_findings = tiers["high"]["findings"]
    assert high_findings[0]["confidence"] >= 0.8

    # Impact estimate present
    assert result["impact"]["total_lines_reclaimable"] > 0


@pytest.mark.asyncio
async def test_get_dead_code_safe_only(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(safe_only=True)
    for tier_data in result["tiers"].values():
        for f in tier_data["findings"]:
            assert f["safe_to_delete"] is True


@pytest.mark.asyncio
async def test_get_dead_code_by_kind(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(kind="unreachable_file", min_confidence=0.0)
    for tier_data in result["tiers"].values():
        for f in tier_data["findings"]:
            assert f["kind"] == "unreachable_file"


@pytest.mark.asyncio
async def test_get_dead_code_low_confidence(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(min_confidence=0.0)
    total = sum(t["count"] for t in result["tiers"].values())
    assert total == 3  # All 3 findings included


@pytest.mark.asyncio
async def test_get_dead_code_tier_filter(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(tier="high")
    assert "high" in result["tiers"]
    assert "medium" not in result["tiers"]
    assert "low" not in result["tiers"]
    assert result["tiers"]["high"]["count"] == 1


@pytest.mark.asyncio
async def test_get_dead_code_group_by_directory(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(group_by="directory", min_confidence=0.0)
    assert "by_directory" in result
    dirs = result["by_directory"]
    assert len(dirs) >= 1
    # Each dir entry has count, lines, safe_count
    for d in dirs:
        assert "directory" in d
        assert "count" in d
        assert "lines" in d


@pytest.mark.asyncio
async def test_get_dead_code_group_by_owner(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(group_by="owner", min_confidence=0.0)
    assert "by_owner" in result
    owners = result["by_owner"]
    assert len(owners) >= 1
    owner_names = [o["owner"] for o in owners]
    assert "Bob" in owner_names  # Bob owns dc2 + dc3


# ---- MCP config generation ----


def test_generate_mcp_config():
    from pathlib import Path
    from repowise.cli.mcp_config import generate_mcp_config

    config = generate_mcp_config(Path("/tmp/test-repo"))
    assert "mcpServers" in config
    assert "repowise" in config["mcpServers"]
    server = config["mcpServers"]["repowise"]
    assert server["command"] == "repowise"
    assert "mcp" in server["args"]
    assert "stdio" in server["args"]


def test_format_setup_instructions():
    from pathlib import Path
    from repowise.cli.mcp_config import format_setup_instructions

    instructions = format_setup_instructions(Path("/tmp/test-repo"))
    assert "Claude Code" in instructions
    assert "Cursor" in instructions
    assert "Cline" in instructions
    assert "repowise" in instructions
