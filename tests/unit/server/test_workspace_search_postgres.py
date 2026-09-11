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
    saved = (
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
    ) = saved


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
        await crud.upsert_repository(
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
        # Add a wiki page for repo-b
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
    with patch("repowise.core.workspace.config.find_workspace_root", return_value=ws_root):
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
async def test_workspace_search_fanout_shared_db(shared_db_workspace, monkeypatch):
    """Workspace search finds hits in non-primary member repos on a shared DB without wiki.db."""
    ws_root = shared_db_workspace["ws_root"]
    db_url = shared_db_workspace["db_url"]

    monkeypatch.setenv("REPOWISE_DB_URL", db_url)
    with patch("repowise.core.workspace.config.find_workspace_root", return_value=ws_root):
        app = create_app()
        async with lifespan(app), AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test"},
        ) as client:
            # Fulltext search fan-out across workspace
            resp = await client.get(
                "/api/search",
                params={"query": "queue", "search_type": "fulltext"},
            )
            assert resp.status_code == 200
            hits = resp.json()
            assert len(hits) >= 1
            assert hits[0]["page_id"] == "file_page:repo-b/service.py"

            # Scoped search to repo-b
            resp_b = await client.get(
                "/api/search",
                params={"query": "queue", "search_type": "fulltext", "repo_id": "repo-b-id"},
            )
            assert resp_b.status_code == 200
            hits_b = resp_b.json()
            assert len(hits_b) >= 1
            assert hits_b[0]["page_id"] == "file_page:repo-b/service.py"


@pytest.mark.asyncio
async def test_workspace_semantic_search_fanout_fallback(shared_db_workspace, monkeypatch):
    """Semantic search fans out across member repos without wiki.db and falls back to FTS."""
    ws_root = shared_db_workspace["ws_root"]
    db_url = shared_db_workspace["db_url"]

    monkeypatch.setenv("REPOWISE_DB_URL", db_url)
    with patch("repowise.core.workspace.config.find_workspace_root", return_value=ws_root):
        app = create_app()
        async with lifespan(app), AsyncClient(
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
            assert len(hits) >= 1
            assert hits[0]["page_id"] == "file_page:repo-b/service.py"
