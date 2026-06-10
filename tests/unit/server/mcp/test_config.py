"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import pytest


def test_generate_mcp_config():
    from pathlib import Path

    from repowise.cli.mcp_config import generate_mcp_config

    config = generate_mcp_config(Path("/tmp/test-repo"))
    assert "mcpServers" in config
    assert "repowise" in config["mcpServers"]
    server = config["mcpServers"]["repowise"]
    assert server["command"] == "repowise"
    assert "mcp" in server["args"]
    assert "stdio" in server["args"]


def test_format_setup_instructions():
    from pathlib import Path

    from repowise.cli.mcp_config import format_setup_instructions

    instructions = format_setup_instructions(Path("/tmp/test-repo"))
    assert "Project .mcp.json" in instructions
    assert "Claude Code" not in instructions
    assert "Cursor" in instructions
    assert "Cline" in instructions
    assert "repowise" in instructions


@pytest.mark.asyncio
async def test_mcp_lifespan_uses_cli_database_env_var(monkeypatch):
    """REPOWISE_DB_URL should be respected by MCP lifespan via resolve_db_url."""
    import repowise.server.mcp_server._server as mcp_server
    from repowise.server.mcp_server import _state

    captured: dict[str, str] = {}

    class DummyEngine:
        @property
        def dialect(self):
            class _D:
                name = "sqlite"

            return _D()

        async def dispose(self) -> None:
            return None

    class DummyFts:
        def __init__(self, engine) -> None:
            self.engine = engine

        async def ensure_index(self) -> None:
            return None

    class DummyVectorStore:
        def __init__(self, *, embedder) -> None:
            self.embedder = embedder

        async def close(self) -> None:
            return None

    async def fake_init_db(engine) -> None:
        return None

    async def fake_load_vector_stores(repo_path: str | None) -> None:
        return None

    def fake_create_engine(url: str) -> DummyEngine:
        captured["url"] = url
        return DummyEngine()

    monkeypatch.setenv("REPOWISE_DB_URL", "sqlite+aiosqlite:///tmp/from-cli.db")
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    monkeypatch.setattr(mcp_server, "create_engine", fake_create_engine)
    monkeypatch.setattr(mcp_server, "init_db", fake_init_db)
    monkeypatch.setattr(mcp_server, "FullTextSearch", DummyFts)
    monkeypatch.setattr(mcp_server, "InMemoryVectorStore", DummyVectorStore)
    monkeypatch.setattr(mcp_server, "async_sessionmaker", lambda *args, **kwargs: object())
    monkeypatch.setattr(mcp_server, "_load_vector_stores", fake_load_vector_stores)

    original_repo_path = _state._repo_path
    original_vector_store = _state._vector_store
    original_decision_store = _state._decision_store
    original_ready = _state._vector_store_ready

    _state._repo_path = None
    _state._vector_store = None
    _state._decision_store = None
    _state._vector_store_ready = None

    try:
        async with mcp_server._lifespan(mcp_server.mcp):
            assert captured["url"] == "sqlite+aiosqlite:///tmp/from-cli.db"
    finally:
        _state._repo_path = original_repo_path
        _state._vector_store = original_vector_store
        _state._decision_store = original_decision_store
        _state._vector_store_ready = original_ready
