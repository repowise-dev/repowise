"""Provider key endpoints wire ``repo_id`` through to the repo's .env (D6).

Adding a key in the web UI must be visible to a later CLI run in that repo, and
``GET /api/providers`` must never hand back key material.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.repo_config import load_repo_env
from tests.unit.server.conftest import create_test_repo


@pytest.fixture(autouse=True)
def isolate_server_store(monkeypatch, tmp_path):
    """Keep set_api_key's server-global JSON out of the real ~/.repowise."""
    from repowise.server import provider_config as pc

    monkeypatch.setenv("REPOWISE_CONFIG_DIR", str(tmp_path / "server-store"))
    # Don't let a real ANTHROPIC_API_KEY on the dev box mask the stored one.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return pc


@pytest.mark.asyncio
async def test_add_key_with_repo_id_writes_repo_env(client: AsyncClient) -> None:
    repo = await create_test_repo(client)

    resp = await client.post(
        "/api/providers/anthropic/key",
        json={"api_key": "sk-ant-from-ui", "repo_id": repo["id"]},
    )
    assert resp.status_code == 204

    from pathlib import Path

    env = load_repo_env(Path(repo["local_path"]))
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-from-ui"


@pytest.mark.asyncio
async def test_add_key_without_repo_id_does_not_touch_disk(client: AsyncClient) -> None:
    repo = await create_test_repo(client)

    resp = await client.post(
        "/api/providers/anthropic/key",
        json={"api_key": "sk-ant-global"},
    )
    assert resp.status_code == 204

    from pathlib import Path

    assert not (Path(repo["local_path"]) / ".repowise" / ".env").exists()


@pytest.mark.asyncio
async def test_remove_key_with_repo_id_clears_repo_env(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    await client.post(
        "/api/providers/anthropic/key",
        json={"api_key": "sk-ant-from-ui", "repo_id": repo["id"]},
    )

    resp = await client.delete(f"/api/providers/anthropic/key?repo_id={repo['id']}")
    assert resp.status_code == 204

    from pathlib import Path

    assert "ANTHROPIC_API_KEY" not in load_repo_env(Path(repo["local_path"]))


@pytest.mark.asyncio
async def test_add_key_resolves_non_primary_workspace_repo(client: AsyncClient, app, tmp_path) -> None:
    # Workspace mode: a non-primary repo's row lives only in its own wiki.db, not
    # the primary DB. The key endpoint gets repo_id in the body, so resolution
    # must route by repo_id (not the request path/query) to reach that DB.
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from repowise.core.persistence import crud
    from repowise.core.persistence.database import init_db
    from repowise.core.repo_config import load_repo_env

    ws_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(ws_engine)
    ws_factory = async_sessionmaker(ws_engine, expire_on_commit=False, class_=AsyncSession)

    repo_dir = tmp_path / "frontend"
    (repo_dir / ".repowise").mkdir(parents=True)
    async with ws_factory() as s:
        repo = await crud.upsert_repository(s, name="frontend", local_path=str(repo_dir))
        await s.commit()
        repo_id = repo.id

    # Register the per-repo DB; its row is deliberately absent from the primary DB.
    app.state.workspace_sessions = {repo_id: ws_factory}
    try:
        resp = await client.post(
            "/api/providers/anthropic/key",
            json={"api_key": "sk-ws-key", "repo_id": repo_id},
        )
        assert resp.status_code == 204
        assert load_repo_env(repo_dir)["ANTHROPIC_API_KEY"] == "sk-ws-key"
    finally:
        app.state.workspace_sessions = {}
        await ws_engine.dispose()


@pytest.mark.asyncio
async def test_get_providers_returns_no_key_material(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    await client.post(
        "/api/providers/anthropic/key",
        json={"api_key": "sk-ant-super-secret", "repo_id": repo["id"]},
    )

    resp = await client.get(f"/api/providers?repo_id={repo['id']}")
    assert resp.status_code == 200
    assert "sk-ant-super-secret" not in resp.text
    for provider in resp.json()["providers"]:
        assert "key" not in provider
        assert "api_key" not in provider
