"""Tests for workspace serve and search fan-out in shared DB / PostgreSQL mode."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

import repowise.server.mcp_server as mcp_mod
from repowise.core.persistence import crud
from repowise.core.persistence.database import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
)
from repowise.core.persistence.search import FullTextSearch
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.server.app import create_app, lifespan


@pytest.fixture(autouse=True)
def restore_tool_globals():
    """The real lifespan writes process-global MCP tool state — put it back."""
    saved_mcp = (
        getattr(mcp_mod, "_registry", None),
        getattr(mcp_mod, "_workspace_root", None),
        getattr(mcp_mod, "_cross_repo_enricher", None),
        getattr(mcp_mod, "_session_factory", None),
        getattr(mcp_mod, "_fts", None),
        getattr(mcp_mod, "_vector_store", None),
    )
    yield
    (
        mcp_mod._registry,
        mcp_mod._workspace_root,
        mcp_mod._cross_repo_enricher,
        mcp_mod._session_factory,
        mcp_mod._fts,
        mcp_mod._vector_store,
    ) = saved_mcp


@pytest.fixture
async def shared_db_workspace(tmp_path: Path):
    """Set up a workspace with 2 repositories on a shared database."""
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()

    repo_a = ws_root / "repo-a"
    repo_a.mkdir()
    repo_b = ws_root / "repo-b"
    repo_b.mkdir()

    ws_config = WorkspaceConfig(
        repos=[
            RepoEntry(alias="repo-a", path="repo-a", is_primary=True),
            RepoEntry(alias="repo-b", path="repo-b", is_primary=False),
        ],
    )
    ws_config.save(ws_root)

    # Shared database (simulating PostgreSQL / configured DB URL)
    db_file = tmp_path / "shared.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    engine = create_engine(db_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)

    # Register both repos in the shared DB
    async with get_session(session_factory) as session:
        r_a = await crud.upsert_repository(
            session,
            name="repo-a",
            local_path=str(repo_a.resolve()),
            default_branch="main",
            repo_id="repo-a-id",
        )
        r_b = await crud.upsert_repository(
            session,
            name="repo-b",
            local_path=str(repo_b.resolve()),
            default_branch="main",
            repo_id="repo-b-id",
        )
        # Add wiki pages for repo-a and repo-b
        await crud.upsert_page(
            session,
            page_id="file_page:repo-a/worker.py",
            repository_id=r_a.id,
            page_type="file_page",
            title="worker.py",
            content="# Worker Module\n\nHandles background queue jobs in repo-a.",
            summary="Worker for queue jobs",
            target_path="worker.py",
            source_hash="hash-a",
            model_name="mock",
            provider_name="mock",
        )
        await crud.upsert_page(
            session,
            page_id="file_page:repo-b/service.py",
            repository_id=r_b.id,
            page_type="file_page",
            title="service.py",
            content="# Service Module\n\nHandles background queue jobs in repo-b.",
            summary="Service for queue jobs",
            target_path="service.py",
            source_hash="hash-b",
            model_name="mock",
            provider_name="mock",
        )

    fts = FullTextSearch(engine)
    await fts.ensure_index()
    await fts.index(
        "file_page:repo-a/worker.py",
        "worker.py",
        "Handles background queue jobs in repo-a",
        summary="Worker for queue jobs",
        target_path="worker.py",
    )
    await fts.index(
        "file_page:repo-b/service.py",
        "service.py",
        "Handles background queue jobs in repo-b",
        summary="Service for queue jobs",
        target_path="service.py",
    )

    yield {
        "ws_root": ws_root,
        "repo_a": repo_a,
        "repo_b": repo_b,
        "db_url": db_url,
        "engine": engine,
        "session_factory": session_factory,
        "fts": fts,
    }

    await engine.dispose()


@pytest.mark.asyncio
async def test_workspace_lifespan_registers_members_on_shared_db(shared_db_workspace, monkeypatch):
    """Workspace lifespan populates workspace_sessions and workspace_fts for member repos without wiki.db."""
    ws_root = shared_db_workspace["ws_root"]
    db_url = shared_db_workspace["db_url"]

    monkeypatch.setenv("REPOWISE_DB_URL", db_url)
    with (
        patch("repowise.core.workspace.config.find_workspace_root", return_value=ws_root),
        patch("repowise.server.app.setup_scheduler"),
    ):
        app = create_app()
        async with lifespan(app):
            assert app.state.workspace_config is not None
            # Both member repos should be registered to the shared session factory and FTS
            assert "repo-a-id" in app.state.workspace_sessions
            assert "repo-b-id" in app.state.workspace_sessions
            assert "repo-a-id" in app.state.workspace_fts
            assert "repo-b-id" in app.state.workspace_fts
            assert app.state.workspace_sessions["repo-a-id"] == app.state.session_factory
            assert app.state.workspace_sessions["repo-b-id"] == app.state.session_factory


@pytest.mark.asyncio
async def test_workspace_search_fanout_shared_db(shared_db_workspace):
    """Workspace search finds hits in non-primary member repos on a shared DB without wiki.db."""
    from repowise.core.persistence.vector_store import InMemoryVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder
    from tests.unit.server.conftest import _create_test_app

    ws_root = shared_db_workspace["ws_root"]
    repo_a = shared_db_workspace["repo_a"]
    repo_b = shared_db_workspace["repo_b"]

    app = _create_test_app()
    vector_store = InMemoryVectorStore(embedder=MockEmbedder())

    app.state.engine = shared_db_workspace["engine"]
    app.state.session_factory = shared_db_workspace["session_factory"]
    app.state.fts = shared_db_workspace["fts"]
    app.state.vector_store = vector_store
    app.state.background_tasks = set()
    app.state.workspace_root = str(ws_root)
    app.state.workspace_config = WorkspaceConfig(
        repos=[
            RepoEntry(alias="repo-a", path="repo-a", is_primary=True),
            RepoEntry(alias="repo-b", path="repo-b", is_primary=False),
        ],
    )
    app.state.workspace_path_to_repo_id = {
        str(repo_a.resolve()): "repo-a-id",
        str(repo_b.resolve()): "repo-b-id",
    }
    app.state.workspace_sessions = {
        "repo-a-id": shared_db_workspace["session_factory"],
        "repo-b-id": shared_db_workspace["session_factory"],
    }
    app.state.workspace_fts = {
        "repo-a-id": shared_db_workspace["fts"],
        "repo-b-id": shared_db_workspace["fts"],
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test"},
    ) as client:
        # Fulltext search fan-out across workspace returns hits from all member repos
        resp = await client.get(
            "/api/search",
            params={"query": "queue", "search_type": "fulltext"},
        )
        assert resp.status_code == 200
        hits = resp.json()
        assert len(hits) == 2
        page_ids = {h["page_id"] for h in hits}
        assert page_ids == {"file_page:repo-a/worker.py", "file_page:repo-b/service.py"}

        # Scoped search to repo-b returns only repo-b's page
        resp_b = await client.get(
            "/api/search",
            params={"query": "queue", "search_type": "fulltext", "repo_id": "repo-b-id"},
        )
        assert resp_b.status_code == 200
        hits_b = resp_b.json()
        assert len(hits_b) == 1
        assert hits_b[0]["page_id"] == "file_page:repo-b/service.py"

        # Scoped search to repo-a returns only repo-a's page
        resp_a = await client.get(
            "/api/search",
            params={"query": "queue", "search_type": "fulltext", "repo_id": "repo-a-id"},
        )
        assert resp_a.status_code == 200
        hits_a = resp_a.json()
        assert len(hits_a) == 1
        assert hits_a[0]["page_id"] == "file_page:repo-a/worker.py"

    await vector_store.close()


@pytest.mark.asyncio
async def test_workspace_semantic_search_fanout_fallback(shared_db_workspace):
    """Semantic search fans out across member repos without wiki.db and falls back to FTS."""
    from repowise.core.persistence.vector_store import InMemoryVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder
    from tests.unit.server.conftest import _create_test_app

    ws_root = shared_db_workspace["ws_root"]
    repo_a = shared_db_workspace["repo_a"]
    repo_b = shared_db_workspace["repo_b"]

    app = _create_test_app()
    vector_store = InMemoryVectorStore(embedder=MockEmbedder())

    app.state.engine = shared_db_workspace["engine"]
    app.state.session_factory = shared_db_workspace["session_factory"]
    app.state.fts = shared_db_workspace["fts"]
    app.state.vector_store = vector_store
    app.state.background_tasks = set()
    app.state.workspace_root = str(ws_root)
    app.state.workspace_config = WorkspaceConfig(
        repos=[
            RepoEntry(alias="repo-a", path="repo-a", is_primary=True),
            RepoEntry(alias="repo-b", path="repo-b", is_primary=False),
        ],
    )
    app.state.workspace_path_to_repo_id = {
        str(repo_a.resolve()): "repo-a-id",
        str(repo_b.resolve()): "repo-b-id",
    }
    app.state.workspace_sessions = {
        "repo-a-id": shared_db_workspace["session_factory"],
        "repo-b-id": shared_db_workspace["session_factory"],
    }
    app.state.workspace_fts = {
        "repo-a-id": shared_db_workspace["fts"],
        "repo-b-id": shared_db_workspace["fts"],
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "test"},
    ) as client:
        resp = await client.get(
            "/api/search",
            params={"query": "queue", "search_type": "semantic"},
        )
        assert resp.status_code == 200
        hits = resp.json()
        assert len(hits) == 2
        page_ids = {h["page_id"] for h in hits}
        assert page_ids == {"file_page:repo-a/worker.py", "file_page:repo-b/service.py"}

    await vector_store.close()


