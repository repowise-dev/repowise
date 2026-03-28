"""Tests for /api/symbols endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import WikiSymbol, _new_uuid
from tests.unit.server.conftest import create_test_repo


async def _insert_symbol(session_factory, repo_id: str, **overrides) -> str:
    """Insert a test symbol and return its DB id."""
    defaults = {
        "id": _new_uuid(),
        "repository_id": repo_id,
        "file_path": "src/main.py",
        "symbol_id": "src/main.py::main",
        "name": "main",
        "qualified_name": "src.main.main",
        "kind": "function",
        "signature": "def main() -> None",
        "start_line": 1,
        "end_line": 10,
        "visibility": "public",
        "language": "python",
    }
    defaults.update(overrides)
    sym_id = defaults["id"]

    async with get_session(session_factory) as session:
        session.add(WikiSymbol(**defaults))

    return sym_id


@pytest.mark.asyncio
async def test_search_symbols_empty(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get("/api/symbols", params={"repo_id": repo["id"]})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_search_symbols_by_name(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_symbol(app.state.session_factory, repo["id"])

    resp = await client.get("/api/symbols", params={"repo_id": repo["id"], "q": "main"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "main"
    assert data[0]["kind"] == "function"


@pytest.mark.asyncio
async def test_search_symbols_by_kind(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_symbol(
        app.state.session_factory,
        repo["id"],
        name="MyClass",
        symbol_id="src/main.py::MyClass",
        kind="class",
    )

    resp = await client.get("/api/symbols", params={"repo_id": repo["id"], "kind": "class"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["kind"] == "class"


@pytest.mark.asyncio
async def test_lookup_by_name_exact(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_symbol(app.state.session_factory, repo["id"])

    resp = await client.get("/api/symbols/by-name/main", params={"repo_id": repo["id"]})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "main"


@pytest.mark.asyncio
async def test_lookup_by_name_fuzzy(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_symbol(
        app.state.session_factory,
        repo["id"],
        name="authenticate_user",
        symbol_id="src/auth.py::authenticate_user",
    )

    resp = await client.get("/api/symbols/by-name/auth", params={"repo_id": repo["id"]})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_get_symbol_by_db_id(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    sym_id = await _insert_symbol(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/symbols/{sym_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "main"


@pytest.mark.asyncio
async def test_get_symbol_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/symbols/nonexistent")
    assert resp.status_code == 404
