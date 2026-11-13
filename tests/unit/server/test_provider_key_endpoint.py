"""Provider key endpoints wire ``repo_id`` through to the repo's .env (D6).

Adding a key in the web UI must be visible to a later CLI run in that repo, and
``GET /api/providers`` must never hand back key material.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from repowise.core.repo_config import load_repo_env
from tests.unit.server.conftest import create_test_repo


@pytest.fixture(autouse=True)
def isolate_server_store(monkeypatch, tmp_path):
    """Keep set_api_key's server-global JSON out of the real ~/.repowise."""
    from repowise.server import provider_config as pc

    monkeypatch.setenv("REPOWISE_CONFIG_DIR", str(tmp_path / "server-store"))
    # Don't let a real ANTHROPIC_API_KEY on the dev box mask the stored one,
    # nor an OPENAI_BASE_URL another test exported stand in for a cleared one.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
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


class _FakeProvider:
    provider_name = "anthropic"
    model_name = "claude-x"

    def __init__(self, raises: bool = False):
        self._raises = raises

    async def generate(self, *_a, **_k):
        if self._raises:
            raise RuntimeError("401 Unauthorized")
        return "OK"


@pytest.mark.asyncio
async def test_validate_ok_when_probe_succeeds(client: AsyncClient, monkeypatch) -> None:
    repo = await create_test_repo(client)
    from repowise.server import provider_config as pc

    monkeypatch.setattr(pc, "get_chat_provider_instance", lambda **_k: _FakeProvider())

    resp = await client.post(f"/api/providers/anthropic/validate?repo_id={repo['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "provider": "anthropic", "model": "claude-x", "error": None}


@pytest.mark.asyncio
async def test_validate_reports_probe_failure(client: AsyncClient, monkeypatch) -> None:
    repo = await create_test_repo(client)
    from repowise.server import provider_config as pc

    monkeypatch.setattr(
        pc, "get_chat_provider_instance", lambda **_k: _FakeProvider(raises=True)
    )

    resp = await client.post(f"/api/providers/anthropic/validate?repo_id={repo['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "401" in body["error"]


@pytest.mark.asyncio
async def test_validate_reports_missing_key(client: AsyncClient, monkeypatch) -> None:
    repo = await create_test_repo(client)
    from repowise.server import provider_config as pc

    def _boom(**_k):
        raise ValueError("No active provider configured. Set an API key first.")

    monkeypatch.setattr(pc, "get_chat_provider_instance", _boom)

    resp = await client.post(f"/api/providers/anthropic/validate?repo_id={repo['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "API key" in body["error"]


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


@pytest.mark.asyncio
async def test_patch_active_with_repo_id_writes_config_yaml(client: AsyncClient) -> None:
    from pathlib import Path

    from repowise.core.repo_config import load_repo_config

    repo = await create_test_repo(client)

    resp = await client.patch(
        "/api/providers/active",
        json={
            "provider": "openai",
            "model": "qwen/qwen3-coder-30b",
            "base_url": "http://localhost:1234/v1",
            "repo_id": repo["id"],
        },
    )
    assert resp.status_code == 200
    active = resp.json()["active"]
    assert (active["provider"], active["model"]) == ("openai", "qwen/qwen3-coder-30b")
    assert active["base_url"] == "http://localhost:1234/v1"
    assert active["provenance"]["base_url"] == "config.yaml"
    cfg = load_repo_config(Path(repo["local_path"]))
    assert cfg["openai"] == {"base_url": "http://localhost:1234/v1"}

    # Leaving base_url out keeps it; the existing request shape still works.
    resp = await client.patch(
        "/api/providers/active",
        json={"provider": "openai", "model": "other", "repo_id": repo["id"]},
    )
    assert resp.json()["active"]["base_url"] == "http://localhost:1234/v1"

    resp = await client.patch(
        "/api/providers/active",
        json={"provider": "openai", "base_url": "", "repo_id": repo["id"]},
    )
    assert resp.json()["active"]["base_url"] is None


@pytest.mark.asyncio
async def test_patch_active_rejects_a_non_http_base_url(client: AsyncClient) -> None:
    resp = await client.patch(
        "/api/providers/active", json={"provider": "openai", "base_url": "localhost:1234"}
    )
    assert resp.status_code == 400
    assert "http" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_detect_query_reports_signed_in_agent_clis(client: AsyncClient, monkeypatch) -> None:
    from repowise.core.agents.identity import AgentIdentity

    monkeypatch.setattr(AgentIdentity, "is_logged_in", lambda self: self.slug == "codex")

    plain = (await client.get("/api/providers")).json()
    detected = (await client.get("/api/providers?detect=true")).json()

    def by_id(body):
        return {p["id"]: p["detected"] for p in body["providers"]}

    assert by_id(plain)["codex_cli"] is None
    assert by_id(detected)["codex_cli"] == "signed_in"
    assert by_id(detected)["claude_cli"] is None


@pytest.mark.asyncio
async def test_detect_names_the_plan_route_timed_by_generate(client: AsyncClient, monkeypatch) -> None:
    from repowise.core.generation import scope
    from repowise.core.index_scope import FreeRoute
    from repowise.core.providers.llm import subscription
    from repowise.server.routers import providers

    monkeypatch.setattr(providers, "_generate_calls", {})
    repo = await create_test_repo(client)
    seen: list[str] = []
    counted: list[str] = []

    def route(path):
        seen.append(str(path))
        return FreeRoute(provider="claude_cli", cli_name="Claude Code")

    async def calls(session, repo_id, repo_path):
        counted.append(repo_id)
        return 300

    monkeypatch.setattr(subscription, "detect_free_route", route)
    monkeypatch.setattr(subscription, "process_limit", lambda name: 5)
    monkeypatch.setattr(scope, "generate_model_calls", calls)

    body = (await client.get(f"/api/providers?detect=true&repo_id={repo['id']}")).json()
    assert body["plan_route"] == {"provider": "claude_cli", "cli_name": "Claude Code", "minutes": 60}
    assert seen == [repo["local_path"]]

    # Only the detect call probes, and only for a repo.
    assert (await client.get(f"/api/providers?repo_id={repo['id']}")).json()["plan_route"] is None
    assert (await client.get("/api/providers?detect=true")).json()["plan_route"] is None
    assert len(seen) == 1

    # A second open probes the sign-in again but reuses the count.
    again = (await client.get(f"/api/providers?detect=true&repo_id={repo['id']}")).json()
    assert again["plan_route"]["minutes"] == 60
    assert len(seen) == 2 and counted == [repo["id"]]


@pytest.mark.asyncio
async def test_detect_names_no_plan_route_without_a_paid_plan(client: AsyncClient, monkeypatch) -> None:
    from repowise.core.providers.llm import subscription

    repo = await create_test_repo(client)
    monkeypatch.setattr(subscription, "detect_free_route", lambda path: None)

    body = (await client.get(f"/api/providers?detect=true&repo_id={repo['id']}")).json()
    assert body["plan_route"] is None


@pytest.fixture
async def plan_route_env(monkeypatch):
    from repowise.core.index_scope import FreeRoute
    from repowise.core.providers.llm import subscription
    from repowise.server.routers import providers

    closed = []

    @asynccontextmanager
    async def session(_factory):
        try:
            yield object()
        finally:
            closed.append(True)

    monkeypatch.setattr(providers, "_generate_calls", {})
    monkeypatch.setattr(providers, "_generate_counts", {}, raising=False)
    monkeypatch.setattr(providers, "_CALLS_WAIT_S", 0.01, raising=False)
    monkeypatch.setattr(providers, "_COUNT_CEILING_S", 5, raising=False)
    monkeypatch.setattr(providers, "resolve_session_factory", lambda *_: object())
    monkeypatch.setattr(providers, "get_session", session)
    monkeypatch.setattr(
        subscription, "detect_free_route", lambda _: FreeRoute("claude_cli", "Claude Code")
    )
    monkeypatch.setattr(subscription, "process_limit", lambda _: 5)
    request = SimpleNamespace(app=SimpleNamespace(state=object()))
    try:
        yield providers, request, closed
    finally:
        tasks = list(providers._generate_counts.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_slow_plan_count_returns_untimed_route_then_reuses_minutes(
    plan_route_env, monkeypatch
) -> None:
    from repowise.core.generation import scope

    providers, request, closed = plan_route_env
    release = asyncio.Event()
    started = []

    async def count(_session, repo_id, _path):
        started.append(repo_id)
        await release.wait()
        return 300

    monkeypatch.setattr(scope, "generate_model_calls", count)
    first, second = await asyncio.wait_for(
        asyncio.gather(
            providers._plan_route(request, "r1", "repo"),
            providers._plan_route(request, "r1", "repo"),
        ),
        1,
    )
    assert first == second == {"provider": "claude_cli", "cli_name": "Claude Code", "minutes": None}
    assert started == ["r1"] and closed == []
    release.set()
    await asyncio.wait_for(providers._generate_counts["r1"], 1)
    assert (await providers._plan_route(request, "r1", "repo"))["minutes"] == 60
    assert started == ["r1"] and closed == [True]


async def test_stuck_plan_count_releases_session_and_is_not_repeated(
    plan_route_env, monkeypatch
) -> None:
    from repowise.core.generation import scope

    providers, request, closed = plan_route_env
    started = []

    async def count(_session, repo_id, _path):
        started.append(repo_id)
        await asyncio.Event().wait()

    monkeypatch.setattr(scope, "generate_model_calls", count)
    monkeypatch.setattr(providers, "_COUNT_CEILING_S", 0.01)
    result = await asyncio.wait_for(providers._plan_route(request, "r1", "repo"), 1)
    assert result["minutes"] is None
    await asyncio.wait_for(providers._generate_counts["r1"], 1)
    assert closed == [True]
    assert await providers._plan_route(request, "r1", "repo") == result
    assert started == ["r1"]


async def test_failed_plan_count_retries_and_caches_zero(plan_route_env, monkeypatch) -> None:
    from repowise.core.generation import scope

    providers, request, _closed = plan_route_env
    started = []

    async def count(_session, repo_id, _path):
        started.append(repo_id)
        if len(started) == 1:
            raise ValueError("synthetic count failure")
        return 0

    monkeypatch.setattr(scope, "generate_model_calls", count)
    assert (await providers._plan_route(request, "r1", "repo"))["minutes"] is None
    assert (await providers._plan_route(request, "r1", "repo"))["minutes"] is None
    assert providers._generate_calls["r1"] == 0
    assert (await providers._plan_route(request, "r1", "repo"))["minutes"] is None
    assert started == ["r1", "r1"]


async def test_cancelled_plan_request_leaves_shared_count_running(
    plan_route_env, monkeypatch
) -> None:
    from repowise.core.generation import scope

    providers, request, closed = plan_route_env
    started = asyncio.Event()
    release = asyncio.Event()

    async def count(_session, _repo_id, _path):
        started.set()
        await release.wait()
        return 300

    monkeypatch.setattr(scope, "generate_model_calls", count)
    monkeypatch.setattr(providers, "_CALLS_WAIT_S", 5)
    caller = asyncio.create_task(providers._plan_route(request, "r1", "repo"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert closed == []
        assert (await asyncio.wait_for(providers._plan_route(request, "r1", "repo"), 1))[
            "minutes"
        ] is None
        release.set()
        await asyncio.wait_for(providers._generate_counts["r1"], 1)
        assert (await providers._plan_route(request, "r1", "repo"))["minutes"] == 60
        assert closed == [True]
    finally:
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
