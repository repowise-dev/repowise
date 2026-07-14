"""Regression tests for persisted REST semantic-search indexes."""

from __future__ import annotations

from types import SimpleNamespace

from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.vector_store import LanceDBVectorStore
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.server.search_helpers import (
    build_primary_vector_store,
    resolve_repo_vector_store,
)


async def test_rest_search_reopens_cli_lancedb_index(
    client: AsyncClient,
    app,
    session_factory,
    tmp_path,
) -> None:
    """REST search should see vectors persisted before server startup."""
    repo_path = tmp_path / "repo"
    lance_path = repo_path / ".repowise" / "lancedb"
    lance_path.mkdir(parents=True)

    async with get_session(session_factory) as session:
        repo = await crud.upsert_repository(
            session,
            name="test-repo",
            local_path=str(repo_path),
        )
        repo_id = repo.id

    embedder = MockEmbedder()
    cli_store = LanceDBVectorStore(str(lance_path), embedder=embedder)
    await cli_store.embed_and_upsert(
        "file_page:src/auth.py",
        "Authentication module handles user login and logout",
        {
            "title": "auth.py",
            "page_type": "file_page",
            "target_path": "src/auth.py",
            "content": "Authentication module handles user login and logout",
        },
    )
    await cli_store.close()

    loaded_store, loaded_repo_id = await build_primary_vector_store(
        session_factory,
        "sqlite+aiosqlite:///:memory:",
        embedder,
    )
    original_store = app.state.vector_store
    original_cache = getattr(app.state, "workspace_vector_stores", None)
    app.state.vector_store = loaded_store
    app.state.workspace_vector_stores = {repo_id: loaded_store}
    try:
        response = await client.get(
            "/api/search",
            params={"query": "authentication", "search_type": "semantic"},
        )
    finally:
        await loaded_store.close()
        app.state.vector_store = original_store
        app.state.workspace_vector_stores = original_cache

    assert loaded_repo_id == repo_id
    assert response.status_code == 200
    assert response.json()[0]["page_id"] == "file_page:src/auth.py"


async def test_repo_vector_stores_are_isolated(tmp_path) -> None:
    """Workspace jobs should persist vectors in their own repo directories."""
    embedder = MockEmbedder()
    state = SimpleNamespace(
        vector_store=SimpleNamespace(_embedder=embedder),
        workspace_vector_stores={},
    )
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"

    store_a = await resolve_repo_vector_store(
        state,
        "repo-a",
        repo_path=repo_a,
        create=True,
    )
    store_b = await resolve_repo_vector_store(
        state,
        "repo-b",
        repo_path=repo_b,
        create=True,
    )

    try:
        await store_a.embed_and_upsert("page-a", "alpha", {"title": "A"})
        await store_b.embed_and_upsert("page-b", "beta", {"title": "B"})

        assert await store_a.list_page_ids() == {"page-a"}
        assert await store_b.list_page_ids() == {"page-b"}
        assert (repo_a / ".repowise" / "lancedb").is_dir()
        assert (repo_b / ".repowise" / "lancedb").is_dir()
    finally:
        await store_a.close()
        await store_b.close()
