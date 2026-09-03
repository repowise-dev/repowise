"""Tests for /api/workspace endpoints."""

from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    _write_json(
        cross_repo_path,
        {
            "version": 2,
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
        },
    )

    contracts_path = tmp_path / "contracts.json"
    _write_json(
        contracts_path,
        {
            "version": 2,
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
                    "line": 42,
                    "symbol_id": "routes.py::get_users",
                    "meta": {"extraction_layer": "index", "framework": "fastapi"},
                    "schema": {
                        "source": "signature",
                        "request_fields": [{"name": "limit", "type": "int"}],
                        "response_fields": [{"name": "id", "type": "int", "required": True}],
                    },
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
                    "line": 7,
                    "symbol_id": "client.ts::fetchUsers",
                    "meta": {"extraction_layer": "index", "client": "fetch"},
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
                {
                    # A consumer nothing provides — the row the detail endpoint
                    # reports an unmatched reason for.
                    "repo": "backend",
                    "contract_id": "http::POST::/api/embed",
                    "contract_type": "http",
                    "role": "consumer",
                    "file_path": "ollama.py",
                    "symbol_name": "embed",
                    "confidence": 0.7,
                    "service": None,
                    "line": 88,
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
                    "provider_service": "services/api",
                    "consumer_service": "services/web",
                    "provider_symbol_id": "routes.py::get_users",
                    "consumer_symbol_id": "client.ts::fetchUsers",
                },
            ],
        },
    )

    # Unmatched reasons live in the system graph, not in contracts.json.
    system_graph_path = tmp_path / "system_graph.json"
    _write_json(
        system_graph_path,
        {
            "version": 1,
            "generated_at": "2026-04-12T12:00:00Z",
            "nodes": [],
            "edges": [],
            "diagnostics": {
                "unmatched_consumers": [
                    {
                        "repo": "backend",
                        "file_path": "ollama.py",
                        "contract_id": "http::POST::/api/embed",
                        "contract_type": "http",
                        "reason": "no_provider",
                    },
                ],
                "unmatched_by_reason": {"no_provider": 1},
            },
        },
    )

    return CrossRepoEnricher(
        cross_repo_path,
        contracts_path=contracts_path,
        system_graph_path=system_graph_path,
    )


def _create_workspace_repo_db(
    workspace_root: Path,
    repo_alias: str,
    *,
    health_rows: list[tuple[float, int]] | None = None,
) -> None:
    repo_dir = workspace_root / repo_alias / ".repowise"
    repo_dir.mkdir(parents=True)
    db_path = repo_dir / "wiki.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE repositories (id TEXT PRIMARY KEY);
            CREATE TABLE graph_nodes (
                id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL DEFAULT 'file',
                language TEXT,
                symbol_count INTEGER DEFAULT 0
            );
            CREATE TABLE wiki_pages (id TEXT PRIMARY KEY, confidence REAL);
            CREATE TABLE git_metadata (
                id TEXT PRIMARY KEY,
                churn_percentile REAL,
                is_hotspot INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE health_file_metrics (
                id TEXT PRIMARY KEY,
                score REAL NOT NULL,
                nloc INTEGER NOT NULL
            );
            INSERT INTO repositories (id) VALUES ('repo-backend');
            INSERT INTO graph_nodes (id, node_type, language, symbol_count) VALUES
                ('src/a.py', 'file', 'python', 2),
                ('src/b.py', 'file', 'python', 3),
                ('src/a.py::Foo', 'symbol', 'python', 0),
                ('src/a.py::Foo.bar', 'symbol', 'python', 0),
                ('src/b.py::baz', 'symbol', 'python', 0);
            INSERT INTO wiki_pages (id, confidence) VALUES
                ('page-a', 0.8),
                ('page-b', 0.6);
            INSERT INTO git_metadata (id, churn_percentile, is_hotspot) VALUES
                ('src/a.py', 0.95, 1),
                ('src/b.py', 0.10, 0);
            """
        )
        for idx, (score, nloc) in enumerate(health_rows or []):
            conn.execute(
                "INSERT INTO health_file_metrics (id, score, nloc) VALUES (?, ?, ?)",
                (f"metric-{idx}", score, nloc),
            )


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
        assert data["contract_summary"]["total_contracts"] == 4
        assert data["contract_summary"]["total_links"] == 1
        assert data["contract_summary"]["by_type"]["http"] == 3

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
        assert data["total_contracts"] == 4
        assert data["total_links"] == 1
        assert len(data["contracts"]) == 4
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


class TestContractWireFields:
    """The list endpoint carries everything but ``schema``."""

    @pytest.mark.asyncio
    async def test_entry_carries_line_symbol_id_and_meta(self, tmp_path: Path) -> None:
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(ws_config=ws_config, enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/contracts")
        entry = next(e for e in resp.json()["contracts"] if e["symbol_name"] == "get_users")
        assert entry["line"] == 42
        assert entry["symbol_id"] == "routes.py::get_users"
        assert entry["meta"]["framework"] == "fastapi"

    @pytest.mark.asyncio
    async def test_entry_tolerates_a_row_without_them(self, tmp_path: Path) -> None:
        """A contract that never bound to a line still serializes."""
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(ws_config=ws_config, enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/contracts")
        entry = next(e for e in resp.json()["contracts"] if e["symbol_name"] == "Login")
        assert entry["line"] is None
        assert entry["symbol_id"] is None
        assert entry["meta"] == {}

    @pytest.mark.asyncio
    async def test_schema_stays_off_the_list(self, tmp_path: Path) -> None:
        """``schema`` is the one field the list must not carry - it is the bulk."""
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(ws_config=ws_config, enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/contracts")
        for entry in resp.json()["contracts"]:
            assert "schema" not in entry
            assert "contract_schema" not in entry

    @pytest.mark.asyncio
    async def test_link_carries_symbol_ids_and_provider_service(self, tmp_path: Path) -> None:
        ws_config = _make_ws_config()
        enricher = _make_enricher(tmp_path)
        app = _make_workspace_app(ws_config=ws_config, enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/contracts")
        link = resp.json()["links"][0]
        assert link["provider_service"] == "services/api"
        assert link["consumer_service"] == "services/web"
        assert link["provider_symbol_id"] == "routes.py::get_users"
        assert link["consumer_symbol_id"] == "client.ts::fetchUsers"


# ---------------------------------------------------------------------------
# Tests — GET /api/workspace/contracts/detail
# ---------------------------------------------------------------------------


class TestGetContractDetail:
    @staticmethod
    def _app(tmp_path: Path):
        return _make_workspace_app(ws_config=_make_ws_config(), enricher=_make_enricher(tmp_path))

    @pytest.mark.asyncio
    async def test_not_workspace_mode(self) -> None:
        app = _make_workspace_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/workspace/contracts/detail",
                params={"repo": "backend", "file": "routes.py", "id": "http::GET::/api/users"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_provider_returns_schema_and_link(self, tmp_path: Path) -> None:
        """The whole point of the route: one contract, with its schema."""
        transport = ASGITransport(app=self._app(tmp_path))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/workspace/contracts/detail",
                params={"repo": "backend", "file": "routes.py", "id": "http::GET::/api/users"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["contract"]["symbol_name"] == "get_users"
        assert data["contract_schema"]["source"] == "signature"
        assert data["contract_schema"]["response_fields"][0]["name"] == "id"
        assert len(data["links"]) == 1
        assert data["links"][0]["consumer_repo"] == "frontend"
        assert data["unmatched_reason"] is None

    @pytest.mark.asyncio
    async def test_consumer_side_finds_its_link(self, tmp_path: Path) -> None:
        """A consumer matches on the consumer columns, not the provider ones."""
        transport = ASGITransport(app=self._app(tmp_path))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/workspace/contracts/detail",
                params={"repo": "frontend", "file": "client.ts", "id": "http::GET::/api/users"},
            )
        data = resp.json()
        assert data["contract"]["role"] == "consumer"
        assert len(data["links"]) == 1
        assert data["contract_schema"] is None
        assert data["unmatched_reason"] is None

    @pytest.mark.asyncio
    async def test_unmatched_consumer_reports_its_reason(self, tmp_path: Path) -> None:
        transport = ASGITransport(app=self._app(tmp_path))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/workspace/contracts/detail",
                params={"repo": "backend", "file": "ollama.py", "id": "http::POST::/api/embed"},
            )
        data = resp.json()
        assert data["links"] == []
        assert data["unmatched_reason"] == "no_provider"

    @pytest.mark.asyncio
    async def test_orphan_provider_has_no_reason(self, tmp_path: Path) -> None:
        """A provider nobody calls is the normal state, not an unmatched one."""
        transport = ASGITransport(app=self._app(tmp_path))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/workspace/contracts/detail",
                params={"repo": "backend", "file": "auth.py", "id": "grpc::Auth/Login"},
            )
        data = resp.json()
        assert data["links"] == []
        assert data["unmatched_reason"] is None

    @pytest.mark.asyncio
    async def test_repo_is_part_of_the_identity(self, tmp_path: Path) -> None:
        """The same contract_id in the wrong repo is a miss, not the other row."""
        transport = ASGITransport(app=self._app(tmp_path))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/workspace/contracts/detail",
                params={"repo": "frontend", "file": "routes.py", "id": "http::GET::/api/users"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_contract(self, tmp_path: Path) -> None:
        transport = ASGITransport(app=self._app(tmp_path))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/workspace/contracts/detail",
                params={"repo": "backend", "file": "nope.py", "id": "http::GET::/nope"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_missing_param_is_rejected(self, tmp_path: Path) -> None:
        """All three identity params are required."""
        transport = ASGITransport(app=self._app(tmp_path))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/contracts/detail", params={"repo": "backend"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_no_enricher(self) -> None:
        """Workspace mode with no contract data is a 404, not an empty detail."""
        app = _make_workspace_app(ws_config=_make_ws_config())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/workspace/contracts/detail",
                params={"repo": "backend", "file": "routes.py", "id": "http::GET::/api/users"},
            )
        assert resp.status_code == 404


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
        """Workspace mode but no enricher -> empty."""
        ws_config = _make_ws_config()
        app = _make_workspace_app(ws_config=ws_config)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/co-changes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


# ---------------------------------------------------------------------------
# Tests — GET /api/workspace/graph
# ---------------------------------------------------------------------------


class TestGetWorkspaceGraph:
    @pytest.mark.asyncio
    async def test_uses_canonical_health_score_from_repo_metrics(self, tmp_path: Path) -> None:
        ws_config = _make_ws_config()
        _create_workspace_repo_db(
            tmp_path,
            "backend",
            health_rows=[(2.0, 10), (9.0, 30)],
        )
        app = _make_workspace_app(
            ws_config=ws_config,
            workspace_root=str(tmp_path),
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/graph")

        assert resp.status_code == 200
        backend = next(n for n in resp.json()["nodes"] if n["name"] == "backend")
        assert backend["health_score"] == 72.5
        assert backend["health_score_source"] == "canonical"

    @pytest.mark.asyncio
    async def test_marks_derived_health_score_when_repo_metrics_are_missing(
        self,
        tmp_path: Path,
    ) -> None:
        ws_config = _make_ws_config()
        _create_workspace_repo_db(tmp_path, "backend")
        app = _make_workspace_app(
            ws_config=ws_config,
            workspace_root=str(tmp_path),
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/graph")

        assert resp.status_code == 200
        backend = next(n for n in resp.json()["nodes"] if n["name"] == "backend")
        assert backend["health_score"] == 62.0
        assert backend["health_score_source"] == "derived"

    @pytest.mark.asyncio
    async def test_weights_a_zero_nloc_file_as_one_line(self, tmp_path: Path) -> None:
        """A zero-nloc row still counts once, rather than dropping out of the average.

        The score is a weighted mean over nloc, so a naive weight would let an
        empty file contribute nothing. Both scores here would otherwise average
        to 80.0; the clamped weight is what makes it 60.0.
        """
        ws_config = _make_ws_config()
        _create_workspace_repo_db(
            tmp_path,
            "backend",
            health_rows=[(4.0, 0), (8.0, 1)],
        )
        app = _make_workspace_app(
            ws_config=ws_config,
            workspace_root=str(tmp_path),
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/graph")

        assert resp.status_code == 200
        backend = next(n for n in resp.json()["nodes"] if n["name"] == "backend")
        assert backend["health_score"] == 60.0


# ---------------------------------------------------------------------------
# Tests — _query_repo_stats
# ---------------------------------------------------------------------------


class TestQueryRepoStats:
    def _make_wiki_db(
        self,
        db_path: Path,
        rows: list[tuple[int, float]],
        *,
        graph_nodes: list[tuple[str, str, str]] | None = None,
    ) -> None:
        """Create a minimal wiki.db with a git_metadata table.

        ``rows`` is a list of ``(is_hotspot, churn_percentile)`` tuples.
        churn_percentile is stored on the real 0.0-1.0 scale.

        ``graph_nodes`` is an optional list of ``(node_id, node_type, language)``
        tuples inserted into ``graph_nodes``, letting a test model the mix of
        file and symbol rows the real table contains.
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path)) as conn:
            # _query_repo_stats reads several tables before the hotspot count;
            # a missing table aborts the whole block, so create the minimal set.
            conn.executescript(
                """
                CREATE TABLE repositories (id TEXT PRIMARY KEY);
                CREATE TABLE graph_nodes (
                    id TEXT PRIMARY KEY,
                    node_type TEXT NOT NULL DEFAULT 'file',
                    language TEXT,
                    symbol_count INTEGER DEFAULT 0
                );
                CREATE TABLE wiki_pages (id TEXT PRIMARY KEY, confidence REAL);
                CREATE TABLE git_metadata (
                    id TEXT PRIMARY KEY,
                    is_hotspot INTEGER NOT NULL DEFAULT 0,
                    churn_percentile REAL NOT NULL DEFAULT 0.0
                );
                INSERT INTO repositories (id) VALUES ('repo-1');
                """
            )
            for idx, (is_hotspot, churn) in enumerate(rows):
                conn.execute(
                    "INSERT INTO git_metadata (id, is_hotspot, churn_percentile) VALUES (?, ?, ?)",
                    (f"src/f{idx}.py", is_hotspot, churn),
                )
            for node_id, node_type, language in graph_nodes or []:
                conn.execute(
                    "INSERT INTO graph_nodes (id, node_type, language) VALUES (?, ?, ?)",
                    (node_id, node_type, language),
                )

    def test_hotspot_count_uses_is_hotspot_flag(self, tmp_path: Path) -> None:
        """hotspot_count reflects the canonical is_hotspot column.

        Regression for #440: the old ``churn_percentile >= 90`` predicate
        never matched because churn_percentile is stored on a 0.0-1.0 scale,
        so every repo reported 0 hotspots. The high churn values here would
        all read as < 1.0, proving the count comes from is_hotspot.
        """
        db_path = tmp_path / ".repowise" / "wiki.db"
        self._make_wiki_db(
            db_path,
            rows=[(1, 0.99), (1, 0.95), (0, 0.10)],
        )

        stats = workspace._query_repo_stats(db_path)

        assert stats["hotspot_count"] == 2

    def test_hotspot_count_zero_when_no_hotspots(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".repowise" / "wiki.db"
        self._make_wiki_db(db_path, rows=[(0, 0.99), (0, 0.80)])

        stats = workspace._query_repo_stats(db_path)

        assert stats["hotspot_count"] == 0

    
    def test_file_count_excludes_symbol_nodes(self, tmp_path: Path) -> None:
        """Regression: graph_nodes stores file *and* symbol rows.

        file_count must only count node_type='file' rows. Previously an
        unfiltered COUNT(*) inflated the number by every symbol (function,
        class, etc.) indexed alongside each file — roughly 10x on real repos.
        """
        db_path = tmp_path / ".repowise" / "wiki.db"
        self._make_wiki_db(
            db_path,
            rows=[],
            graph_nodes=[
                ("src/a.py", "file", "python"),
                ("src/b.py", "file", "python"),
                ("src/c.py", "file", "python"),
                # Every file contributes several symbol rows to the same table.
                ("src/a.py::Foo", "symbol", "python"),
                ("src/a.py::Foo.bar", "symbol", "python"),
                ("src/a.py::Foo.baz", "symbol", "python"),
                ("src/b.py::qux", "symbol", "python"),
                ("src/b.py::quux", "symbol", "python"),
                ("src/c.py::corge", "symbol", "python"),
            ],
        )

        stats = workspace._query_repo_stats(db_path)

        assert stats["file_count"] == 3

    def test_top_language_excludes_symbol_nodes(self, tmp_path: Path) -> None:
        """Regression: top-language must be derived from file rows only.

        A language with fewer files can still "win" on an unfiltered count
        if its files happen to contain lots of symbol rows. Here Python has
        more files (3) than JavaScript (1), but JavaScript's single file has
        many more symbol rows — an unfiltered query would incorrectly pick
        JavaScript as the top language.
        """
        db_path = tmp_path / ".repowise" / "wiki.db"
        self._make_wiki_db(
            db_path,
            rows=[],
            graph_nodes=[
                ("src/a.py", "file", "python"),
                ("src/b.py", "file", "python"),
                ("src/c.py", "file", "python"),
                ("src/dense.js", "file", "javascript"),
                ("src/dense.js::f1", "symbol", "javascript"),
                ("src/dense.js::f2", "symbol", "javascript"),
                ("src/dense.js::f3", "symbol", "javascript"),
                ("src/dense.js::f4", "symbol", "javascript"),
                ("src/dense.js::f5", "symbol", "javascript"),
            ],
        )

        top_language = workspace._query_top_language(db_path)

        assert top_language == "python"


# ---------------------------------------------------------------------------
# GET /api/workspace/system-graph + /diagnostics
# ---------------------------------------------------------------------------


def _make_system_graph_enricher(tmp_path: Path) -> CrossRepoEnricher:
    """Enricher backed by a real, core-built system graph artifact."""
    from repowise.core.workspace.contracts import Contract, ContractLink
    from repowise.core.workspace.cross_repo import CrossRepoOverlay, CrossRepoPackageDep
    from repowise.core.workspace.system_graph import build_system_graph

    contracts = [
        Contract(
            repo="backend",
            contract_id="http::GET::/api/users",
            contract_type="http",
            role="provider",
            file_path="routes.py",
            symbol_name="get_users",
            confidence=0.85,
        ),
        Contract(
            repo="frontend",
            contract_id="http::GET::/api/users",
            contract_type="http",
            role="consumer",
            file_path="client.ts",
            symbol_name="fetchUsers",
            confidence=0.75,
        ),
        Contract(
            repo="backend",
            contract_id="http::GET::/orphan",
            contract_type="http",
            role="provider",
            file_path="routes.py",
            symbol_name="orphan",
            confidence=0.85,
        ),
    ]
    links = [
        ContractLink(
            contract_id="http::GET::/api/users",
            contract_type="http",
            match_type="exact",
            confidence=0.75,
            provider_repo="backend",
            provider_file="routes.py",
            provider_symbol="get_users",
            provider_service=None,
            consumer_repo="frontend",
            consumer_file="client.ts",
            consumer_symbol="fetchUsers",
            consumer_service=None,
        ),
    ]
    overlay = CrossRepoOverlay(
        package_deps=[
            CrossRepoPackageDep(
                source_repo="frontend",
                target_repo="backend",
                source_manifest="package.json",
                kind="npm_local_path",
            ),
        ]
    )
    graph = build_system_graph(contracts, links, overlay, {}, generated_at="t")

    _write_json(tmp_path / "system_graph.json", graph.to_dict())
    return CrossRepoEnricher(
        tmp_path / "cross_repo_edges.json",
        system_graph_path=tmp_path / "system_graph.json",
    )


class TestGetSystemGraph:
    @pytest.mark.asyncio
    async def test_not_workspace_mode(self) -> None:
        app = _make_workspace_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/system-graph")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_when_no_graph(self) -> None:
        app = _make_workspace_app(ws_config=_make_ws_config(), enricher=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/system-graph")
        assert resp.status_code == 200
        assert resp.json()["nodes"] == []

    @pytest.mark.asyncio
    async def test_returns_nodes_and_typed_edges(self, tmp_path: Path) -> None:
        enricher = _make_system_graph_enricher(tmp_path)
        app = _make_workspace_app(ws_config=_make_ws_config(), enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/system-graph")
        assert resp.status_code == 200
        data = resp.json()
        assert {n["id"] for n in data["nodes"]} == {"backend", "frontend"}
        kinds = {(e["source"], e["target"], e["kind"]) for e in data["edges"]}
        assert ("frontend", "backend", "http") in kinds  # consumer -> provider
        assert ("frontend", "backend", "package") in kinds  # dependent -> dependency


class TestGetBlastRadius:
    @pytest.mark.asyncio
    async def test_not_workspace_mode(self) -> None:
        app = _make_workspace_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/blast-radius", params={"target": "backend"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_when_no_graph(self) -> None:
        app = _make_workspace_app(ws_config=_make_ws_config(), enricher=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/blast-radius", params={"target": "backend"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["impacted"] == []
        assert data["unresolved_targets"] == ["backend"]

    @pytest.mark.asyncio
    async def test_changing_provider_impacts_consumer(self, tmp_path: Path) -> None:
        enricher = _make_system_graph_enricher(tmp_path)
        app = _make_workspace_app(ws_config=_make_ws_config(), enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/blast-radius", params={"target": "backend"})
        assert resp.status_code == 200
        data = resp.json()
        # frontend consumes backend's http contract AND package-depends on it.
        assert "frontend" in {n["id"] for n in data["impacted"]}
        assert data["structural_count"] >= 1
        assert "frontend" in data["impacted_repos"]

    @pytest.mark.asyncio
    async def test_unresolved_target_reported(self, tmp_path: Path) -> None:
        enricher = _make_system_graph_enricher(tmp_path)
        app = _make_workspace_app(ws_config=_make_ws_config(), enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/blast-radius", params={"target": "ghost"})
        data = resp.json()
        assert data["unresolved_targets"] == ["ghost"]
        assert data["impacted"] == []

    @pytest.mark.asyncio
    async def test_target_is_required(self) -> None:
        app = _make_workspace_app(ws_config=_make_ws_config())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/blast-radius")
        assert resp.status_code == 422  # missing required query param


class TestGetDiagnostics:
    @pytest.mark.asyncio
    async def test_not_workspace_mode(self) -> None:
        app = _make_workspace_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/diagnostics")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_reports_orphans_and_counts(self, tmp_path: Path) -> None:
        enricher = _make_system_graph_enricher(tmp_path)
        app = _make_workspace_app(ws_config=_make_ws_config(), enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/diagnostics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_providers"] == 2
        assert data["total_consumers"] == 1
        assert data["total_links"] == 1
        assert len(data["orphan_providers"]) == 1
        assert data["orphan_providers"][0]["contract_id"] == "http::GET::/orphan"


# ---------------------------------------------------------------------------
# GET /api/workspace/breaking-changes
# ---------------------------------------------------------------------------


def _make_breaking_enricher(tmp_path: Path) -> CrossRepoEnricher:
    """Enricher backed by a real, core-built breaking-change report artifact."""
    from repowise.core.workspace.breaking_change import detect_breaking_changes
    from repowise.core.workspace.contracts import Contract, ContractLink, ContractStore

    prev = ContractStore(
        contracts=[
            Contract(
                repo="backend",
                contract_id="http::GET::/api/users",
                contract_type="http",
                role="provider",
                file_path="routes.py",
                symbol_name="get_users",
                confidence=0.85,
            ),
        ],
        contract_links=[
            ContractLink(
                contract_id="http::GET::/api/users",
                contract_type="http",
                match_type="exact",
                confidence=0.75,
                provider_repo="backend",
                provider_file="routes.py",
                provider_symbol="get_users",
                provider_service=None,
                consumer_repo="frontend",
                consumer_file="client.ts",
                consumer_symbol="fetchUsers",
                consumer_service=None,
            ),
        ],
    )
    report = detect_breaking_changes(prev, ContractStore(), generated_at="t")
    _write_json(tmp_path / "breaking_changes.json", report.to_dict())
    return CrossRepoEnricher(
        tmp_path / "cross_repo_edges.json",
        breaking_changes_path=tmp_path / "breaking_changes.json",
    )


class TestGetBreakingChanges:
    @pytest.mark.asyncio
    async def test_not_workspace_mode(self) -> None:
        app = _make_workspace_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/breaking-changes")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_when_no_report(self) -> None:
        app = _make_workspace_app(ws_config=_make_ws_config(), enricher=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/breaking-changes")
        assert resp.status_code == 200
        assert resp.json()["changes"] == []

    @pytest.mark.asyncio
    async def test_reports_removed_endpoint_with_consumer(self, tmp_path: Path) -> None:
        enricher = _make_breaking_enricher(tmp_path)
        app = _make_workspace_app(ws_config=_make_ws_config(), enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/breaking-changes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["breaking_count"] == 1
        assert data["changes"][0]["kind"] == "removed_endpoint"
        assert data["changes"][0]["impacted_consumers"][0]["repo"] == "frontend"
        assert data["impacted_repos"] == ["frontend"]

    @pytest.mark.asyncio
    async def test_filter_by_repo_recomputes_rollups(self, tmp_path: Path) -> None:
        enricher = _make_breaking_enricher(tmp_path)
        app = _make_workspace_app(ws_config=_make_ws_config(), enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/breaking-changes", params={"repo": "nope"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["changes"] == []
        assert data["total"] == 0
        assert data["impacted_repos"] == []


# ---------------------------------------------------------------------------
# GET /api/workspace/conformance
# ---------------------------------------------------------------------------


def _make_conformance_enricher(tmp_path: Path) -> CrossRepoEnricher:
    """Enricher backed by a real, core-built conformance report artifact."""
    from repowise.core.workspace.config import ConformanceRule
    from repowise.core.workspace.conformance import build_conformance_report
    from repowise.core.workspace.system_graph import SystemEdge, SystemGraph, SystemNode

    graph = SystemGraph(
        nodes=[
            SystemNode(id="frontend", repo="frontend", service_path=None, name="frontend"),
            SystemNode(id="db", repo="db", service_path=None, name="db"),
        ],
        edges=[
            SystemEdge(
                id="frontend->db:http",
                source="frontend",
                target="db",
                kind="http",
                match_type="exact",
                confidence=1.0,
                weight=1,
                structural=True,
            ),
            SystemEdge(
                id="db->frontend:http",
                source="db",
                target="frontend",
                kind="http",
                match_type="exact",
                confidence=1.0,
                weight=1,
                structural=True,
            ),
        ],
    )
    report = build_conformance_report(
        graph, [ConformanceRule(source="frontend", target="db")], generated_at="t"
    )
    _write_json(tmp_path / "conformance.json", report.to_dict())
    return CrossRepoEnricher(
        tmp_path / "cross_repo_edges.json",
        conformance_path=tmp_path / "conformance.json",
    )


class TestGetConformance:
    @pytest.mark.asyncio
    async def test_not_workspace_mode(self) -> None:
        app = _make_workspace_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/conformance")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_when_no_report(self) -> None:
        app = _make_workspace_app(ws_config=_make_ws_config(), enricher=None)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/conformance")
        assert resp.status_code == 200
        body = resp.json()
        assert body["violations"] == []
        assert body["cycles"] == []

    @pytest.mark.asyncio
    async def test_reports_violation_and_cycle(self, tmp_path: Path) -> None:
        enricher = _make_conformance_enricher(tmp_path)
        app = _make_workspace_app(ws_config=_make_ws_config(), enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/conformance")
        assert resp.status_code == 200
        data = resp.json()
        assert data["violation_count"] == 1
        assert data["cycle_count"] == 1
        assert data["violations"][0]["source"] == "frontend"
        assert data["violations"][0]["target"] == "db"
        assert set(data["violating_repos"]) == {"frontend", "db"}

    @pytest.mark.asyncio
    async def test_filter_by_repo(self, tmp_path: Path) -> None:
        enricher = _make_conformance_enricher(tmp_path)
        app = _make_workspace_app(ws_config=_make_ws_config(), enricher=enricher)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/conformance", params={"repo": "frontend"})
        assert resp.status_code == 200
        data = resp.json()
        # frontend participates in both the violation and the cycle
        assert data["violation_count"] == 1
        assert data["cycle_count"] == 1

        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/workspace/conformance", params={"repo": "unrelated"})
        data = resp.json()
        assert data["violation_count"] == 0
        assert data["cycle_count"] == 0


# ---------------------------------------------------------------------------
# Tests — per-repo query budget
# ---------------------------------------------------------------------------


class TestRepoQueryBudget:
    """Pin the per-repo sqlite cost of the workspace listing.

    Workspace repos each live in their own wiki.db, so unlike the dashboard's
    /api/repos/summary there is no shared table to GROUP BY and the total query
    count is necessarily O(repos). What must stay flat is the cost *per* repo:
    this fails if someone adds a query to the loop or reintroduces a second
    sweep over the same databases.
    """

    QUERIES_PER_REPO = 7
    CONNECTIONS_PER_REPO = 2

    def _config_for(self, aliases: list[str]):
        ws_config = MagicMock()
        repos = []
        for i, alias in enumerate(aliases):
            r = MagicMock()
            r.alias = alias
            r.path = f"./{alias}"
            r.is_primary = i == 0
            r.indexed_at = None
            r.last_commit_at_index = None
            repos.append(r)
        ws_config.repos = repos
        ws_config.default_repo = aliases[0]
        return ws_config

    async def _measure(self, tmp_path: Path, count: int) -> tuple[int, int]:
        """Return (statements, connections) for one GET /api/workspace."""
        root = tmp_path / f"ws{count}"
        aliases = [f"repo{i}" for i in range(count)]
        for alias in aliases:
            _create_workspace_repo_db(root, alias, health_rows=[(7.0, 100)])

        statements = 0
        connections = 0
        real_connect = sqlite3.connect

        def counting_connect(*args, **kwargs):
            nonlocal connections
            connections += 1
            conn = real_connect(*args, **kwargs)

            def trace(_stmt):
                nonlocal statements
                statements += 1

            conn.set_trace_callback(trace)
            return conn

        app = _make_workspace_app(
            ws_config=self._config_for(aliases),
            workspace_root=str(root),
        )
        transport = ASGITransport(app=app)
        with patch.object(sqlite3, "connect", counting_connect):
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.get("/api/workspace")
        assert resp.status_code == 200
        assert len(resp.json()["repos"]) == count
        return statements, connections

    @pytest.mark.asyncio
    async def test_per_repo_cost_does_not_grow_with_repo_count(
        self, tmp_path: Path
    ) -> None:
        two_stmts, two_conns = await self._measure(tmp_path, 2)
        six_stmts, six_conns = await self._measure(tmp_path, 6)

        assert two_stmts / 2 == six_stmts / 6, (
            f"per-repo statements moved from {two_stmts / 2} (2 repos) to "
            f"{six_stmts / 6} (6 repos) — the loop is doing more work per repo"
        )
        assert two_conns / 2 == six_conns / 6, (
            f"per-repo connections moved from {two_conns / 2} to {six_conns / 6}"
        )

    @pytest.mark.asyncio
    async def test_per_repo_cost_is_pinned(self, tmp_path: Path) -> None:
        """Pinned so "flat" has a value, and so a second sweep cannot slip back in.

        Six stats queries plus one weighted health average, over one connection
        for the stats block and one for the health read.
        """
        statements, connections = await self._measure(tmp_path, 3)
        assert statements == self.QUERIES_PER_REPO * 3
        assert connections == self.CONNECTIONS_PER_REPO * 3
