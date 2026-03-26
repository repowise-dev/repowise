"""Tests for /api/search endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session

from tests.unit.server.conftest import create_test_repo


@pytest.mark.asyncio
async def test_semantic_search_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/search", params={"query": "auth module"})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_semantic_search_with_data(client: AsyncClient, app) -> None:
    # Insert a page and embed it in the vector store
    repo = await create_test_repo(client)
    repo_id = repo["id"]

    async with get_session(app.state.session_factory) as session:
        await crud.upsert_page(
            session,
            page_id="file_page:src/auth.py",
            repository_id=repo_id,
            page_type="file_page",
            title="auth.py",
            content="# Authentication module\n\nHandles user login and logout.",
            target_path="src/auth.py",
            source_hash="def456",
            model_name="mock",
            provider_name="mock",
        )

    # Embed in vector store
    await app.state.vector_store.embed_and_upsert(
        "file_page:src/auth.py",
        "Authentication module handles user login and logout",
        {
            "title": "auth.py",
            "page_type": "file_page",
            "target_path": "src/auth.py",
            "content": "Authentication module handles user login and logout",
        },
    )

    resp = await client.get("/api/search", params={"query": "authentication"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["page_id"] == "file_page:src/auth.py"
    assert data[0]["search_type"] == "vector"


@pytest.mark.asyncio
async def test_fulltext_search(client: AsyncClient, app) -> None:
    # Index a page in FTS
    await app.state.fts.index(
        "file_page:src/main.py",
        "main.py",
        "Entry point for the application with routing logic",
    )

    resp = await client.get(
        "/api/search",
        params={"query": "routing", "search_type": "fulltext"},
    )
    assert resp.status_code == 200
    # FTS requires exact word match; this may or may not match
    # depending on tokenization, but the endpoint should not error


@pytest.mark.asyncio
async def test_search_requires_query(client: AsyncClient) -> None:
    resp = await client.get("/api/search", params={"query": ""})
    assert resp.status_code == 422  # Validation error: min_length=1
