"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.database import init_db
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
from repowise.core.providers.embedding.base import MockEmbedder

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


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
            first_commit_at=datetime(2025, 1, 1, tzinfo=UTC),
            last_commit_at=datetime(2026, 3, 15, tzinfo=UTC),
            primary_owner_name="Alice",
            primary_owner_email="alice@example.com",
            primary_owner_commit_pct=0.65,
            top_authors_json=json.dumps(
                [
                    {"name": "Alice", "count": 27},
                    {"name": "Bob", "count": 15},
                ]
            ),
            significant_commits_json=json.dumps(
                [
                    {
                        "sha": "abc1234",
                        "date": "2026-03-15",
                        "message": "Refactor auth flow",
                        "author": "Alice",
                    },
                    {
                        "sha": "def5678",
                        "date": "2026-02-10",
                        "message": "Add JWT support",
                        "author": "Bob",
                    },
                ]
            ),
            co_change_partners_json=json.dumps(
                [
                    {"file_path": "src/auth/middleware.py", "count": 5},
                    {"file_path": "src/db/models.py", "count": 3},
                ]
            ),
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
            first_commit_at=datetime(2025, 1, 1, tzinfo=UTC),
            last_commit_at=datetime(2025, 9, 1, tzinfo=UTC),
            primary_owner_name="Bob",
            primary_owner_email="bob@example.com",
            primary_owner_commit_pct=0.90,
            top_authors_json=json.dumps([{"name": "Bob", "count": 13}]),
            significant_commits_json=json.dumps(
                [
                    {
                        "sha": "111aaa",
                        "date": "2025-09-01",
                        "message": "Add migration helper",
                        "author": "Bob",
                    },
                ]
            ),
            co_change_partners_json=json.dumps(
                [
                    {"file_path": "src/auth/service.py", "count": 3},
                ]
            ),
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

    # Reset globals (including workspace state)
    mcp_mod._session_factory = None
    mcp_mod._fts = None
    mcp_mod._vector_store = None
    mcp_mod._decision_store = None
    mcp_mod._repo_path = None
    mcp_mod._registry = None
    mcp_mod._workspace_root = None
    mcp_mod._embedder_status = None


@pytest.fixture
async def health_data(session: AsyncSession, populated_db: str) -> str:
    """Seed health_findings + health_file_metrics for the existing repo."""
    from repowise.core.persistence.crud import (
        save_health_findings,
        save_health_metrics,
    )

    rid = populated_db
    await save_health_metrics(
        session,
        rid,
        [
            {
                "file_path": "src/auth/service.py",
                "score": 4.5,
                "max_ccn": 15,
                "max_nesting": 5,
                "nloc": 200,
                "has_test_file": False,
                "module": "auth",
                "defect_score": 4.5,
                "maintainability_score": 6.0,
                "performance_score": 9.0,
            },
            {
                "file_path": "src/db/models.py",
                "score": 8.5,
                "max_ccn": 4,
                "max_nesting": 2,
                "nloc": 50,
                "has_test_file": True,
                "module": "db",
                "defect_score": 8.5,
                "maintainability_score": 9.0,
                "performance_score": 10.0,
            },
        ],
    )
    await save_health_findings(
        session,
        rid,
        [
            {
                "file_path": "src/auth/service.py",
                "biomarker_type": "complex_method",
                "severity": "high",
                "function_name": "authenticate",
                "line_start": 10,
                "line_end": 80,
                "details": {"ccn": 15, "cognitive": 30, "nloc": 70},
                "health_impact": 1.2,
                "reason": "authenticate has cyclomatic complexity 15",
            },
            {
                "file_path": "src/auth/service.py",
                "biomarker_type": "nested_complexity",
                "severity": "medium",
                "function_name": "authenticate",
                "line_start": 10,
                "line_end": 80,
                "details": {"max_nesting": 5, "ccn": 15, "cognitive": 30},
                "health_impact": 0.7,
                "reason": "authenticate nests 5 levels deep",
            },
            {
                "file_path": "src/auth/service.py",
                "biomarker_type": "low_cohesion",
                "severity": "high",
                "function_name": None,
                "line_start": None,
                "line_end": None,
                "details": {},
                "health_impact": 1.0,
                "reason": "AuthService has low cohesion",
                "dimension": "maintainability",
            },
            {
                "file_path": "src/auth/service.py",
                "biomarker_type": "io_in_loop",
                "severity": "medium",
                "function_name": "load_users",
                "line_start": 42,
                "line_end": 42,
                "details": {
                    "boundary_kind": "db",
                    "cross_function": True,
                    "path": ["src/auth/service.py::load_users", "src/db/models.py::fetch_one"],
                },
                "health_impact": 1.0,
                "reason": "a database call is reached once per loop iteration (cross-function N+1)",
                "dimension": "performance",
            },
        ],
    )
    await session.commit()
    return rid
