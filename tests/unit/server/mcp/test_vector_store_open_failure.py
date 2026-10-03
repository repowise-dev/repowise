"""A semantic index on disk that cannot be opened must not pass for healthy.

A partial LanceDB install imports fine and lacks ``connect_async``. The server
caught that, served an empty in-memory store, and every response still said
``embedder_degraded: false``. Only an index that exists and fails counts: no
index at all is a keyless repo, not a failure.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from repowise.core.persistence.vector_store import InMemoryVectorStore, LanceDBVectorStore
from repowise.core.persistence.vector_store.lancedb_store import LanceDBUnavailableError
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.server.mcp_server import _server, _state
from repowise.server.mcp_server._meta import build_meta


@pytest.fixture(autouse=True)
def fresh_store_errors(monkeypatch):
    monkeypatch.setattr(_state, "_vector_store_errors", {}, raising=False)


@pytest.fixture
def healthy_openai(monkeypatch):
    monkeypatch.setattr(
        _state,
        "_embedder_status",
        {"active": "openai", "requested": "openai", "degraded": False},
        raising=False,
    )
    monkeypatch.setattr(_state, "_vector_store", None, raising=False)
    monkeypatch.setattr(_state, "_decision_store", None, raising=False)
    monkeypatch.setattr(_state, "_vector_store_ready", None, raising=False)
    monkeypatch.setattr(_server, "_query_embedder", MockEmbedder)


@pytest.fixture
def broken_lancedb(monkeypatch):
    async def _boom(self):
        raise LanceDBUnavailableError("LanceDB is missing or broken (AttributeError: connect_async)")

    monkeypatch.setattr(LanceDBVectorStore, "_ensure_connected", _boom)


def test_an_unopenable_index_marks_the_embedder_degraded(
    tmp_path, healthy_openai, broken_lancedb
):
    (tmp_path / ".repowise" / "lancedb").mkdir(parents=True)

    asyncio.run(_server._load_vector_stores(str(tmp_path)))

    assert isinstance(_state._vector_store, InMemoryVectorStore)
    meta = build_meta(timing_ms=1.0)
    assert meta["embedder_degraded"] is True
    assert meta["semantic_search"] is False
    assert "connect_async" in meta["embedder_warning"]
    assert "reinstall" in meta["embedder_warning"]


def test_the_mark_survives_resolving_the_embedder_again(
    tmp_path, healthy_openai, broken_lancedb, monkeypatch
):
    """The workspace registry resolves the embedder on every repo (re)load,
    which rewrites ``_embedder_status``. The store failure must outlive that."""
    (tmp_path / ".repowise" / "lancedb").mkdir(parents=True)
    asyncio.run(_server._load_vector_stores(str(tmp_path)))

    monkeypatch.setattr(_server, "_configured_embedder_name", lambda: "mock")
    _server._resolve_embedder()

    meta = build_meta(timing_ms=1.0)
    assert meta["embedder_degraded"] is True
    assert "connect_async" in meta["embedder_warning"]


def test_a_locked_table_is_not_blamed_on_the_install(tmp_path, healthy_openai, monkeypatch):
    async def _locked(self):
        raise OSError("database is locked")

    monkeypatch.setattr(LanceDBVectorStore, "_ensure_connected", _locked)
    (tmp_path / ".repowise" / "lancedb").mkdir(parents=True)

    asyncio.run(_server._load_vector_stores(str(tmp_path)))

    warning = build_meta(timing_ms=1.0)["embedder_warning"]
    assert "database is locked" in warning
    assert "reinstall" not in warning
    assert "retry" in warning


def test_no_index_on_disk_is_not_a_failure(tmp_path, healthy_openai, broken_lancedb):
    asyncio.run(_server._load_vector_stores(str(tmp_path)))

    assert build_meta(timing_ms=1.0)["embedder_degraded"] is False


def test_the_workspace_registry_reports_an_unopenable_index(tmp_path, broken_lancedb):
    from repowise.core.workspace.registry import RepoRegistry

    (tmp_path / ".repowise" / "lancedb").mkdir(parents=True)
    reported: list[tuple[str, BaseException]] = []

    async def _go():
        ctx = SimpleNamespace(alias="api", vector_store_ready=asyncio.Event())
        registry = SimpleNamespace(
            _on_vector_store_error=lambda alias, exc: reported.append((alias, exc)),
            _contexts={"api": ctx},
            _vs_tasks={},
        )
        await RepoRegistry._load_vector_stores(registry, ctx, tmp_path, MockEmbedder())
        return ctx

    ctx = asyncio.run(_go())

    assert [alias for alias, _ in reported] == ["api"]
    assert "connect_async" in str(reported[0][1])
    assert ctx.vector_store_ready.is_set()


@pytest.fixture
def bridge_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(_state, "_embedder_status", None, raising=False)
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.resolve_embedder_for_repo", lambda p: "openai"
    )
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.build_embedder", lambda name, _p=None: MockEmbedder()
    )
    (tmp_path / ".repowise" / "lancedb").mkdir(parents=True)
    return tmp_path


def test_the_cli_bridge_reports_an_unopenable_index(bridge_repo, broken_lancedb):
    from repowise.cli import tool_bridge

    async def _go():
        return await tool_bridge._connect_or_degrade(
            await tool_bridge._open_vector_store(bridge_repo)
        )

    store = asyncio.run(_go())

    assert isinstance(store, InMemoryVectorStore)
    assert build_meta(timing_ms=1.0)["embedder_degraded"] is True
    assert "connect_async" in build_meta(timing_ms=1.0)["embedder_warning"]


def test_the_cli_bridge_does_not_open_the_store_for_other_tools(bridge_repo, monkeypatch):
    """Importing lancedb costs about a second; tools that never read vectors
    must not pay it."""
    from repowise.cli import tool_bridge

    connected: list[bool] = []

    async def _spy(self):
        connected.append(True)

    monkeypatch.setattr(LanceDBVectorStore, "_ensure_connected", _spy)

    assert "get_context" not in tool_bridge._VECTOR_TOOLS
    store = asyncio.run(tool_bridge._open_vector_store(bridge_repo))

    assert isinstance(store, LanceDBVectorStore)
    assert connected == []
    assert {"search_codebase", "get_answer"} <= tool_bridge._VECTOR_TOOLS
