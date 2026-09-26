"""Tests for DELETE /api/workspace/repos/{alias} and removing missing workspace entries."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.server.deps import verify_api_key
from repowise.server.routers import repos, workspace


def _make_workspace_app(
    *,
    ws_config: WorkspaceConfig | None = None,
    workspace_root: str | None = None,
) -> FastAPI:
    """Build a minimal FastAPI app with workspace and repos routers + injected state."""

    @asynccontextmanager
    async def noop_lifespan(app: FastAPI):
        yield

    app = FastAPI(title="workspace-test", lifespan=noop_lifespan)

    @app.exception_handler(LookupError)
    async def not_found_handler(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    app.state.workspace_config = ws_config
    app.state.workspace_root = workspace_root
    app.state.session_factory = MagicMock()
    app.state.fts = MagicMock()
    app.state.workspace_sessions = {}
    app.state.workspace_fts = {}
    app.state.workspace_path_to_repo_id = {}
    app.state.workspace_vector_stores = {}

    app.include_router(workspace.router)
    app.include_router(repos.router)

    # Override auth so ASGI test transport (where request.client is None)
    # doesn't reject every request with 403 when REPOWISE_API_KEY is unset.
    async def _noop_auth() -> None:
        return None

    app.dependency_overrides[verify_api_key] = _noop_auth
    return app


@pytest.mark.asyncio
async def test_remove_workspace_repo_success(tmp_path: Path):
    """DELETE /api/workspace/repos/{alias} drops the entry from YAML and live app state."""
    repo_a = RepoEntry(alias="backend", path="backend", is_primary=True)
    repo_b = RepoEntry(alias="frontend", path="frontend", is_primary=False)
    ws_config = WorkspaceConfig(repos=[repo_a, repo_b], default_repo="backend")
    ws_config.save(tmp_path)

    app = _make_workspace_app(ws_config=ws_config, workspace_root=str(tmp_path))

    frontend_abs = str((tmp_path / "frontend").resolve())
    app.state.workspace_path_to_repo_id[frontend_abs] = "repo-frontend-id"
    app.state.workspace_sessions["repo-frontend-id"] = MagicMock()
    app.state.workspace_fts["repo-frontend-id"] = MagicMock()
    app.state.workspace_vector_stores["repo-frontend-id"] = MagicMock()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.delete("/api/workspace/repos/frontend")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["alias"] == "frontend"
        assert data["remaining_repos"] == 1

    # Verify on-disk configuration
    reloaded_config = WorkspaceConfig.load(tmp_path)
    assert len(reloaded_config.repos) == 1
    assert reloaded_config.repos[0].alias == "backend"

    # Verify in-memory state updates
    assert len(app.state.workspace_config.repos) == 1
    assert app.state.workspace_config.repos[0].alias == "backend"
    assert "repo-frontend-id" not in app.state.workspace_sessions
    assert "repo-frontend-id" not in app.state.workspace_fts
    assert "repo-frontend-id" not in app.state.workspace_vector_stores
    assert frontend_abs not in app.state.workspace_path_to_repo_id


@pytest.mark.asyncio
async def test_remove_workspace_repo_not_found(tmp_path: Path):
    """DELETE /api/workspace/repos/{alias} returns 404 for unknown alias."""
    repo_a = RepoEntry(alias="backend", path="backend", is_primary=True)
    ws_config = WorkspaceConfig(repos=[repo_a], default_repo="backend")
    ws_config.save(tmp_path)

    app = _make_workspace_app(ws_config=ws_config, workspace_root=str(tmp_path))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.delete("/api/workspace/repos/unknown-repo")
        assert resp.status_code == 404
        assert "Unknown repo alias" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_remove_workspace_repo_not_workspace_mode():
    """DELETE /api/workspace/repos/{alias} returns 404 when not in workspace mode."""
    app = _make_workspace_app(ws_config=None)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.delete("/api/workspace/repos/backend")
        assert resp.status_code == 404
        assert "Not running in workspace mode" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_delete_synthetic_repo_via_repos_router(tmp_path: Path):
    """DELETE /api/repos/ws:{alias} removes the synthetic entry from workspace config."""
    repo_a = RepoEntry(alias="backend", path="backend", is_primary=True)
    repo_b = RepoEntry(alias="missing-folder", path="missing-folder", is_primary=False)
    ws_config = WorkspaceConfig(repos=[repo_a, repo_b], default_repo="backend")
    ws_config.save(tmp_path)

    app = _make_workspace_app(ws_config=ws_config, workspace_root=str(tmp_path))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.delete("/api/repos/ws:missing-folder")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["deleted_pages"] == 0

    # Verify on-disk configuration
    reloaded_config = WorkspaceConfig.load(tmp_path)
    assert len(reloaded_config.repos) == 1
    assert reloaded_config.repos[0].alias == "backend"
