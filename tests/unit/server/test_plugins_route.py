"""/api/repos/{id}/plugins: the plugins listing and "Sync now"."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from repowise.server import auth
from repowise.server.auth import Action, Principal
from repowise.server.routers import plugins
from repowise.server.services import setup_cli
from tests.unit.server.conftest import create_test_repo

SECRET = "tok-panel-sentinel-91c4"

#: ``repowise plugins --format json`` as the CLI prints it.
_LISTING = {
    "api_version": 1,
    "plugins": [
        {
            "name": "pgstat",
            "source": "entry point",
            "target": "repowise_pgstat:register",
            "status": "loaded",
            "error": None,
            "seams": [
                "evidence-source:pgstat",
                "setting:plugins.pgstat.db",
                "setting:REPOWISE_PLUGIN_PGSTAT_TOKEN",
            ],
            "storage": {"applied": [], "pending": []},
            "version": "0.2.0",
            "configured": False,
            "missing": [
                {
                    "key": "REPOWISE_PLUGIN_PGSTAT_TOKEN",
                    "fix": "export REPOWISE_PLUGIN_PGSTAT_TOKEN=<value>",
                }
            ],
            "settings": [
                {"key": "plugins.pgstat.db", "env": "REPOWISE_PLUGIN_PGSTAT_DB",
                 "secret": False, "set": True},
                {"key": "REPOWISE_PLUGIN_PGSTAT_TOKEN", "env": "REPOWISE_PLUGIN_PGSTAT_TOKEN",
                 "secret": True, "set": False},
            ],
            "next_command": "export REPOWISE_PLUGIN_PGSTAT_TOKEN=<value>",
        }
    ],
    "sources": [
        {
            "source": "pgstat",
            "seam": "evidence-source",
            "plugin": "pgstat",
            "configured": False,
            "message": "needs REPOWISE_PLUGIN_PGSTAT_TOKEN",
            "next_command": "export REPOWISE_PLUGIN_PGSTAT_TOKEN=<value>",
            "last_sync": {
                "at": "2026-11-28T10:00:00+00:00",
                "state": "stored",
                "stored_at": "2026-11-28T10:00:00+00:00",
                "batches": 2,
                "message": None,
            },
        }
    ],
    "seams": {"evidence-source": {"default": None, "implementations": ["pgstat"]}},
    "document_extractors": {},
}


class FakeCli:
    """Stands in for ``run_cli``: records argv; a sync waits on ``release``."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.release = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.sync_output = json.dumps({"repo": "r", "sources": []})

    def __call__(self, args: list[str], *, cwd: Path, timeout: float) -> setup_cli.CliRun:
        self.calls.append(args)
        if args[:1] == ["plugins"]:
            return setup_cli.CliRun(0, json.dumps(_LISTING), "")
        # Runs in a worker thread: block until the test lets the sync finish.
        asyncio.run_coroutine_threadsafe(self.release.wait(), self.loop).result(timeout=10)
        return setup_cli.CliRun(0, self.sync_output, "")


@pytest.fixture
async def client(app) -> AsyncClient:
    app.include_router(plugins.router)
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://localhost:7337") as c:
        yield c


@pytest.fixture
async def repo(client: AsyncClient, tmp_path: Path) -> dict:
    return await create_test_repo(client, tmp_path)


@pytest.fixture
async def cli(monkeypatch: pytest.MonkeyPatch) -> FakeCli:
    fake = FakeCli()
    monkeypatch.setattr(setup_cli, "run_cli", fake)
    return fake


async def _drain(app) -> None:
    await asyncio.gather(*app.state.background_tasks, return_exceptions=True)


@pytest.mark.anyio
async def test_listing_is_the_cli_json_with_no_secret_value(
    client: AsyncClient, repo: dict, cli: FakeCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPOWISE_PLUGIN_PGSTAT_TOKEN", SECRET)
    resp = await client.get(f"/api/repos/{repo['id']}/plugins")
    assert resp.status_code == 200
    body = resp.json()
    assert cli.calls == [["plugins", "--format", "json"]]
    assert SECRET not in resp.text
    (row,) = body["plugins"]
    assert row["next_command"] == "export REPOWISE_PLUGIN_PGSTAT_TOKEN=<value>"
    assert [(s["key"], s["secret"], s["set"]) for s in row["settings"]] == [
        ("plugins.pgstat.db", False, True),
        ("REPOWISE_PLUGIN_PGSTAT_TOKEN", True, False),
    ]
    # A setting carries whether it is set, never a value.
    assert all("value" not in s for s in row["settings"])
    (source,) = body["sources"]
    assert source["last_sync"]["batches"] == 2
    assert (body["syncing"], body["sync_error"]) == ([], None)


@pytest.mark.anyio
async def test_listing_answers_502_when_the_cli_fails(
    client: AsyncClient, repo: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        setup_cli, "run_cli", lambda args, *, cwd, timeout: setup_cli.CliRun(1, "boom", "")
    )
    resp = await client.get(f"/api/repos/{repo['id']}/plugins")
    assert resp.status_code == 502
    assert "repowise plugins" in resp.json()["detail"]


@pytest.mark.anyio
async def test_sync_now_runs_in_the_background_and_shows_while_running(
    app, client: AsyncClient, repo: dict, cli: FakeCli
) -> None:
    resp = await client.post(f"/api/repos/{repo['id']}/plugins/sync", json={"source": "pgstat"})
    assert resp.status_code == 202
    assert resp.json() == {"accepted": True, "syncing": ["pgstat"]}
    listing = (await client.get(f"/api/repos/{repo['id']}/plugins")).json()
    assert listing["syncing"] == ["pgstat"]
    again = await client.post(f"/api/repos/{repo['id']}/plugins/sync", json={})
    assert again.status_code == 409

    cli.release.set()
    await _drain(app)
    assert [
        "sources", "sync", "--path", repo["local_path"], "--format", "json", "--source=pgstat"
    ] in cli.calls
    listing = (await client.get(f"/api/repos/{repo['id']}/plugins")).json()
    assert (listing["syncing"], listing["sync_error"]) == ([], None)


@pytest.mark.anyio
async def test_sync_of_every_source_and_a_refusal_is_reported(
    app, client: AsyncClient, repo: dict, cli: FakeCli
) -> None:
    cli.sync_output = json.dumps(
        {"ok": False, "code": "no_index", "message": "This repository has no index to store into."}
    )
    resp = await client.post(f"/api/repos/{repo['id']}/plugins/sync", json={})
    assert resp.json()["syncing"] == [plugins.ALL_SOURCES]
    cli.release.set()
    await _drain(app)
    assert not any(a.startswith("--source") for call in cli.calls for a in call)
    listing = (await client.get(f"/api/repos/{repo['id']}/plugins")).json()
    assert listing["sync_error"] == "This repository has no index to store into."


@pytest.mark.anyio
async def test_sync_refuses_an_option_shaped_source(
    client: AsyncClient, repo: dict, cli: FakeCli
) -> None:
    resp = await client.post(
        f"/api/repos/{repo['id']}/plugins/sync", json={"source": "--path=/etc"}
    )
    assert resp.status_code == 400
    assert cli.calls == []


class _ReaderAuth:
    """A configured authenticator whose caller may read every repo but write none."""

    authorization_servers: tuple[str, ...] = ()
    principal = Principal(id="sso:ana", kind="sso", claims={"roles": ["reader"]})

    def configured(self) -> bool:
        return True

    async def verify_token(self, token: str) -> Principal | None:
        return self.principal if token == "t" else None

    async def authenticate(self, request) -> Principal:
        if request.headers.get("Authorization") != "Bearer t":
            raise HTTPException(status_code=401, detail="Missing API key")
        return self.principal


class _ReadOnly:
    def can_access_repo(self, principal, identifiers, action=Action.READ):
        return action is Action.READ

    def can_use_tool(self, principal, tool, action=Action.READ):
        return action is Action.READ


@pytest.fixture
def reader_only():
    auth.set_authenticator(_ReaderAuth())
    auth.set_authorizer(_ReadOnly())
    yield
    auth.set_authorizer(None)
    auth.set_authenticator(None)


@pytest.mark.anyio
async def test_sync_needs_write_access_and_a_credential(
    client: AsyncClient, repo: dict, cli: FakeCli, reader_only
) -> None:
    url = f"/api/repos/{repo['id']}/plugins"
    assert (await client.post(f"{url}/sync", json={})).status_code == 401
    headers = {"Authorization": "Bearer t"}
    assert (await client.post(f"{url}/sync", json={}, headers=headers)).status_code == 403
    assert (await client.get(url, headers=headers)).status_code == 200
    assert not any(call[:1] == ["sources"] for call in cli.calls)
