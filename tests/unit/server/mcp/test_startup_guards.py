"""Startup failures exit once with a named cause instead of respawning.

An MCP host restarts a stdio server that dies at startup. Two failures used
to escape as bare tracebacks and loop that way: an index directory the
process cannot create or open, and a tool module whose optional dependency
is missing. The first now raises a message naming the path and the fix; the
second is skipped so the rest of the surface still serves.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from pathlib import Path
from typing import ClassVar

import pytest

import repowise.server.mcp_server as mcp_pkg
from repowise.server.mcp_server import _server, _state
from repowise.server.mcp_server._server import StoreUnavailableError


@pytest.mark.asyncio
async def test_lifespan_names_the_path_when_the_store_cannot_be_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".repowise").write_text("not a directory\n")
    for name in ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))

    async def _no_warm() -> None:
        return None

    monkeypatch.setattr(_server, "_warm_lancedb", _no_warm)
    monkeypatch.setattr(_server, "_detect_workspace", lambda _p: (None, None, None))

    with pytest.raises(StoreUnavailableError) as raised:
        async with _server._lifespan(_server.mcp):
            pass
    message = str(raised.value)
    assert str(tmp_path / ".repowise") in message
    assert "repowise init" in message


def test_missing_tool_dependency_skips_that_tool_and_keeps_the_rest(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real_import = importlib.import_module
    victim = f"{mcp_pkg.__name__}.tool_flows"

    def _import(name: str, package: str | None = None):
        if name == victim:
            raise ModuleNotFoundError("No module named 'lancedb'", name="lancedb")
        return real_import(name, package)

    monkeypatch.setattr(mcp_pkg, "_surface_applied", False)
    monkeypatch.setattr(importlib, "import_module", _import)

    with caplog.at_level(logging.WARNING, logger="repowise.mcp"):
        server = mcp_pkg.ensure_full_surface()

    assert server is _server.mcp
    record = next(r for r in caplog.records if "skipping tool module" in r.getMessage())
    assert "tool_flows" in record.getMessage()
    assert "lancedb" in record.getMessage()


@pytest.mark.asyncio
async def test_configured_database_failure_names_the_url_not_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite:///{(tmp_path / 'elsewhere.db').as_posix()}")
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))

    async def _no_warm() -> None:
        return None

    async def _refuse(_engine) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(_server, "_warm_lancedb", _no_warm)
    monkeypatch.setattr(_server, "_detect_workspace", lambda _p: (None, None, None))
    monkeypatch.setattr(_server, "init_db", _refuse)

    with pytest.raises(StoreUnavailableError) as raised:
        async with _server._lifespan(_server.mcp):
            pass
    message = str(raised.value)
    assert "REPOWISE_DB_URL" in message
    assert "repowise init" not in message


@pytest.mark.asyncio
async def test_workspace_startup_failure_cancels_the_background_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[asyncio.Task] = []
    real_create_task = asyncio.create_task

    def _spy(coro, **kw):
        task = real_create_task(coro, **kw)
        started.append(task)
        return task

    async def _no_warm() -> None:
        await asyncio.sleep(3600)

    class _Registry:
        def __init__(self, **_kw) -> None:
            pass

        async def get_default(self):
            raise RuntimeError("workspace store missing")

    class _Config:
        repos: ClassVar[list] = []

        def get_repo(self, _alias):
            return None

    monkeypatch.setattr(_server, "_warm_lancedb", _no_warm)
    monkeypatch.setattr(_server, "_detect_workspace", lambda _p: (tmp_path, _Config(), None))
    monkeypatch.setattr("repowise.core.workspace.registry.RepoRegistry", _Registry)
    monkeypatch.setattr(_server.asyncio, "create_task", _spy)

    with pytest.raises(RuntimeError):
        async with _server._lifespan(_server.mcp):
            pass
    assert started and all(t.done() for t in started)
