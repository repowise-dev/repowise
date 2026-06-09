"""MCP database engines must use the shared persistence factory.

The core factory installs SQLite WAL and busy_timeout pragmas. Issue #88
called out that MCP startup paths were bypassing that factory with raw
``create_async_engine`` calls, so these tests exercise the MCP-specific paths
and inspect the live connection pragmas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.core.workspace.registry import RepoRegistry


async def _read_pragma(session_factory: Any, pragma: str) -> str:
    async with session_factory() as session:
        result = await session.execute(text(f"PRAGMA {pragma}"))
        row = result.fetchone()
        return str(row[0]) if row is not None else ""


@pytest.mark.asyncio
async def test_single_repo_mcp_engine_uses_sqlite_pragmas(tmp_path: Path) -> None:
    from repowise.server.mcp_server import _server, _state

    repo_path = tmp_path / "repo"
    (repo_path / ".repowise").mkdir(parents=True)

    _state._repo_path = str(repo_path)
    try:
        async with _server._lifespan(_server.mcp):
            assert _state._session_factory is not None
            assert (await _read_pragma(_state._session_factory, "journal_mode")).lower() == "wal"
            assert int(await _read_pragma(_state._session_factory, "busy_timeout")) >= 1000
    finally:
        _state._repo_path = None
        _state._session_factory = None
        _state._fts = None
        _state._vector_store = None
        _state._decision_store = None
        _state._vector_store_ready = None


@pytest.mark.asyncio
async def test_workspace_registry_engine_uses_sqlite_pragmas(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    repo_path = workspace_root / "api"
    (repo_path / ".repowise").mkdir(parents=True)

    ws_config = WorkspaceConfig(
        repos=[RepoEntry(path="api", alias="api", is_primary=True)],
        default_repo="api",
    )
    registry = RepoRegistry(workspace_root=workspace_root, ws_config=ws_config)

    try:
        ctx = await registry.get("api")
        assert (await _read_pragma(ctx.session_factory, "journal_mode")).lower() == "wal"
        assert int(await _read_pragma(ctx.session_factory, "busy_timeout")) >= 1000
    finally:
        await registry.close()
