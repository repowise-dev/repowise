"""Cancelling index reads must return their real SQLite connections."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from repowise.core.workspace import repo_index as module
from tests.unit.workspace._repo_index import make_repo_index


@pytest.mark.parametrize("operation", ["open", "close"])
async def test_cancelled_cleanup_returns_connection(tmp_path, monkeypatch, operation):
    index = await make_repo_index(tmp_path, {})
    closing, resume = asyncio.Event(), asyncio.Event()
    opened, returned = [], []

    def block_close(index):
        opened.append(index)
        event.listen(index._engine.sync_engine, "checkin", lambda c, r: returned.append(True))
        original_close = index.session.close

        async def close():
            closing.set()
            await resume.wait()
            await original_close()

        monkeypatch.setattr(index.session, "close", close)

    if operation == "open":
        await index.close()

        async def fail(index, *args):
            block_close(index)
            raise LookupError("load failed")

        monkeypatch.setattr(module.RepoIndex, "_load", fail)
        task = asyncio.create_task(module.open_repo_index("alpha", tmp_path))
    else:
        block_close(index)
        task = asyncio.create_task(index.close())
    try:
        await closing.wait()
        task.cancel()
        await asyncio.sleep(0)
        # An anyio cancel scope cancels again on every pass of the loop.
        task.cancel()
        resume.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert returned
    finally:
        resume.set()
        for index in opened:
            await index.close()


async def test_cancelled_close_preserves_cancellation_when_session_close_fails():
    closing, resume = asyncio.Event(), asyncio.Event()
    disposed = []

    async def close():
        closing.set()
        await resume.wait()
        raise RuntimeError("close failed")

    async def dispose():
        disposed.append(True)

    task = asyncio.create_task(
        module._close_index_db(SimpleNamespace(close=close), SimpleNamespace(dispose=dispose))
    )
    await closing.wait()
    task.cancel()
    resume.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert disposed


@pytest.mark.parametrize("close_fails", [False, True])
async def test_cancelled_workspace_open_closes_already_opened_siblings(
    tmp_path, monkeypatch, close_fails
):
    indexes = {
        alias: await make_repo_index(tmp_path / alias, {}, alias=alias)
        for alias in ("alpha", "beta")
    }
    returned = []
    for alias, index in indexes.items():
        event.listen(
            index._engine.sync_engine,
            "checkin",
            lambda c, r, alias=alias: returned.append(alias),
        )
    original_close = indexes["alpha"].session.close
    closing, resume = asyncio.Event(), asyncio.Event()

    async def block_close():
        closing.set()
        await resume.wait()
        if close_fails:
            raise RuntimeError("close failed")
        await original_close()

    monkeypatch.setattr(indexes["alpha"].session, "close", block_close)
    ready = {alias: asyncio.Event() for alias in indexes}
    third_started = asyncio.Event()

    async def open_index(alias, path):
        if alias in indexes:
            ready[alias].set()
            return indexes[alias]
        third_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(module, "open_repo_index", open_index)
    openable = {alias: tmp_path / alias for alias in ("alpha", "beta", "gamma")}
    workspace = module.WorkspaceIndex({}, openable)
    task = asyncio.create_task(workspace.ensure(openable))
    try:
        for opened in ready.values():
            await opened.wait()
        await third_started.wait()
        task.cancel()
        await asyncio.wait_for(closing.wait(), timeout=5)
        task.cancel()
        resume.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert returned == (["beta"] if close_fails else ["alpha", "beta"])
        reopened = {}

        async def reopen_index(alias, path):
            reopened[alias] = object()
            return reopened[alias]

        monkeypatch.setattr(module, "open_repo_index", reopen_index)
        await workspace.ensure(openable)
        assert reopened.keys() == openable.keys()
        for alias in openable:
            assert workspace.get(alias) is reopened[alias]
    finally:
        monkeypatch.setattr(indexes["alpha"].session, "close", original_close)
        for index in indexes.values():
            await index.close()
