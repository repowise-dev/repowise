"""Regression tests for persisted REST semantic-search indexes."""

from __future__ import annotations

from types import SimpleNamespace

from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.vector_store import LanceDBVectorStore
from repowise.core.providers.embedding.base import MockEmbedder
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
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


async def test_build_primary_vector_store_postgres_multiple_repos_workspace_config(
    session_factory,
    tmp_path,
    monkeypatch,
) -> None:
    """PostgreSQL URL with multiple repos should resolve primary from workspace config."""
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    repo_a = ws_root / "services" / "primary-svc"
    repo_b = ws_root / "services" / "secondary-svc"
    (repo_a / ".repowise" / "lancedb").mkdir(parents=True)
    (repo_b / ".repowise" / "lancedb").mkdir(parents=True)

    ws_config = WorkspaceConfig(
        default_repo="primary",
        repos=[
            RepoEntry(path="services/primary-svc", alias="primary", is_primary=True),
            RepoEntry(path="services/secondary-svc", alias="secondary"),
        ],
    )
    ws_config.save(ws_root)

    async with get_session(session_factory) as session:
        await crud.upsert_repository(
            session,
            name="secondary-svc",
            local_path=str(repo_b),
        )
        r_a = await crud.upsert_repository(
            session,
            name="primary-svc",
            local_path=str(repo_a),
        )

    embedder = MockEmbedder()
    lance_store = LanceDBVectorStore(str(repo_a / ".repowise" / "lancedb"), embedder=embedder)
    await lance_store.embed_and_upsert("p1", "content", {"title": "P1"})
    await lance_store.close()

    monkeypatch.chdir(ws_root)
    postgres_url = "postgresql+asyncpg://postgres:secret@localhost:5432/repowise"

    loaded_store, loaded_repo_id = await build_primary_vector_store(
        session_factory,
        postgres_url,
        embedder,
    )
    try:
        assert loaded_repo_id == r_a.id
        assert isinstance(loaded_store, LanceDBVectorStore)
        assert await loaded_store.list_page_ids() == {"p1"}
    finally:
        await loaded_store.close()


async def test_build_primary_vector_store_postgres_multiple_repos_cwd_match(
    session_factory,
    tmp_path,
    monkeypatch,
) -> None:
    """PostgreSQL URL with multiple repos should resolve primary matching process CWD."""
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    repo_a = repos_dir / "service-a"
    repo_b = repos_dir / "service-b"
    (repo_a / ".repowise" / "lancedb").mkdir(parents=True)
    (repo_b / ".repowise" / "lancedb").mkdir(parents=True)

    async with get_session(session_factory) as session:
        await crud.upsert_repository(
            session,
            name="service-a",
            local_path=str(repo_a),
        )
        r_b = await crud.upsert_repository(
            session,
            name="service-b",
            local_path=str(repo_b),
        )

    embedder = MockEmbedder()
    lance_store = LanceDBVectorStore(str(repo_b / ".repowise" / "lancedb"), embedder=embedder)
    await lance_store.embed_and_upsert("p2", "content-b", {"title": "P2"})
    await lance_store.close()

    monkeypatch.chdir(repo_b)
    postgres_url = "postgresql+asyncpg://postgres:secret@localhost:5432/repowise"

    loaded_store, loaded_repo_id = await build_primary_vector_store(
        session_factory,
        postgres_url,
        embedder,
    )
    try:
        assert loaded_repo_id == r_b.id
        assert isinstance(loaded_store, LanceDBVectorStore)
        assert await loaded_store.list_page_ids() == {"p2"}
    finally:
        await loaded_store.close()


async def test_build_primary_vector_store_postgres_single_repo_fallback(
    session_factory,
    tmp_path,
    monkeypatch,
) -> None:
    """PostgreSQL URL with single repo falls back to rows[0]."""
    standalone_dir = tmp_path / "standalone"
    standalone_dir.mkdir()
    repo_path = tmp_path / "single-repo"
    (repo_path / ".repowise" / "lancedb").mkdir(parents=True)

    async with get_session(session_factory) as session:
        repo = await crud.upsert_repository(
            session,
            name="single-repo",
            local_path=str(repo_path),
        )

    embedder = MockEmbedder()
    lance_store = LanceDBVectorStore(str(repo_path / ".repowise" / "lancedb"), embedder=embedder)
    await lance_store.embed_and_upsert("p-single", "single-content", {"title": "Single"})
    await lance_store.close()

    monkeypatch.chdir(standalone_dir)
    postgres_url = "postgresql+asyncpg://postgres:secret@localhost:5432/repowise"

    loaded_store, loaded_repo_id = await build_primary_vector_store(
        session_factory,
        postgres_url,
        embedder,
    )
    try:
        assert loaded_repo_id == repo.id
        assert isinstance(loaded_store, LanceDBVectorStore)
        assert await loaded_store.list_page_ids() == {"p-single"}
    finally:
        await loaded_store.close()


async def test_build_primary_vector_store_create_false_does_not_create_empty_dir(
    session_factory,
    tmp_path,
    monkeypatch,
) -> None:
    """create=False should return InMemoryVectorStore and avoid creating empty LanceDB dir."""
    repo_path = tmp_path / "unindexed-repo"
    repo_path.mkdir(parents=True)

    async with get_session(session_factory) as session:
        await crud.upsert_repository(
            session,
            name="unindexed-repo",
            local_path=str(repo_path),
        )

    embedder = MockEmbedder()
    monkeypatch.chdir(repo_path)
    postgres_url = "postgresql+asyncpg://postgres:secret@localhost:5432/repowise"

    loaded_store, loaded_repo_id = await build_primary_vector_store(
        session_factory,
        postgres_url,
        embedder,
        create=False,
    )
    try:
        from repowise.core.persistence.vector_store import InMemoryVectorStore

        assert loaded_repo_id is None
        assert isinstance(loaded_store, InMemoryVectorStore)
        assert not (repo_path / ".repowise" / "lancedb").exists()
    finally:
        await loaded_store.close()
