"""Startup failures serve a named cause instead of respawning.

An MCP host restarts a stdio server that dies at startup. Two failures used
to escape as bare tracebacks and loop that way: an index directory the
process cannot create or open, and a tool module whose optional dependency
is missing. The first is served: every tool answers with a message naming the
path and the fix. The second is skipped so the rest of the surface still
serves. A directory that was never indexed is not a failure either: the server
starts and its tools say so.
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


async def _no_warm() -> None:
    return None


def _single_repo(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    for name in ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_state, "_repo_path", str(repo))
    monkeypatch.setattr(_server, "_warm_lancedb", _no_warm)
    monkeypatch.setattr(_server, "_detect_workspace", lambda _p: (None, None, None))


async def _tool_answer() -> dict:
    from repowise.server.mcp_server import get_symbol
    from repowise.server.mcp_server._failure_shield import shield

    return await shield(get_symbol)("pkg/mod.py::alpha")


@pytest.mark.asyncio
async def test_a_store_that_cannot_open_is_served_with_the_path_and_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Served, not exited: a host respawns a server that exits at startup."""
    (tmp_path / ".repowise").write_text("not a directory\n")
    _single_repo(monkeypatch, tmp_path)

    async with _server._lifespan(_server.mcp):
        result = await _tool_answer()
        assert _state._store_error.reason == "store_open_failed"

    assert str(tmp_path / ".repowise") in result["error"]
    assert "repowise init" in result["error"]
    assert "restart the MCP server" in result["remedy"]
    assert _state._serving_no_index is True
    assert _state._lancedb_ready is None


@pytest.mark.asyncio
async def test_a_store_served_as_unavailable_does_not_outlive_its_lifespan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The next lifespan in the process opens its own store afresh."""
    (tmp_path / ".repowise").write_text("not a directory\n")
    _single_repo(monkeypatch, tmp_path)
    async with _server._lifespan(_server.mcp):
        pass

    unindexed = tmp_path / "other"
    unindexed.mkdir()
    monkeypatch.setattr(_state, "_repo_path", str(unindexed))
    async with _server._lifespan(_server.mcp):
        result = await _tool_answer()

    assert _state._store_error is None
    assert "no repowise index" in result["error"].lower()


@pytest.mark.asyncio
async def test_lifespan_raises_when_even_the_empty_store_cannot_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".repowise").write_text("not a directory\n")
    _single_repo(monkeypatch, tmp_path)

    async def _refuse(_engine) -> None:
        raise OSError("out of memory")

    monkeypatch.setattr(_server, "init_db", _refuse)

    with pytest.raises(StoreUnavailableError) as raised:
        async with _server._lifespan(_server.mcp):
            pass
    message = str(raised.value)
    assert str(tmp_path / ".repowise") in message
    assert raised.value.reason == "store_open_failed"
    # The failure path never reaches the teardown, so it has to leave the
    # warmup event cleared itself. A leaked event stays bound to this test's
    # loop and makes every later tool call raise inside the failure shield.
    assert _state._lancedb_ready is None


@pytest.mark.asyncio
async def test_unindexed_directory_starts_and_reports_no_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory with no .repowise/ serves, rather than exiting, and stays clean.

    Exiting made the host respawn the server in a loop and the agent never saw
    the cause. Creating the directory instead left a stray .repowise/ and an
    empty wiki.db that made the directory look initialised (#3163).
    """
    from repowise.server.mcp_server import get_symbol
    from repowise.server.mcp_server._failure_shield import shield

    for name in ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))

    async def _no_warm() -> None:
        return None

    monkeypatch.setattr(_server, "_warm_lancedb", _no_warm)
    monkeypatch.setattr(_server, "_detect_workspace", lambda _p: (None, None, None))

    async with _server._lifespan(_server.mcp):
        result = await shield(get_symbol)("pkg/mod.py::alpha")

    assert "no repowise index" in result["error"].lower()
    assert "repowise init" in result["remedy"]
    assert _state._serving_no_index is True
    assert not (tmp_path / ".repowise").exists()
    assert _state._lancedb_ready is None


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
    real_init_db = _server.init_db

    async def _refuse(engine) -> None:
        if ":memory:" not in str(engine.url):
            raise OSError("connection refused")
        await real_init_db(engine)

    monkeypatch.setattr(_server, "_warm_lancedb", _no_warm)
    monkeypatch.setattr(_server, "_detect_workspace", lambda _p: (None, None, None))
    monkeypatch.setattr(_server, "init_db", _refuse)

    async with _server._lifespan(_server.mcp):
        result = await _tool_answer()
        assert _state._store_error.reason == "db_url_unreachable"

    assert "REPOWISE_DB_URL" in result["error"]
    assert "repowise init" not in result["error"]


@pytest.mark.asyncio
async def test_workspace_default_store_failure_is_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Registry:
        def __init__(self, **_kw) -> None:
            pass

        async def get_default(self):
            raise OSError("permission denied")

        async def close(self) -> None:
            pass

    class _Config:
        repos: ClassVar[list] = []

        def get_repo(self, _alias):
            return None

    for name in ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    monkeypatch.setattr(_server, "_warm_lancedb", _no_warm)
    monkeypatch.setattr(_server, "_detect_workspace", lambda _p: (tmp_path, _Config(), None))
    monkeypatch.setattr("repowise.core.workspace.registry.RepoRegistry", _Registry)

    async with _server._lifespan(_server.mcp):
        result = await _tool_answer()
        assert _state._registry is None
        assert _state._store_error.reason == "workspace_default_failed"

    assert "permission denied" in result["error"]
    assert not (tmp_path / ".repowise").exists()


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
