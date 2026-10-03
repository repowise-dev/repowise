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
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.server.mcp_server import _server, _state
from repowise.server.mcp_server._meta import build_meta


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
        raise RuntimeError("LanceDB is missing or broken (AttributeError: connect_async)")

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


def test_the_cli_bridge_reports_an_unopenable_index(tmp_path, monkeypatch, broken_lancedb):
    from repowise.cli import tool_bridge

    monkeypatch.setattr(_state, "_embedder_status", None, raising=False)
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.resolve_embedder_for_repo", lambda p: "openai"
    )
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.build_embedder", lambda name, _p=None: MockEmbedder()
    )
    (tmp_path / ".repowise" / "lancedb").mkdir(parents=True)

    store = asyncio.run(tool_bridge._open_vector_store(tmp_path))

    assert isinstance(store, InMemoryVectorStore)
    assert _state._embedder_status["degraded"] is True
    assert "connect_async" in _state._embedder_status["reason"]
