"""/api/platform: identity from ~/.repowise, and publish through the CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.platform import telemetry
from repowise.server.routers import platform
from tests.unit.server.conftest import create_test_repo


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake home with an empty ~/.repowise, and no consent env vars."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for name in ("DO_NOT_TRACK", "REPOWISE_TELEMETRY_DISABLED", "REPOWISE_NO_HINTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(telemetry, "_state_cache", None)
    (tmp_path / ".repowise").mkdir()
    return tmp_path


@pytest.fixture
async def platform_client(app, client: AsyncClient) -> AsyncClient:
    app.include_router(platform.router)
    return client


def _write(home: Path, name: str, data: object) -> None:
    (home / ".repowise" / name).write_text(json.dumps(data), encoding="utf-8")


async def _identity(client: AsyncClient) -> dict:
    resp = await client.get("/api/platform/identity")
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.anyio
async def test_identity_with_nothing_stored(platform_client: AsyncClient, home: Path) -> None:
    assert await _identity(platform_client) == {
        "signed_in": False,
        "hints_enabled": True,
    }
    # Read-only: answering must not mint an id the CLI would then disagree with.
    assert not (home / ".repowise" / "platform.json").exists()


@pytest.mark.anyio
async def test_identity_never_carries_the_install_id(
    platform_client: AsyncClient, home: Path
) -> None:
    # The UI's links carry their source only, so the id has no reason to leave.
    _write(home, "platform.json", {"anon_id": "abc123"})
    assert "anon_id" not in await _identity(platform_client)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("creds", "signed_in"),
    [
        ({"access_token": "tok"}, True),
        ({"access_token": "tok", "stale": True}, False),
        ({"refresh_token": "only"}, False),
        (["not", "a", "dict"], False),
    ],
)
async def test_identity_signed_in(
    platform_client: AsyncClient, home: Path, creds: object, signed_in: bool
) -> None:
    _write(home, "credentials.json", creds)
    assert (await _identity(platform_client))["signed_in"] is signed_in


@pytest.mark.anyio
async def test_identity_hints_off_in_platform_json(
    platform_client: AsyncClient, home: Path
) -> None:
    _write(home, "platform.json", {"hints_enabled": False})
    assert (await _identity(platform_client))["hints_enabled"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("var", ["REPOWISE_NO_HINTS", "DO_NOT_TRACK"])
async def test_identity_hints_off_by_env(
    platform_client: AsyncClient, home: Path, monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    monkeypatch.setenv(var, "1")
    assert (await _identity(platform_client))["hints_enabled"] is False


_PUBLISHED = {
    "outcome": "published",
    "message": "Your repo is being indexed:",
    "url": "https://repowise.dev/s/x/indexing?src=local_web_publish",
    "details": ["line"],
    "open_url": "https://repowise.dev/s/x/indexing?src=local_web_publish",
    "repo": "o/n",
}


@pytest.mark.anyio
async def test_publish_runs_the_cli_and_returns_its_answer(
    platform_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await create_test_repo(platform_client)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["timeout"] == platform._TIMEOUT_SECONDS
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(_PUBLISHED), stderr="")

    monkeypatch.setattr(platform.subprocess, "run", fake_run)
    resp = await platform_client.post("/api/platform/publish", json={"repo_id": repo["id"]})

    assert resp.status_code == 200
    assert resp.json() == _PUBLISHED
    assert calls == [
        [
            sys.executable,
            "-m",
            "repowise.cli.main",
            "publish",
            repo["local_path"],
            "--format",
            "json",
            "--no-open",
            "--src",
            "local_web_publish",
        ]
    ]


@pytest.mark.anyio
async def test_publish_passes_a_refusal_through(
    platform_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await create_test_repo(platform_client)
    refusal = {**_PUBLISHED, "outcome": "signed_out", "message": "Sign in first", "url": None}
    monkeypatch.setattr(
        platform.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, json.dumps(refusal), ""),
    )
    resp = await platform_client.post("/api/platform/publish", json={"repo_id": repo["id"]})
    assert resp.status_code == 200
    assert resp.json() == refusal


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("no python"),
        subprocess.TimeoutExpired(["repowise"], 90),
        "not json at all",
        "[1, 2]",
    ],
)
async def test_publish_answers_error_when_the_cli_cannot_run(
    platform_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, failure: object
) -> None:
    repo = await create_test_repo(platform_client)

    def fake_run(argv, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return subprocess.CompletedProcess(argv, 1, stdout=failure, stderr="Traceback")

    monkeypatch.setattr(platform.subprocess, "run", fake_run)
    resp = await platform_client.post("/api/platform/publish", json={"repo_id": repo["id"]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "error"
    assert "repowise publish" in body["message"]


@pytest.mark.anyio
async def test_publish_unknown_repo_is_404(
    platform_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform.subprocess, "run", lambda *a, **k: pytest.fail("ran the CLI"))
    resp = await platform_client.post("/api/platform/publish", json={"repo_id": "nope"})
    assert resp.status_code == 404


def test_cli_main_runs_as_a_module() -> None:
    """The argv above relies on ``python -m repowise.cli.main`` running the CLI."""
    proc = subprocess.run(
        [sys.executable, "-m", "repowise.cli.main", "publish", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--format" in proc.stdout
