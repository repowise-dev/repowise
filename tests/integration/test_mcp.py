"""Integration tests for the MCP server tools.

Tests MCP tools against a fully populated database created by running
a mock init pipeline — verifying end-to-end tool responses with realistic data.
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
async def mcp_env():
    """Set up a complete MCP test environment with realistic data."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    fts = FullTextSearch(engine)
    await fts.ensure_index()
    embedder = MockEmbedder()
    vector_store = InMemoryVectorStore(embedder=embedder)
    decision_store = InMemoryVectorStore(embedder=embedder)

    # Populate DB with a realistic multi-file Python project
    async with factory() as session:
        repo = Repository(
            id="integ-repo",
            name="sample-project",
            url="https://github.com/example/sample",
            local_path="/tmp/sample-project",
            default_branch="main",
            settings_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        )
        session.add(repo)

        # Pages: overview, arch diagram, 2 modules, 4 files
        pages_data = [
            (
                "repo_overview:sample",
                "repo_overview",
                "Sample Project Overview",
                "# Sample Project\n\nA Python web application with auth and data modules.",
                "sample",
                6,
            ),
            (
                "architecture_diagram:sample",
                "architecture_diagram",
                "Architecture",
                "graph TD\n    auth[Auth] --> db[Database]\n    api[API] --> auth\n    api --> db",
                "sample",
                6,
            ),
            (
                "module_page:src/auth",
                "module_page",
                "Authentication Module",
                "# Auth\n\nHandles user authentication, sessions, and JWT tokens.",
                "src/auth",
                4,
            ),
            (
                "module_page:src/data",
                "module_page",
                "Data Module",
                "# Data\n\nDatabase models, repositories, and query builders.",
                "src/data",
                4,
            ),
            (
                "file_page:src/auth/login.py",
                "file_page",
                "Login Handler",
                "# Login\n\nHandles user login via username/password or OAuth.",
                "src/auth/login.py",
                2,
            ),
            (
                "file_page:src/auth/jwt.py",
                "file_page",
                "JWT Utilities",
                "# JWT\n\nJSON Web Token creation, validation, and refresh.",
                "src/auth/jwt.py",
                2,
            ),
            (
                "file_page:src/data/user_repo.py",
                "file_page",
                "User Repository",
                "# UserRepository\n\nCRUD operations for User model.",
                "src/data/user_repo.py",
                2,
            ),
            (
                "file_page:src/data/models.py",
                "file_page",
                "Data Models",
                "# Models\n\nSQLAlchemy ORM models: User, Session, Token.",
                "src/data/models.py",
                2,
            ),
        ]
        for pid, ptype, title, content, tpath, level in pages_data:
            session.add(
                Page(
                    id=pid,
                    repository_id="integ-repo",
                    page_type=ptype,
                    title=title,
                    content=content,
                    target_path=tpath,
                    source_hash="h" + pid[:6],
                    model_name="mock",
                    provider_name="mock",
                    generation_level=level,
                    confidence=0.9,
                    freshness_status="fresh",
                    metadata_json="{}",
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )

        # Symbols
        sym_data = [
            (
                "src/auth/login.py",
                "login_handler",
                "function",
                "async def login_handler(request: Request) -> Response",
            ),
            ("src/auth/login.py", "LoginForm", "class", "class LoginForm(BaseModel)"),
            (
                "src/auth/jwt.py",
                "create_token",
                "function",
                "def create_token(user_id: str, secret: str) -> str",
            ),
            (
                "src/auth/jwt.py",
                "verify_token",
                "function",
                "def verify_token(token: str, secret: str) -> dict",
            ),
            ("src/data/user_repo.py", "UserRepository", "class", "class UserRepository"),
            (
                "src/data/user_repo.py",
                "find_by_email",
                "method",
                "async def find_by_email(self, email: str) -> User | None",
            ),
            ("src/data/models.py", "User", "class", "class User(Base)"),
            ("src/data/models.py", "Session", "class", "class Session(Base)"),
        ]
        for i, (fp, name, kind, sig) in enumerate(sym_data):
            session.add(
                WikiSymbol(
                    id=f"is{i}",
                    repository_id="integ-repo",
                    file_path=fp,
                    symbol_id=f"{fp}::{name}",
                    name=name,
                    qualified_name=name,
                    kind=kind,
                    signature=sig,
                    start_line=1,
                    end_line=20,
                    visibility="public",
                    language="python",
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )

        # Graph nodes
        files = [
            "src/auth/login.py",
            "src/auth/jwt.py",
            "src/data/user_repo.py",
            "src/data/models.py",
        ]
        for i, fp in enumerate(files):
            session.add(
                GraphNode(
                    id=f"ign{i}",
                    repository_id="integ-repo",
                    node_id=fp,
                    node_type="file",
                    language="python",
                    symbol_count=2,
                    is_entry_point=(fp == "src/auth/login.py"),
                    pagerank=0.8 - i * 0.15,
                    betweenness=0.3,
                    community_id=1 if "auth" in fp else 2,
                    created_at=_NOW,
                )
            )

        # Graph edges
        edge_data = [
            ("src/auth/login.py", "src/auth/jwt.py", '["create_token"]'),
            ("src/auth/login.py", "src/data/user_repo.py", '["UserRepository"]'),
            ("src/data/user_repo.py", "src/data/models.py", '["User"]'),
        ]
        for i, (src, tgt, names) in enumerate(edge_data):
            session.add(
                GraphEdge(
                    id=f"ige{i}",
                    repository_id="integ-repo",
                    source_node_id=src,
                    target_node_id=tgt,
                    imported_names_json=names,
                    created_at=_NOW,
                )
            )

        # Git metadata
        session.add(
            GitMetadata(
                id="igm1",
                repository_id="integ-repo",
                file_path="src/auth/login.py",
                commit_count_total=50,
                commit_count_90d=12,
                commit_count_30d=5,
                first_commit_at=datetime(2025, 1, 1, tzinfo=UTC),
                last_commit_at=datetime(2026, 3, 18, tzinfo=UTC),
                primary_owner_name="Alice",
                primary_owner_email="alice@ex.com",
                primary_owner_commit_pct=0.70,
                top_authors_json=json.dumps(
                    [{"name": "Alice", "count": 35}, {"name": "Bob", "count": 15}]
                ),
                significant_commits_json=json.dumps(
                    [
                        {
                            "sha": "a1",
                            "date": "2026-03-18",
                            "message": "Fix OAuth redirect",
                            "author": "Alice",
                        },
                    ]
                ),
                co_change_partners_json=json.dumps(
                    [
                        {"file_path": "src/auth/jwt.py", "count": 8},
                    ]
                ),
                is_hotspot=True,
                is_stable=False,
                churn_percentile=0.95,
                age_days=443,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )

        # Dead code
        session.add(
            DeadCodeFinding(
                id="idc1",
                repository_id="integ-repo",
                kind="unused_export",
                file_path="src/auth/jwt.py",
                symbol_name="deprecated_verify",
                symbol_kind="function",
                confidence=0.8,
                reason="No callers",
                lines=15,
                safe_to_delete=True,
                primary_owner="Alice",
                status="open",
                analyzed_at=_NOW,
            )
        )

        await session.commit()

    # Index pages in vector store for search
    await vector_store.embed_and_upsert(
        "file_page:src/auth/login.py",
        "Login Handler — OAuth and password auth",
        {"title": "Login Handler", "page_type": "file_page", "target_path": "src/auth/login.py"},
    )
    await vector_store.embed_and_upsert(
        "file_page:src/data/models.py",
        "Data Models — SQLAlchemy User Session Token",
        {"title": "Data Models", "page_type": "file_page", "target_path": "src/data/models.py"},
    )

    # Configure MCP globals
    import repowise.server.mcp_server as mcp_mod

    mcp_mod._session_factory = factory
    mcp_mod._fts = fts
    mcp_mod._vector_store = vector_store
    mcp_mod._decision_store = decision_store
    mcp_mod._repo_path = "/tmp/sample-project"

    yield

    mcp_mod._session_factory = None
    mcp_mod._fts = None
    mcp_mod._vector_store = None
    mcp_mod._decision_store = None
    mcp_mod._repo_path = None

    await decision_store.close()
    await vector_store.close()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Integration tests — verify end-to-end MCP tool responses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_full_exploration_flow(mcp_env):
    """Test the typical MCP exploration flow: overview → get_context for module/file/symbol."""
    from repowise.server.mcp_server import get_context, get_overview

    # Step 1: Get overview
    overview = await get_overview()
    assert "Sample Project" in overview["content_md"]
    assert len(overview["key_modules"]) == 2
    assert overview["architecture_diagram_mermaid"] is not None

    # Step 2: Drill into auth module via get_context
    auth = await get_context(["src/auth"])
    t = auth["targets"]["src/auth"]
    assert t["type"] == "module"
    assert "authentication" in t["docs"]["content_md"].lower()
    assert len(t["docs"]["files"]) == 2

    # Step 3: Get file context
    login = await get_context(["src/auth/login.py"])
    t = login["targets"]["src/auth/login.py"]
    assert t["type"] == "file"
    assert t["docs"]["title"] == "Login Handler"
    assert any(s["name"] == "login_handler" for s in t["docs"]["symbols"])

    # Step 4: Look up a symbol
    sym = await get_context(["UserRepository"])
    t = sym["targets"]["UserRepository"]
    assert t["type"] == "symbol"
    assert t["docs"]["kind"] == "class"
    assert t["docs"]["file_path"] == "src/data/user_repo.py"


@pytest.mark.asyncio
async def test_mcp_git_intelligence_flow(mcp_env):
    """Test git intelligence via get_context and get_risk."""
    from repowise.server.mcp_server import get_context, get_risk

    # File context with ownership and history
    ctx = await get_context(["src/auth/login.py"], include=["ownership", "last_change"])
    t = ctx["targets"]["src/auth/login.py"]
    assert t["ownership"]["primary_owner"] == "Alice"
    assert t["ownership"]["owner_pct"] == 0.70
    assert t["last_change"]["days_ago"] == 443

    # Risk assessment
    risk = await get_risk(["src/auth/login.py"])
    t = risk["targets"]["src/auth/login.py"]
    assert t["hotspot_score"] == 0.95
    assert len(t["co_change_partners"]) == 1
    assert t["co_change_partners"][0]["file_path"] == "src/auth/jwt.py"
    assert "global_hotspots" in risk


@pytest.mark.asyncio
async def test_mcp_dead_code_and_freshness_flow(mcp_env):
    """Test dead code tool and freshness via get_context."""
    from repowise.server.mcp_server import get_context, get_dead_code

    # Dead code
    dead = await get_dead_code()
    assert dead["summary"]["total_findings"] == 1
    assert dead["tiers"]["high"]["findings"][0]["symbol_name"] == "deprecated_verify"

    # Freshness via get_context (all pages have confidence 0.9)
    ctx = await get_context(["src/auth/login.py"], include=["freshness"])
    t = ctx["targets"]["src/auth/login.py"]
    assert t["freshness"]["confidence_score"] == 0.9
    assert t["freshness"]["is_stale"] is False


@pytest.mark.asyncio
async def test_mcp_search_flow(mcp_env):
    """Test semantic search."""
    from repowise.server.mcp_server import search_codebase

    result = await search_codebase("authentication login OAuth")
    assert "results" in result
    assert len(result["results"]) >= 1
