"""The CLI's wiring of MCP tool state.

The tools read their session factory, full-text index and vector store out of
``mcp_server._state``, which the MCP server's lifespan normally publishes.
This bridge is the CLI's caller of the same publisher, and the two things that
can go wrong are silent: publishing the wrong objects (the tool answers from
another repo's database) and not tearing down (a leaked engine per invocation,
invisible in a one-shot process and fatal in a test suite).
"""

from __future__ import annotations

import pytest

from repowise.cli import tool_bridge


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _FakeStore:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Patch the bridge's collaborators and record what it published."""
    engine = _FakeEngine()
    store = _FakeStore()
    published: dict = {}

    monkeypatch.setattr(
        "repowise.core.persistence.create_engine", lambda url: engine, raising=False
    )
    monkeypatch.setattr(
        "repowise.core.persistence.FullTextSearch", lambda eng: ("fts", eng), raising=False
    )
    monkeypatch.setattr(
        "repowise.cli.helpers.get_db_url_for_repo", lambda p: f"sqlite+aiosqlite:///{p}"
    )

    async def _fake_open(repo_path):
        return store

    monkeypatch.setattr(tool_bridge, "_open_vector_store", _fake_open)

    def _fake_init(session_factory, fts, vector_store, decision_store=None, repo_path=None):
        published.update(
            session_factory=session_factory,
            fts=fts,
            vector_store=vector_store,
            decision_store=decision_store,
            repo_path=repo_path,
        )

    monkeypatch.setattr("repowise.server.chat_tools.init_tool_state", _fake_init)
    return engine, store, published, tmp_path


def test_it_publishes_this_repos_resources_and_tears_them_down(wired):
    engine, store, published, repo = wired

    async def _tool():
        return {"ok": True}

    assert tool_bridge.call_tool(repo, _tool) == {"ok": True}
    assert published["vector_store"] is store
    # The server points both at one store: decisions share the page table under
    # a "decision:" namespace rather than living in a separate one.
    assert published["decision_store"] is store
    assert published["repo_path"] == str(repo)
    assert published["fts"] == ("fts", engine)
    assert engine.disposed and store.closed


def test_a_failing_tool_still_disposes_the_engine(wired):
    engine, store, _published, repo = wired

    async def _tool():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        tool_bridge.call_tool(repo, _tool)
    assert engine.disposed and store.closed


def test_a_store_that_fails_to_close_does_not_lose_the_answer(wired, monkeypatch):
    """Teardown of a store the tool already answered from is not the answer."""
    engine, store, _published, repo = wired

    async def _explode():
        raise OSError("lancedb handle already gone")

    monkeypatch.setattr(store, "close", _explode)

    async def _tool():
        return {"ok": True}

    assert tool_bridge.call_tool(repo, _tool) == {"ok": True}
    assert engine.disposed


def test_the_factory_runs_inside_the_loop_after_the_state_is_published(wired):
    """A coroutine built before the wiring would see the previous repo's state."""
    _engine, _store, published, repo = wired
    seen: list = []

    async def _tool():
        seen.append(dict(published))
        return {}

    tool_bridge.call_tool(repo, _tool)
    assert seen and seen[0]["repo_path"] == str(repo)


def test_it_falls_back_to_an_in_memory_store_when_the_repo_has_no_lancedb(
    monkeypatch, tmp_path
):
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    monkeypatch.setattr(
        "repowise.cli.providers.embedders.resolve_embedder_for_repo", lambda p: "mock"
    )
    monkeypatch.setattr("repowise.cli.providers.embedders.build_embedder", lambda name: object())

    import asyncio

    store = asyncio.run(tool_bridge._open_vector_store(tmp_path))
    assert isinstance(store, InMemoryVectorStore)
