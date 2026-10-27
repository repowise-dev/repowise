"""/api/repos/{id}/setup and /api/setup: the Setup page's payload and its write routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from repowise.core.platform import telemetry
from repowise.server import deps
from repowise.server.routers import setup
from repowise.server.services import setup_cli, setup_status
from tests.unit.server.conftest import create_test_repo

_UI = {"Origin": "http://localhost:3000"}
_UI_PORT = "3000"

_DOCTOR = {
    "ok": False,
    "checks": [
        {"name": "Post-commit hook", "ok": True, "detail": "installed", "id": "post-commit-hook",
         "level": "ok", "fix": None},
        {"name": "Distill rewrite hook", "ok": True,
         "detail": "not installed (opt-in: repowise hook rewrite install)",
         "id": "distill-rewrite-hook", "level": "ok", "fix": None},
        {"name": "Database", "ok": False, "detail": "wiki.db not found", "id": "database",
         "level": "fail", "fix": "repowise init"},
    ],
}


def _agents_listing(repo: str) -> dict:
    return {
        "repo": repo,
        "agents": [
            {
                "id": "claude-code", "display_name": "Claude Code", "tier": "full",
                "docs_url": None, "project_file_id": "claude-md", "present": True,
                "method": "repowise",
                "registrations": [
                    {"method": "repowise", "scope": "user", "config_path": "/h/.claude.json"}
                ],
                "config_files": {"project": [f"{repo}/.mcp.json"], "user": ["/h/.claude.json"]},
            },
            {
                "id": "cursor", "display_name": "Cursor", "tier": "full", "docs_url": None,
                "project_file_id": "cursor-rules", "present": True, "method": "repowise",
                "registrations": [],
                "config_files": {"project": [f"{repo}/.cursor/mcp.json"]},
            },
        ],
    }


def _agents_write_payload(target: str, action: str) -> dict:
    return {
        "action": "add",
        "repo": "r",
        "scope": "both",
        "changed": True,
        "agents": [
            {
                "id": target, "display_name": target, "method": "repowise", "skips": {},
                "writes": {
                    "project": {
                        "files": [{"path": "/r/.cursor/mcp.json", "action": action,
                                   "reason": None}],
                        "notes": [],
                    }
                },
            }
        ],
    }


class FakeCli:
    """Stands in for ``run_cli``: records argv and answers per command."""

    def __init__(self, repo_path: str) -> None:
        self.calls: list[list[str]] = []
        self.repo_path = repo_path
        self.exit_code = 0

    def __call__(self, args: list[str], *, cwd: Path, timeout: float) -> setup_cli.CliRun:
        self.calls.append(args)
        if args[0] == "doctor":
            return setup_cli.CliRun(1, json.dumps(_DOCTOR), "")
        if args == ["agents", "--format", "json"]:
            return setup_cli.CliRun(0, json.dumps(_agents_listing(self.repo_path)), "")
        if args[0] == "agents":
            action = "removed" if args[1] == "remove" else "created"
            return setup_cli.CliRun(self.exit_code, json.dumps(_agents_write_payload("cursor", action)), "")
        return setup_cli.CliRun(self.exit_code, "Post-commit hook: installed\n", "")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / ".repowise").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    for name in ("DO_NOT_TRACK", "REPOWISE_NO_HINTS"):
        monkeypatch.delenv(name, raising=False)
    # What `repowise serve` records when it starts the UI.
    monkeypatch.setenv("REPOWISE_UI_PORT", _UI_PORT)
    monkeypatch.setattr(telemetry, "_state_cache", None)
    return home


@pytest.fixture
async def local(app, home: Path) -> AsyncClient:
    """A loopback client addressed to the API the way the UI's proxy reaches it."""
    app.include_router(setup.router)
    setup_status.invalidate()
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://localhost:7337") as c:
        yield c
    setup_status.invalidate()
    await _settle()


async def _settle() -> None:
    """Let the background builds a request started finish."""
    import asyncio

    while setup_status._running:
        await asyncio.gather(*setup_status._running, return_exceptions=True)


@pytest.fixture
async def repo(local: AsyncClient, tmp_path: Path) -> dict:
    return await create_test_repo(local, tmp_path)


@pytest.fixture
def cli(repo: dict, monkeypatch: pytest.MonkeyPatch) -> FakeCli:
    fake = FakeCli(repo["local_path"])
    monkeypatch.setattr(setup_cli, "run_cli", fake)
    return fake


# -- GET -----------------------------------------------------------------------


@pytest.mark.anyio
async def test_setup_payload_shape(
    local: AsyncClient, repo: dict, cli: FakeCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.savings import service

    monkeypatch.setattr(
        service,
        "load_agent_calls",
        lambda root: {
            "claude_code": ("2026-10-01T10:00:00Z", "2026-10-02T10:00:00Z"),
            "unknown": ("2026-09-30T10:00:00Z", "2026-09-30T11:00:00Z"),
        },
    )
    report = Path(repo["local_path"]) / "coverage" / "lcov.info"
    report.parent.mkdir()
    report.write_text("TN:\n", encoding="utf-8")

    resp = await local.get(f"/api/repos/{repo['id']}/setup")
    assert resp.status_code == 200
    body = resp.json()

    assert body["index"]["state"] == "incomplete"  # no file nodes yet
    assert body["agents_available"] is True
    claude, cursor = body["agents"]
    assert claude["connected"] and claude["installed"]
    assert claude["first_call_at"] == "2026-10-01T10:00:00Z"
    assert cursor["connected"] is False and cursor["installed"] is True
    assert cursor["first_call_at"] is None
    assert cursor["config_files"]["project"][0].endswith(".cursor/mcp.json")
    assert body["calls"] == {
        "first_call_at": "2026-09-30T10:00:00Z",
        "last_call_at": "2026-10-02T10:00:00Z",
        "unattributed": True,
    }
    assert body["sync"] == {"installed": True, "detail": "installed"}
    assert body["rewrite_hook"]["state"] == "not_installed"
    assert body["coverage"]["ingested_at"] is None
    assert [r["path"] for r in body["coverage"]["discovered"]] == ["coverage/lcov.info"]
    assert body["model"]["docs_mode"] == "none"
    assert {c["id"] for c in body["checks"]} == {
        "post-commit-hook", "distill-rewrite-hook", "database"
    }
    assert body["checks"][2] == {
        "id": "database", "name": "Database", "level": "fail",
        "detail": "wiki.db not found", "fix": "repowise init",
    }
    assert body["onboarding"] == {
        "welcome_seen": {}, "setup_pill_hidden": False, "hosted_card_hidden": False,
        "hints_enabled": True, "signed_in": False,
    }
    assert body["writes_allowed"] is True
    assert body["calls_available"] and body["coverage_available"] and body["model_available"]


@pytest.mark.anyio
async def test_setup_payload_is_cached_between_page_loads(
    local: AsyncClient, repo: dict, cli: FakeCli
) -> None:
    await local.get(f"/api/repos/{repo['id']}/setup")
    await local.get(f"/api/repos/{repo['id']}/setup")
    await _settle()
    doctors = [c for c in cli.calls if c[0] == "doctor"]
    # One quick run for the page, one full run behind it.
    assert [("--quick" in c) for c in doctors] == [True, False]


@pytest.mark.anyio
async def test_a_stale_payload_is_served_at_once_and_rebuilt_behind_it(
    local: AsyncClient, repo: dict, cli: FakeCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = (await local.get(f"/api/repos/{repo['id']}/setup")).json()
    await _settle()
    monkeypatch.setattr(setup_status, "SETUP_CACHE_SECONDS", 0.0)
    calls_before = len(cli.calls)

    stale = (await local.get(f"/api/repos/{repo['id']}/setup")).json()
    assert stale["generated_at"] == first["generated_at"]  # served without waiting
    await _settle()
    fresh = (await local.get(f"/api/repos/{repo['id']}/setup")).json()
    assert fresh["generated_at"] != first["generated_at"]
    assert any("--quick" in c for c in cli.calls[calls_before:])


@pytest.mark.anyio
async def test_a_write_makes_the_next_load_wait_for_a_new_probe(
    local: AsyncClient, repo: dict, cli: FakeCli
) -> None:
    first = (await local.get(f"/api/repos/{repo['id']}/setup")).json()
    await _settle()
    setup_status.invalidate(repo["id"])
    after = (await local.get(f"/api/repos/{repo['id']}/setup")).json()
    assert after["generated_at"] != first["generated_at"]


@pytest.mark.anyio
async def test_a_payload_past_the_stale_limit_waits_for_a_rebuild(
    local: AsyncClient, repo: dict, cli: FakeCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = (await local.get(f"/api/repos/{repo['id']}/setup")).json()
    await _settle()
    monkeypatch.setattr(setup_status, "SETUP_STALE_SECONDS", 0.0)
    after = (await local.get(f"/api/repos/{repo['id']}/setup")).json()
    assert after["generated_at"] != first["generated_at"]


_MCP_FAIL = {"name": "MCP server responds", "ok": False, "detail": "server exited with code 1",
             "id": "mcp-responds", "level": "fail", "fix": None}


def _full_doctor_answers(cli: FakeCli, monkeypatch: pytest.MonkeyPatch, answers: list) -> None:
    """The full (not --quick) doctor runs answer from *answers* in turn."""
    original = cli.__call__

    def answer(args, *, cwd, timeout):
        if args[0] == "doctor" and "--quick" not in args:
            answer = answers.pop(0)
            return answer() if callable(answer) else answer
        return original(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(setup_cli, "run_cli", answer)


@pytest.mark.anyio
async def test_full_rows_outlive_a_write_until_the_next_full_run(
    local: AsyncClient, repo: dict, cli: FakeCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    failing = setup_cli.CliRun(1, json.dumps({"ok": False, "checks": [_MCP_FAIL]}), "")
    passing = setup_cli.CliRun(0, json.dumps({"ok": True, "checks": [
        {**_MCP_FAIL, "ok": True, "level": "ok", "detail": "initialised"}]}), "")
    import threading

    rerun = threading.Event()
    _full_doctor_answers(cli, monkeypatch, [failing, lambda: rerun.wait(10) and passing])
    url = f"/api/repos/{repo['id']}/setup"
    await local.get(url)
    await _settle()
    setup_status.invalidate(repo["id"])

    body = (await local.get(url)).json()  # the full run is due again, not done yet
    assert [c["level"] for c in body["checks"] if c["id"] == "mcp-responds"] == ["fail"]
    rerun.set()
    await _settle()
    body = (await local.get(url)).json()
    assert [c["level"] for c in body["checks"] if c["id"] == "mcp-responds"] == ["ok"]


@pytest.mark.anyio
async def test_a_failed_full_run_is_not_retried_on_every_rebuild(
    local: AsyncClient, repo: dict, cli: FakeCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    _full_doctor_answers(cli, monkeypatch, [setup_cli.CliRun(None, "", "timed out")])
    monkeypatch.setattr(setup_status, "SETUP_CACHE_SECONDS", 0.0)
    url = f"/api/repos/{repo['id']}/setup"
    for _ in range(3):
        await local.get(url)
        await _settle()
    # One full run (popping a second answer would raise); the quick rows stand.
    body = (await local.get(url)).json()
    assert body["checks_available"] is True
    assert "mcp-responds" not in {c["id"] for c in body["checks"]}


@pytest.mark.anyio
async def test_the_full_doctor_rows_join_the_quick_ones(
    local: AsyncClient, repo: dict, cli: FakeCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp = {"name": "MCP server responds", "ok": False, "detail": "server exited with code 1",
           "id": "mcp-responds", "level": "fail", "fix": None}
    stale_hook = {**_DOCTOR["checks"][0], "detail": "not installed", "level": "warn"}
    original = cli.__call__

    def answer(args, *, cwd, timeout):
        if args[0] == "doctor" and "--quick" not in args:
            full = {"ok": False, "checks": [stale_hook, *_DOCTOR["checks"][1:], mcp]}
            return setup_cli.CliRun(1, json.dumps(full), "")
        return original(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(setup_cli, "run_cli", answer)
    await local.get(f"/api/repos/{repo['id']}/setup")
    await _settle()
    body = (await local.get(f"/api/repos/{repo['id']}/setup")).json()
    ids = [c["id"] for c in body["checks"]]
    assert ids == ["post-commit-hook", "distill-rewrite-hook", "database", "mcp-responds"]
    # A row both runs report comes from the quick one.
    assert body["sync"]["installed"] is True


@pytest.mark.anyio
async def test_concurrent_cold_loads_share_one_build_that_survives_a_dropped_request(
    local: AsyncClient, repo: dict, cli: FakeCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import threading

    gate, entered = threading.Event(), threading.Event()
    original = cli.__call__

    def slow(args, *, cwd, timeout):
        if args[0] == "doctor":
            entered.set()
            gate.wait(10)
        return original(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(setup_cli, "run_cli", slow)
    url = f"/api/repos/{repo['id']}/setup"
    dropped = asyncio.create_task(local.get(url))
    waiting = [asyncio.create_task(local.get(url)) for _ in range(2)]
    await asyncio.to_thread(entered.wait, 10)
    await asyncio.sleep(0.2)  # the other loads reach the shared build
    dropped.cancel()  # the proxy gave up on this one
    await asyncio.sleep(0.05)
    gate.set()
    bodies = [(await t).json() for t in waiting]
    assert bodies[0]["generated_at"] == bodies[1]["generated_at"]
    assert sum(1 for c in cli.calls if "--quick" in c) == 1


@pytest.mark.anyio
async def test_rewrite_hook_not_applicable_without_claude_code(
    local: AsyncClient, repo: dict, cli: FakeCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = _agents_listing(repo["local_path"])
    listing["agents"][0].update(present=False, registrations=[])
    original = cli.__call__

    def answer(args, *, cwd, timeout):
        if args == ["agents", "--format", "json"]:
            return setup_cli.CliRun(0, json.dumps(listing), "")
        return original(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(setup_cli, "run_cli", answer)
    body = (await local.get(f"/api/repos/{repo['id']}/setup")).json()
    assert body["rewrite_hook"]["state"] == "not_applicable"


@pytest.mark.anyio
async def test_setup_degrades_when_the_cli_cannot_run(
    local: AsyncClient, repo: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        setup_cli, "run_cli", lambda args, **kw: setup_cli.CliRun(None, "", "no python")
    )
    body = (await local.get(f"/api/repos/{repo['id']}/setup")).json()
    assert body["agents_available"] is False and body["agents"] == []
    assert body["checks_available"] is False and body["checks"] == []
    assert body["sync"]["installed"] is None
    assert body["rewrite_hook"]["state"] == "unknown"


@pytest.mark.anyio
async def test_setup_flags_sections_it_could_not_read(
    local: AsyncClient, repo: dict, cli: FakeCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    from repowise.core.analysis.health import coverage
    from repowise.core.savings import service
    from repowise.server import provider_config

    def ledger_locked(root):
        raise sqlite3.OperationalError("database is locked")

    def bad_config(**kwargs):
        raise ValueError("Could not parse config.yaml")

    def unreadable_dir(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(service, "load_agent_calls", ledger_locked)
    monkeypatch.setattr(provider_config, "list_provider_status", bad_config)
    monkeypatch.setattr(coverage, "discover_artifacts", unreadable_dir)

    resp = await local.get(f"/api/repos/{repo['id']}/setup")
    assert resp.status_code == 200
    body = resp.json()
    assert body["calls_available"] is False
    assert body["coverage_available"] is False
    assert body["model_available"] is False
    assert body["model"]["docs_mode"] == "none"
    # The CLI-backed sections are unaffected.
    assert body["agents_available"] is True and body["checks_available"] is True


# -- writes ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_connect_agent_runs_agents_add_and_names_the_files(
    local: AsyncClient, repo: dict, cli: FakeCli
) -> None:
    resp = await local.post(f"/api/repos/{repo['id']}/setup/agents/cursor", headers=_UI)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["command"] == "repowise agents add --target=cursor"
    assert body["files"] == [
        {"path": "/r/.cursor/mcp.json", "action": "created", "scope": "project", "reason": None}
    ]
    assert cli.calls[-1] == [
        "agents", "add", repo["local_path"], "--target=cursor", "--format", "json", "--yes"
    ]


@pytest.mark.anyio
async def test_disconnect_agent_runs_agents_remove(
    local: AsyncClient, repo: dict, cli: FakeCli
) -> None:
    resp = await local.delete(f"/api/repos/{repo['id']}/setup/agents/cursor", headers=_UI)
    assert resp.status_code == 200
    assert resp.json()["files"][0]["action"] == "removed"
    assert cli.calls[-1] == [
        "agents", "remove", repo["local_path"], "--target=cursor", "--format", "json"
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("target", ["all", "auto", "none", "cursor,codex", "Cursor", "../x"])
async def test_connect_agent_refuses_anything_but_one_id(
    local: AsyncClient, repo: dict, cli: FakeCli, target: str
) -> None:
    resp = await local.post(f"/api/repos/{repo['id']}/setup/agents/{target}", headers=_UI)
    assert resp.status_code in (400, 404)
    assert cli.calls == []


@pytest.mark.anyio
async def test_unknown_agent_id_is_400(local: AsyncClient, repo: dict, cli: FakeCli) -> None:
    cli.exit_code = 2
    resp = await local.post(f"/api/repos/{repo['id']}/setup/agents/notanagent", headers=_UI)
    assert resp.status_code == 400


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("name", "enabled", "argv", "command"),
    [
        ("post-commit", True, ["hook", "install"], "repowise hook install"),
        ("post-commit", False, ["hook", "uninstall"], "repowise hook uninstall"),
        ("rewrite", True, ["hook", "rewrite", "install"], "repowise hook rewrite install"),
        ("rewrite", False, ["hook", "rewrite", "uninstall"], "repowise hook rewrite uninstall"),
    ],
)
async def test_hooks_run_the_hook_commands(
    local: AsyncClient,
    repo: dict,
    cli: FakeCli,
    name: str,
    enabled: bool,
    argv: list[str],
    command: str,
) -> None:
    resp = await local.put(
        f"/api/repos/{repo['id']}/setup/hooks/{name}", json={"enabled": enabled}, headers=_UI
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["command"] == command
    assert body["output"] == ["Post-commit hook: installed"]
    # Turning sync off runs `hook uninstall`, which removes more; the answer says so.
    assert ("security check" in body["message"]) is (name == "post-commit" and not enabled)
    assert cli.calls[-1] == [*argv, repo["local_path"], "--no-workspace"]


@pytest.mark.anyio
async def test_a_failed_hook_command_says_so(
    local: AsyncClient, repo: dict, cli: FakeCli
) -> None:
    cli.exit_code = 1
    resp = await local.put(
        f"/api/repos/{repo['id']}/setup/hooks/post-commit", json={"enabled": True}, headers=_UI
    )
    body = resp.json()
    assert body["ok"] is False
    assert "repowise hook install" in body["message"]


@pytest.mark.anyio
async def test_unknown_hook_is_404(local: AsyncClient, repo: dict, cli: FakeCli) -> None:
    resp = await local.put(
        f"/api/repos/{repo['id']}/setup/hooks/pre-push", json={"enabled": True}, headers=_UI
    )
    assert resp.status_code == 404
    assert cli.calls == []


@pytest.mark.anyio
async def test_coverage_add_runs_for_a_report_inside_the_repo(
    local: AsyncClient, repo: dict, cli: FakeCli
) -> None:
    report = Path(repo["local_path"]) / "coverage" / "lcov.info"
    report.parent.mkdir()
    report.write_text("TN:\n", encoding="utf-8")
    resp = await local.post(
        f"/api/repos/{repo['id']}/setup/coverage", json={"path": "coverage/lcov.info"}, headers=_UI
    )
    assert resp.status_code == 200
    assert resp.json()["command"] == "repowise coverage add coverage/lcov.info"
    assert cli.calls[-1] == [
        "coverage", "add", str(report.resolve()), "--path", repo["local_path"]
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["../outside.info", "coverage/*.info", "missing.info", "a=b"])
async def test_coverage_add_refuses_paths_outside_or_vague(
    local: AsyncClient, repo: dict, cli: FakeCli, tmp_path: Path, path: str
) -> None:
    (Path(repo["local_path"]).parent / "outside.info").write_text("TN:\n", encoding="utf-8")
    resp = await local.post(
        f"/api/repos/{repo['id']}/setup/coverage", json={"path": path}, headers=_UI
    )
    assert resp.status_code == 400
    assert cli.calls == []


# -- write refusals ----------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://evil.example"},
        {"Origin": "http://localhost:8080"},
        {"Origin": "null"},
        {"Origin": "http://localhost:3000", "Sec-Fetch-Site": "cross-site"},
        {"Origin": "http://localhost:3000", "Sec-Fetch-Site": "same-site"},
        {"Origin": "http://localhost:3000", "Host": "rebound.example:7337"},
    ],
)
async def test_writes_refuse_requests_not_from_the_local_ui(
    local: AsyncClient, repo: dict, cli: FakeCli, headers: dict
) -> None:
    resp = await local.put(
        f"/api/repos/{repo['id']}/setup/hooks/post-commit", json={"enabled": True}, headers=headers
    )
    assert resp.status_code == 403
    assert "web UI" in resp.json()["detail"]
    assert cli.calls == []


@pytest.mark.anyio
async def test_writes_accept_the_api_port_and_the_recorded_ui_port(
    local: AsyncClient, repo: dict, cli: FakeCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"/api/repos/{repo['id']}/setup/hooks/post-commit"
    ok = await local.put(url, json={"enabled": True}, headers={"Origin": "http://127.0.0.1:7337"})
    assert ok.status_code == 200
    monkeypatch.setenv("REPOWISE_UI_PORT", "4123")
    moved = await local.put(url, json={"enabled": True}, headers={"Origin": "http://localhost:4123"})
    assert moved.status_code == 200
    old = await local.put(url, json={"enabled": True}, headers=_UI)
    assert old.status_code == 403
    # With no UI recorded, the usual UI port is not trusted on its own.
    monkeypatch.delenv("REPOWISE_UI_PORT")
    unrecorded = await local.put(url, json={"enabled": True}, headers=_UI)
    assert unrecorded.status_code == 403


@pytest.mark.anyio
async def test_writes_refused_when_the_server_has_an_api_key(
    local: AsyncClient, repo: dict, cli: FakeCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deps, "_API_KEY", "secret")
    resp = await local.put(
        f"/api/repos/{repo['id']}/setup/hooks/post-commit",
        json={"enabled": True},
        headers={**_UI, "Authorization": "Bearer secret"},
    )
    assert resp.status_code == 403
    assert "API key" in resp.json()["detail"]
    assert cli.calls == []
    # Reads still follow the usual auth, and say writes are off.
    body = (
        await local.get(
            f"/api/repos/{repo['id']}/setup", headers={"Authorization": "Bearer secret"}
        )
    ).json()
    assert body["writes_allowed"] is False


@pytest.mark.anyio
async def test_writes_refused_from_a_remote_peer(app, repo: dict, cli: FakeCli) -> None:
    transport = ASGITransport(app=app, client=("10.0.0.5", 50000))
    async with AsyncClient(transport=transport, base_url="http://localhost:7337") as remote:
        resp = await remote.put(
            f"/api/repos/{repo['id']}/setup/hooks/post-commit",
            json={"enabled": True},
            headers=_UI,
        )
    assert resp.status_code == 403
    assert cli.calls == []


# -- onboarding -------------------------------------------------------------------


@pytest.mark.anyio
async def test_onboarding_round_trip(local: AsyncClient, home: Path) -> None:
    platform_json = home / ".repowise" / "platform.json"
    platform_json.write_text(json.dumps({"anon_id": "keep-me"}), encoding="utf-8")

    resp = await local.patch(
        "/api/setup/onboarding",
        json={"welcome_seen_repo": "abc123", "hosted_card_hidden": True},
        headers=_UI,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["welcome_seen"]) == {"abc123"}
    assert body["hosted_card_hidden"] is True and body["setup_pill_hidden"] is False

    again = (await local.get("/api/setup/onboarding")).json()
    assert again == body
    stored = json.loads(platform_json.read_text(encoding="utf-8"))
    assert stored["anon_id"] == "keep-me"
    assert stored["onboarding"]["welcome_seen"] == body["welcome_seen"]

    await local.patch("/api/setup/onboarding", json={"hosted_card_hidden": False}, headers=_UI)
    after = (await local.get("/api/setup/onboarding")).json()
    assert after["hosted_card_hidden"] is False
    assert set(after["welcome_seen"]) == {"abc123"}
    assert json.loads(platform_json.read_text(encoding="utf-8"))["anon_id"] == "keep-me"


@pytest.mark.anyio
async def test_onboarding_reports_hint_switches(
    local: AsyncClient, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    (home / ".repowise" / "credentials.json").write_text(
        json.dumps({"access_token": "t"}), encoding="utf-8"
    )
    body = (await local.get("/api/setup/onboarding")).json()
    assert body["hints_enabled"] is False and body["signed_in"] is True


@pytest.mark.anyio
async def test_onboarding_write_never_replaces_a_file_it_cannot_read(
    local: AsyncClient, home: Path
) -> None:
    platform_json = home / ".repowise" / "platform.json"
    platform_json.write_text('{"anon_id": "keep-me", ', encoding="utf-8")
    resp = await local.patch(
        "/api/setup/onboarding", json={"setup_pill_hidden": True}, headers=_UI
    )
    assert resp.status_code == 500
    assert platform_json.read_text(encoding="utf-8") == '{"anon_id": "keep-me", '


@pytest.mark.anyio
async def test_a_write_during_a_build_keeps_the_old_payload_out_of_the_cache(
    local: AsyncClient, repo: dict, cli: FakeCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    def doctor_then_write(args, **kw):
        run = cli(args, **kw)
        if args[0] == "doctor" and "--quick" in args:
            setup_status.invalidate(repo["id"])  # a write lands while this build runs
        return run

    monkeypatch.setattr(setup_cli, "run_cli", doctor_then_write)
    await local.get(f"/api/repos/{repo['id']}/setup")
    await _settle()
    assert repo["id"] not in setup_status._probes
    assert repo["id"] not in setup_status._full_checks


@pytest.mark.anyio
async def test_onboarding_patch_needs_the_local_ui(local: AsyncClient, home: Path) -> None:
    resp = await local.patch("/api/setup/onboarding", json={"setup_pill_hidden": True})
    assert resp.status_code == 403
    assert not (home / ".repowise" / "platform.json").exists()


@pytest.mark.anyio
async def test_onboarding_rejects_a_malformed_repo_id(local: AsyncClient) -> None:
    resp = await local.patch(
        "/api/setup/onboarding", json={"welcome_seen_repo": "../../x"}, headers=_UI
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("command", "flags"),
    [
        (["doctor"], ["--no-workspace", "--format", "--quick"]),
        (["agents", "add"], ["--target", "--format", "--yes"]),
        (["agents", "remove"], ["--target", "--format"]),
        (["hook", "install"], ["--no-workspace"]),
        (["hook", "uninstall"], ["--no-workspace"]),
        (["hook", "rewrite", "install"], ["--no-workspace"]),
        (["hook", "rewrite", "uninstall"], ["--no-workspace"]),
        (["coverage", "add"], ["--path"]),
    ],
)
def test_the_cli_commands_take_the_flags_the_routes_pass(
    command: list[str], flags: list[str]
) -> None:
    """The routes run these commands in a child process; their flags must exist."""
    from click.testing import CliRunner

    from repowise.cli.main import cli as repowise_cli

    result = CliRunner().invoke(repowise_cli, [*command, "--help"])
    assert result.exit_code == 0, result.output
    for flag in flags:
        assert flag in result.output
