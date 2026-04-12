"""Tests for /api/workspace endpoints."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from repowise.server.mcp_server._enrichment import CrossRepoEnricher
from repowise.server.routers import workspace


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_workspace_app(
    *,
    ws_config=None,
    enricher=None,
    workspace_root: str | None = None,
) -> FastAPI:
    """Build a minimal FastAPI app with workspace router + injected state."""

    @asynccontextmanager
    async def noop_lifespan(app: FastAPI):
        yield

    app = FastAPI(title="workspace-test", lifespan=noop_lifespan)

    @app.exception_handler(LookupError)
    async def not_found_handler(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    app.state.workspace_config = ws_config
    app.state.cross_repo_enricher = enricher
    app.state.workspace_root = workspace_root

    app.include_router(workspace.router)
    return app


def _make_ws_config():
    """Build a fake WorkspaceConfig-like object."""
    repo1 = MagicMock()
    repo1.alias = "backend"
    repo1.path = "./backend"
    repo1.is_primary = True
    repo1.indexed_at = "2026-04-12T10:00:00Z"
    repo1.last_commit_at_index = "abc1234"

    repo2 = MagicMock()
    repo2.alias = "frontend"
    repo2.path = "./frontend"
    repo2.is_primary = False
    repo2.indexed_at = None
    repo2.last_commit_at_index = None

    ws_config = MagicMock()
    ws_config.repos = [repo1, repo2]
    ws_config.default_repo = "backend"
    return ws_config


def _make_enricher(tmp_path: Path) -> CrossRepoEnricher:
    """Build a real enricher with sample data."""
    cross_repo_path = tmp_path / "cross_repo_edges.json"
    _write_json(cross_repo_path, {
        "version": 1,
        "co_changes": [
            {
                "source_repo": "backend",
                "source_file": "api/routes.py",
                "target_repo": "frontend",
                "target_file": "src/client.ts",
                "strength": 0.8,
                "frequency": 5,
                "last_date": "2026-04-10",
            },
        ],
        "package_deps": [
            {
                "source_repo": "frontend",
                "target_repo": "backend",
                "source_manifest": "package.json",
                "kind": "npm",
            },
        ],
    })

    contracts_path = tmp_path / "contracts.json"
    _write_json(contracts_path, {
        "version": 1,
        "generated_at": "2026-04-12T12:00:00Z",
        "contracts": [
            {
                "repo": "backend",
                "contract_id": "http::GET::/api/users",
                "contract_type": "http",
                "role": "provider",
                "file_path": "routes.py",
                "symbol_name": "get_users",
                "confidence": 0.85,
                "service": None,
            },
            {
                "repo": "frontend",
                "contract_id": "http::GET::/api/users",
                "contract_type": "http",
                "role": "consumer",
                "file_path": "client.ts",
                "symbol_name": "fetchUsers",
                "confidence": 0.75,
                "service": None,
            },
            {
                "repo": "backend",
                "contract_id": "grpc::Auth/Login",
                "contract_type": "grpc",
                "role": "provider",
                "file_path": "auth.py",
                "symbol_name": "Login",
                "confidence": 0.85,
                "service": None,
            },
        ],
        "contract_links": [
            {
                "contract_id": "http::GET::/api/users",
                "contract_type": "http",
                "match_type": "exact",
                "confidence": 0.75,
                "provider_repo": "backend",
                "provider_file": "routes.py",
                "provider_symbol": "get_users",
                "consumer_repo": "frontend",
                "consumer_file": "client.ts",
                "consumer_symbol": "fetchUsers",
            },
        ],
    })

    return CrossRepoEnricher(cross_repo_path, contracts_path=contracts_path)


# ---------------------------------------------------------------------------
# Tests — GET /api/workspace
# ---------------------------------------------------------------------------


class TestGetWorkspace:
    @pytest.mark.asyncio
    async def test_single_repo_mode(self) -> None:
        """No workspace config → is_workspace=false."""
        app = _make_workspace_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_workspace"] is False
        assert data["repos"] == []
        assert data["default_repo"] is None

    @pytest.mark.asyncio
    async def test_workspace_mode(self, tmp_path: Path) -> None:
        """With workspace config → is_workspace=true, repos listed."""
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(
            ws_config=ws_config,
            enricher=enricher,
            workspace_root="/projects/myworkspace",
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_workspace"] is True
        assert len(data["repos"]) == 2
        assert data["repos"][0]["alias"] == "backend"
        assert data["repos"][0]["is_primary"] is True
        assert data["default_repo"] == "backend"
        assert data["workspace_root"] == "/projects/myworkspace"

    @pytest.mark.asyncio
    async def test_cross_repo_summary(self, tmp_path: Path) -> None:
        """Enricher data populates cross_repo_summary."""
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(ws_config=ws_config, enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace")
        data = resp.json()
        assert data["cross_repo_summary"]["co_change_count"] == 1
        assert data["cross_repo_summary"]["package_dep_count"] == 1

    @pytest.mark.asyncio
    async def test_contract_summary(self, tmp_path: Path) -> None:
        """Enricher contract data populates contract_summary."""
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(ws_config=ws_config, enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace")
        data = resp.json()
        assert data["contract_summary"]["total_contracts"] == 3
        assert data["contract_summary"]["total_links"] == 1
        assert data["contract_summary"]["by_type"]["http"] == 2

    @pytest.mark.asyncio
    async def test_no_enricher(self) -> None:
        """Workspace config but no enricher → summaries are null."""
        ws_config = _make_ws_config()
        app = _make_workspace_app(ws_config=ws_config)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace")
        data = resp.json()
        assert data["is_workspace"] is True
        assert data["cross_repo_summary"] is None
        assert data["contract_summary"] is None


# ---------------------------------------------------------------------------
# Tests — GET /api/workspace/contracts
# ---------------------------------------------------------------------------


class TestGetContracts:
    @pytest.mark.asyncio
    async def test_not_workspace_mode(self) -> None:
        """404 when not in workspace mode."""
        app = _make_workspace_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/contracts")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_all(self, tmp_path: Path) -> None:
        """Returns all contracts and links."""
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(ws_config=ws_config, enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/contracts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_contracts"] == 3
        assert data["total_links"] == 1
        assert len(data["contracts"]) == 3
        assert len(data["links"]) == 1

    @pytest.mark.asyncio
    async def test_filter_by_type(self, tmp_path: Path) -> None:
        """Filter by contract_type returns only matching."""
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(ws_config=ws_config, enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/contracts", params={"contract_type": "grpc"})
        data = resp.json()
        assert data["total_contracts"] == 1
        assert data["contracts"][0]["contract_type"] == "grpc"
        assert data["total_links"] == 0  # no gRPC links in fixture

    @pytest.mark.asyncio
    async def test_filter_by_repo(self, tmp_path: Path) -> None:
        """Filter by repo returns only that repo's contracts."""
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(ws_config=ws_config, enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/contracts", params={"repo": "frontend"})
        data = resp.json()
        assert data["total_contracts"] == 1
        assert data["contracts"][0]["repo"] == "frontend"

    @pytest.mark.asyncio
    async def test_filter_by_role(self, tmp_path: Path) -> None:
        """Filter by role returns only providers or consumers."""
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(ws_config=ws_config, enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/contracts", params={"role": "provider"})
        data = resp.json()
        assert data["total_contracts"] == 2
        assert all(c["role"] == "provider" for c in data["contracts"])

    @pytest.mark.asyncio
    async def test_no_enricher(self) -> None:
        """Workspace mode but no enricher → empty response."""
        ws_config = _make_ws_config()
        app = _make_workspace_app(ws_config=ws_config)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/contracts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_contracts"] == 0
        assert data["total_links"] == 0


# ---------------------------------------------------------------------------
# Tests — GET /api/workspace/co-changes
# ---------------------------------------------------------------------------


class TestGetCoChanges:
    @pytest.mark.asyncio
    async def test_not_workspace_mode(self) -> None:
        """404 when not in workspace mode."""
        app = _make_workspace_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/co-changes")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_all(self, tmp_path: Path) -> None:
        """Returns co-change pairs."""
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(ws_config=ws_config, enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/co-changes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["co_changes"]) == 1
        assert data["co_changes"][0]["source_repo"] == "backend"
        assert data["co_changes"][0]["strength"] == 0.8

    @pytest.mark.asyncio
    async def test_filter_by_repo(self, tmp_path: Path) -> None:
        """Filter by repo returns matching pairs."""
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(ws_config=ws_config, enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/co-changes", params={"repo": "backend"})
        data = resp.json()
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_filter_by_repo_no_match(self, tmp_path: Path) -> None:
        """Filter by non-existent repo returns empty."""
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(ws_config=ws_config, enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/co-changes", params={"repo": "nonexistent"})
        data = resp.json()
        assert data["total"] == 0
        assert data["co_changes"] == []

    @pytest.mark.asyncio
    async def test_min_strength_filter(self, tmp_path: Path) -> None:
        """min_strength filter excludes weak pairs."""
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(ws_config=ws_config, enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # Our only co-change has strength 0.8 — filter above it
            resp = await c.get("/api/workspace/co-changes", params={"min_strength": "0.9"})
        data = resp.json()
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_no_enricher(self) -> None:
        """Workspace mode but no enricher → empty."""
        ws_config = _make_ws_config()
        app = _make_workspace_app(ws_config=ws_config)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/co-changes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
