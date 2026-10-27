"""Regression tests for shared state across Streamable HTTP MCP sessions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

import repowise.server.mcp_server as mcp_mod
from repowise.server.mcp_server import _server
from repowise.server.mcp_server._helpers import _resolve_repo_context


@pytest.fixture(autouse=True)
def restore_mcp_state():
    saved = (
        mcp_mod._registry,
        mcp_mod._workspace_root,
        mcp_mod._cross_repo_enricher,
        mcp_mod._session_factory,
        mcp_mod._fts,
        mcp_mod._vector_store,
        _server._state._decision_store,
        _server._state._repo_path,
        _server._state._lancedb_ready,
        _server._state._vector_store_ready,
    )
    yield
    (
        mcp_mod._registry,
        mcp_mod._workspace_root,
        mcp_mod._cross_repo_enricher,
        mcp_mod._session_factory,
        mcp_mod._fts,
        mcp_mod._vector_store,
        _server._state._decision_store,
        _server._state._repo_path,
        _server._state._lancedb_ready,
        _server._state._vector_store_ready,
    ) = saved


def _install_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_first_start: bool = False,
) -> SimpleNamespace:
    contexts = {
        alias: SimpleNamespace(
            alias=alias,
            session_factory=object(),
            fts=object(),
            vector_store=object(),
            decision_store=object(),
            vector_store_ready=asyncio.Event(),
        )
        for alias in ("boot", "gateway")
    }
    registries = []
    cancelled_tasks = []

    class WorkspaceConfig:
        repos: ClassVar[list[SimpleNamespace]] = [
            SimpleNamespace(alias="boot"),
            SimpleNamespace(alias="gateway"),
        ]
        default_repo = "boot"

        def get_repo(self, alias: str):
            return next((repo for repo in self.repos if repo.alias == alias), None)

    class Registry:
        def __init__(self, **_kwargs) -> None:
            self.closed = False
            registries.append(self)

        async def get_default(self):
            await asyncio.sleep(0)
            if fail_first_start and len(registries) == 1:
                raise RuntimeError("workspace startup failed")
            return contexts["boot"]

        def get_all_aliases(self):
            return list(contexts)

        def get_default_alias(self):
            return "boot"

        def resolve_repo_param(self, alias):
            return alias

        async def get(self, alias):
            return contexts[alias]

        async def close(self):
            self.closed = True

    async def idle_task() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled_tasks.append(True)

    monkeypatch.setattr(_server, "_detect_workspace", lambda _path: (tmp_path, WorkspaceConfig(), None))
    monkeypatch.setattr(_server, "_warm_lancedb", idle_task)
    monkeypatch.setattr(_server, "_poll_release_check", idle_task)
    monkeypatch.setattr("repowise.core.workspace.registry.RepoRegistry", Registry)
    return SimpleNamespace(registries=registries, cancelled_tasks=cancelled_tasks)


@pytest.mark.asyncio
async def test_overlapping_sessions_share_lifespan_and_reenter_after_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _install_workspace(tmp_path, monkeypatch)

    session_a = _server.mcp._mcp_server.lifespan(_server.mcp._mcp_server)
    session_b = _server.mcp._mcp_server.lifespan(_server.mcp._mcp_server)
    await asyncio.gather(session_a.__aenter__(), session_b.__aenter__())
    try:
        assert len(workspace.registries) == 1
        registry = workspace.registries[0]
        assert (await _resolve_repo_context("gateway")).alias == "gateway"

        await session_b.__aexit__(None, None, None)
        assert not registry.closed
        assert mcp_mod._registry is not None
        assert (await _resolve_repo_context("gateway")).alias == "gateway"

    finally:
        await session_a.__aexit__(None, None, None)

    assert registry.closed
    assert mcp_mod._registry is None
    assert mcp_mod._workspace_root is None
    assert len(workspace.cancelled_tasks) == 2

    new_session = _server.mcp._mcp_server.lifespan(_server.mcp._mcp_server)
    await new_session.__aenter__()
    try:
        assert len(workspace.registries) == 2
        assert (await _resolve_repo_context("gateway")).alias == "gateway"
    finally:
        await new_session.__aexit__(None, None, None)
    assert workspace.registries[1].closed
    assert len(workspace.cancelled_tasks) == 4


@pytest.mark.asyncio
async def test_failed_startup_can_be_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _install_workspace(tmp_path, monkeypatch, fail_first_start=True)
    failed_session = _server.mcp._mcp_server.lifespan(_server.mcp._mcp_server)

    with pytest.raises(RuntimeError, match="workspace startup failed"):
        await failed_session.__aenter__()

    assert mcp_mod._registry is None
    assert _server._state._lancedb_ready is None
    assert len(workspace.cancelled_tasks) == 2

    retry_session = _server.mcp._mcp_server.lifespan(_server.mcp._mcp_server)
    await retry_session.__aenter__()
    try:
        assert len(workspace.registries) == 2
        assert (await _resolve_repo_context("gateway")).alias == "gateway"
    finally:
        await retry_session.__aexit__(None, None, None)

    assert workspace.registries[1].closed
    assert len(workspace.cancelled_tasks) == 4


@pytest.mark.asyncio
async def test_cancelled_last_session_still_cleans_up_lifespan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _install_workspace(tmp_path, monkeypatch)
    session = _server.mcp._mcp_server.lifespan(_server.mcp._mcp_server)
    await session.__aenter__()
    registry = workspace.registries[0]

    await session.__aexit__(asyncio.CancelledError, asyncio.CancelledError(), None)

    assert registry.closed
    assert mcp_mod._registry is None
    assert mcp_mod._workspace_root is None
    assert len(workspace.cancelled_tasks) == 2
