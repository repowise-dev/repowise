"""The hosted setup surface: ``agents add --hosted``, ``login --device``, ``hosted status``.

What these protect, in the order a user meets them:

* Each client gets the remote entry in its own shape, in the file it reads,
  and a re-run moves nothing.
* The name rule: ``repowise`` unless a local server holds it, then
  ``repowise-hosted``. A local entry is never overwritten, and ``init`` never
  overwrites a remote one either: it says how to rename it.
* Device sign-in follows RFC 8628 (pending, slow_down, denied, expired, a
  restart on invalid_grant), stores the token like ``repowise login`` and
  never prints it.
* ``hosted status`` passes the platform's payload through and reads signed out
  as a state, not a failure.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.agent_targets.formats.server_entry import (
    HOSTED_NAME,
    LOCAL_NAME,
    RENAME_HINT,
    RemoteServerEntryError,
    ServerSpec,
)
from repowise.cli.agent_targets.registry import get_target
from repowise.cli.agent_targets.types import FileAction, InstallOptions, Scope

URL = "https://api.repowise.dev/mcp/acme/api"
OTHER_URL = "https://api.repowise.dev/mcp/acme/web"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def _install_hosted(target_id: str, repo: Path, url: str = URL):
    target = get_target(target_id)
    assert target is not None and target.hosted is not None
    return target.install(target.hosted.scope, InstallOptions(hosted_url=url), repo_path=repo)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _claude_servers(repo: Path) -> dict:
    data = _read_json(Path.home() / ".claude.json")
    return data["projects"][repo.absolute().as_posix()]["mcpServers"]


# Where each client's hosted entry lands, and what it looks like there.
CASES = {
    "claude-code": (_claude_servers, {"type": "http", "url": URL}),
    "cursor": (lambda r: _read_json(r / ".cursor" / "mcp.json")["mcpServers"], {"url": URL}),
    "vscode": (
        lambda r: _read_json(r / ".vscode" / "mcp.json")["servers"],
        {"type": "http", "url": URL},
    ),
    "codex": (
        lambda r: tomllib.loads((r / ".codex" / "config.toml").read_text(encoding="utf-8"))[
            "mcp_servers"
        ],
        {"url": URL},
    ),
    "windsurf": (
        lambda r: _read_json(Path.home() / ".codeium" / "windsurf" / "mcp_config.json")[
            "mcpServers"
        ],
        {"serverUrl": URL},
    ),
    "opencode": (
        lambda r: _read_json(next(r.glob("opencode.json*")))["mcp"],
        {"type": "remote", "url": URL, "enabled": True},
    ),
}


# ---------------------------------------------------------------------------
# Per-client writers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target_id", sorted(CASES))
def test_each_client_gets_the_remote_entry_in_its_own_shape(target_id: str, repo: Path) -> None:
    servers_of, expected = CASES[target_id]

    result = _install_hosted(target_id, repo)

    assert result.server_name == LOCAL_NAME
    assert [f.action for f in result.files] == [FileAction.CREATED]
    assert servers_of(repo) == {LOCAL_NAME: expected}


@pytest.mark.parametrize("target_id", sorted(CASES))
def test_a_rerun_changes_nothing(target_id: str, repo: Path) -> None:
    _install_hosted(target_id, repo)
    again = _install_hosted(target_id, repo)

    assert again.server_name == LOCAL_NAME
    assert [f.action for f in again.files] == [FileAction.UNCHANGED]


@pytest.mark.parametrize("target_id", ["kiro", "copilot", "hermes"])
def test_clients_without_a_per_repo_remote_entry_say_so(target_id: str) -> None:
    target = get_target(target_id)
    assert target is not None and target.hosted is None


# ---------------------------------------------------------------------------
# Naming and never overwriting a local entry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target_id", ["cursor", "vscode", "codex", "opencode"])
def test_a_local_server_keeps_its_name_and_the_hosted_one_sits_beside_it(
    target_id: str, repo: Path
) -> None:
    target = get_target(target_id)
    assert target is not None
    target.install(Scope.PROJECT, InstallOptions(), repo_path=repo)
    servers_of, expected = CASES[target_id]
    local = servers_of(repo)[LOCAL_NAME]

    result = _install_hosted(target_id, repo)

    assert result.server_name == HOSTED_NAME
    servers = servers_of(repo)
    assert servers[LOCAL_NAME] == local
    assert servers[HOSTED_NAME] == expected
    assert _install_hosted(target_id, repo).files[0].action is FileAction.UNCHANGED


def test_windsurf_names_the_hosted_entry_beside_a_local_one(repo: Path) -> None:
    windsurf = get_target("windsurf")
    assert windsurf is not None
    windsurf.install(Scope.USER, InstallOptions(), repo_path=repo)
    assert windsurf.detect(repo)

    assert _install_hosted("windsurf", repo).server_name == HOSTED_NAME
    servers = CASES["windsurf"][0](repo)
    assert "command" in servers[LOCAL_NAME]
    assert servers[HOSTED_NAME] == {"serverUrl": URL}


def test_claude_code_uses_the_hosted_name_when_a_local_server_loads_elsewhere(
    repo: Path,
) -> None:
    """A user-scope local entry and a local-scope remote one of the same name shadow."""
    config = Path.home() / ".claude.json"
    config.write_text(
        json.dumps({"mcpServers": {LOCAL_NAME: {"command": "repowise", "args": ["mcp"]}}}),
        encoding="utf-8",
    )

    result = _install_hosted("claude-code", repo)

    assert result.server_name == HOSTED_NAME
    data = _read_json(config)
    assert data["mcpServers"][LOCAL_NAME] == {"command": "repowise", "args": ["mcp"]}
    assert _claude_servers(repo) == {HOSTED_NAME: {"type": "http", "url": URL}}


def test_claude_code_reuses_the_project_key_claude_code_already_wrote(repo: Path) -> None:
    """Claude Code has filed projects under either slash on Windows; match, don't duplicate."""
    key = str(repo)  # the platform spelling, backslashes on Windows
    config = Path.home() / ".claude.json"
    original = {"projects": {key: {"allowedTools": [], "mcpServers": {"other": {"url": "x"}}}}}
    config.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")

    _install_hosted("claude-code", repo)

    projects = _read_json(config)["projects"]
    assert list(projects) == [key]
    assert projects[key]["allowedTools"] == []
    assert projects[key]["mcpServers"] == {
        "other": {"url": "x"},
        LOCAL_NAME: {"type": "http", "url": URL},
    }


def test_a_remote_entry_for_another_repo_is_repointed_not_duplicated(repo: Path) -> None:
    path = repo / ".cursor" / "mcp.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"mcpServers": {LOCAL_NAME: {"url": OTHER_URL}}}), encoding="utf-8")

    result = _install_hosted("cursor", repo)

    assert result.server_name == LOCAL_NAME
    assert _read_json(path)["mcpServers"] == {LOCAL_NAME: {"url": URL}}


def test_a_file_that_is_not_strict_json_is_kept_with_the_reason(repo: Path) -> None:
    path = repo / ".vscode" / "mcp.json"
    path.parent.mkdir()
    path.write_text('{\n  // mine\n  "servers": {}\n}\n', encoding="utf-8")

    result = _install_hosted("vscode", repo)

    assert result.server_name is None
    assert result.files[0].action is FileAction.KEPT
    assert "could not be rewritten" in (result.files[0].reason or "")
    assert "// mine" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("servers", "local_elsewhere", "expected"),
    [
        ({}, False, LOCAL_NAME),
        ({}, True, HOSTED_NAME),
        ({LOCAL_NAME: {"command": "repowise"}}, False, HOSTED_NAME),
        ({LOCAL_NAME: {"type": "http", "url": OTHER_URL}}, False, LOCAL_NAME),
        ({HOSTED_NAME: {"type": "http", "url": OTHER_URL}}, False, HOSTED_NAME),
        ({HOSTED_NAME: {"type": "http", "url": URL}}, False, HOSTED_NAME),
        ({LOCAL_NAME: {"serverUrl": URL}}, True, LOCAL_NAME),
    ],
)
def test_the_hosted_name_rule(servers: dict, local_elsewhere: bool, expected: str) -> None:
    spec = ServerSpec({"type": "http", "url": URL}, hosted=True)
    assert spec.name_in(servers, local_elsewhere=local_elsewhere) == expected


def test_a_local_write_refuses_a_remote_entry_under_its_name() -> None:
    with pytest.raises(RemoteServerEntryError):
        ServerSpec({"command": "repowise"}).name_in({LOCAL_NAME: {"type": "http", "url": URL}})


# ---------------------------------------------------------------------------
# init suggests the rename and changes nothing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target_id", ["cursor", "vscode", "codex", "opencode"])
def test_a_local_install_keeps_a_remote_repowise_and_suggests_the_rename(
    target_id: str, repo: Path
) -> None:
    _install_hosted(target_id, repo)
    servers_of, expected = CASES[target_id]
    target = get_target(target_id)
    assert target is not None

    result = target.install(Scope.PROJECT, InstallOptions(), repo_path=repo)

    assert any(f.reason == RENAME_HINT for f in result.files)
    assert any(HOSTED_NAME in note for note in result.notes)
    assert servers_of(repo)[LOCAL_NAME] == expected


def test_init_leaves_a_remote_mcp_json_entry_and_says_how_to_rename(repo: Path) -> None:
    from repowise.cli.agent_targets.targets import claude_code

    path = repo / ".mcp.json"
    original = json.dumps({"mcpServers": {LOCAL_NAME: {"type": "http", "url": URL}}})
    path.write_text(original, encoding="utf-8")

    written = claude_code.write_project_mcp_config(repo)

    assert written.action is FileAction.KEPT
    assert written.reason == RENAME_HINT
    assert path.read_text(encoding="utf-8") == original


def test_init_suggests_renaming_a_remote_user_scope_entry(capsys) -> None:
    from rich.console import Console

    from repowise.cli.editor_integrations.claude import _suggest_hosted_rename

    (Path.home() / ".claude.json").write_text(
        json.dumps({"mcpServers": {LOCAL_NAME: {"type": "http", "url": URL}}}), encoding="utf-8"
    )
    console = Console(force_terminal=False, width=400)
    _suggest_hosted_rename(console, Path.home())
    assert f'Rename that entry to "{HOSTED_NAME}"' in capsys.readouterr().out


def test_init_vscode_prints_the_rename_hint(repo: Path, capsys) -> None:
    from rich.console import Console

    from repowise.cli.editor_integrations.vscode import _write_vscode_files

    _install_hosted("vscode", repo)
    written = _write_vscode_files(Console(force_terminal=False, width=400), repo)

    assert repo / ".vscode" / "mcp.json" not in written
    assert f'Rename that entry to "{HOSTED_NAME}"' in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The nested minimal edit Claude Code's local scope needs
# ---------------------------------------------------------------------------


def test_nested_member_merge_touches_only_its_own_bytes(tmp_path: Path) -> None:
    from repowise.cli.agent_targets.formats.json_merge import merge_json_object_member

    path = tmp_path / "c.json"
    before = '{\n  "a": 1,\n  "projects": {\n    "/r": {\n      "x": [1,2]\n    }\n  }\n}\n'
    path.write_text(before, encoding="utf-8", newline="\n")

    action = merge_json_object_member(path, ("projects", "/r", "mcpServers"), "s", {"url": "u"})

    assert action is FileAction.UPDATED
    text = path.read_bytes().decode("utf-8")
    assert '"x": [1,2]' in text  # untouched formatting
    assert json.loads(text)["projects"]["/r"]["mcpServers"] == {"s": {"url": "u"}}
    again = merge_json_object_member(path, ("projects", "/r", "mcpServers"), "s", {"url": "u"})
    assert again is FileAction.UNCHANGED


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def _cli(*args: str, cwd: Path):
    from repowise.cli.main import cli

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=cwd.parent):
        import os

        os.chdir(cwd)
        return runner.invoke(cli, list(args), catch_exceptions=False)


def test_agents_add_hosted_reports_each_client_and_the_human_step(repo: Path) -> None:
    result = _cli(
        "agents", "add", str(repo), "--hosted", "--repo", "acme/api",
        "--target", "cursor,kiro", "--format", "json", cwd=repo,
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["url"] == URL and payload["hosted_repo"] == "acme/api"
    cursor, kiro = payload["agents"]
    assert cursor["server_name"] == LOCAL_NAME
    assert '"Needs login"' in cursor["next_step"]
    assert cursor["writes"]["project"]["files"][0]["action"] == "created"
    assert kiro["writes"] == {} and URL in kiro["skips"]["hosted"]


def test_agents_add_hosted_reads_the_github_origin(repo: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:acme/api.git"], cwd=repo, check=True
    )
    result = _cli(
        "agents", "add", str(repo), "--hosted", "--target", "vscode", "--format", "json", cwd=repo
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["url"] == URL


def test_agents_add_hosted_without_an_origin_asks_for_repo(repo: Path) -> None:
    from repowise.cli.main import cli

    result = CliRunner().invoke(
        cli, ["agents", "add", str(repo), "--hosted", "--target", "cursor"]
    )
    assert result.exit_code == 2
    assert "--repo owner/name" in result.output


def test_repo_needs_hosted(repo: Path) -> None:
    from repowise.cli.main import cli

    result = CliRunner().invoke(cli, ["agents", "add", str(repo), "--repo", "acme/api"])
    assert result.exit_code == 2
    assert "--repo needs --hosted" in result.output


# ---------------------------------------------------------------------------
# login --device
# ---------------------------------------------------------------------------


@pytest.fixture
def device_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A scripted authorization server: device-code answers, then token answers."""
    from repowise.cli.platform import auth, credentials, store

    monkeypatch.setattr(credentials, "_path", lambda: tmp_path / "credentials.json")
    monkeypatch.setattr(store, "_path", lambda: tmp_path / "platform.json")
    monkeypatch.setattr(auth, "fetch_account", lambda: {"github_username": "octo", "tier": "pro"})
    monkeypatch.setattr("repowise.cli.commands.login_cmd._link_anonymous_id", lambda: None)
    sleeps: list[float] = []
    monkeypatch.setattr("repowise.cli.platform.auth.time.sleep", sleeps.append)

    from types import SimpleNamespace

    server = SimpleNamespace(codes=[], tokens=[], device_requests=[], token_forms=[], sleeps=sleeps)

    def request_device_code(device_name):
        server.device_requests.append(device_name)
        return server.codes.pop(0)

    def token_request(form):
        server.token_forms.append(form)
        return server.tokens.pop(0)

    monkeypatch.setattr(auth, "request_device_code", request_device_code)
    monkeypatch.setattr(auth, "_token_request", token_request)
    return server


def _code(device_code: str = "dc-1", interval: int = 5) -> tuple[int, dict]:
    return 200, {
        "device_code": device_code,
        "user_code": "WDJB-MJHT",
        "verification_uri": "https://repowise.dev/device",
        "verification_uri_complete": "https://repowise.dev/device?code=WDJB-MJHT",
        "expires_in": 600,
        "interval": interval,
    }


TOKEN = {"access_token": "secret-access", "refresh_token": "secret-refresh", "expires_in": 3600}


def _login(*args: str):
    from repowise.cli.commands.login_cmd import login_command

    return CliRunner().invoke(login_command, ["--device", "--device-name", "box", *args])


def test_device_login_polls_through_pending_and_slow_down(device_server) -> None:
    from repowise.cli.platform import credentials

    device_server.codes = [_code()]
    device_server.tokens = [
        (400, {"error": "authorization_pending"}),
        (400, {"error": "slow_down"}),
        (400, {"error": "authorization_pending"}),
        (200, TOKEN),
    ]

    result = _login()

    assert result.exit_code == 0, result.output
    assert device_server.sleeps == [5, 5, 10, 10]
    assert "https://repowise.dev/device" in result.output and "WDJB-MJHT" in result.output
    assert "device?code=WDJB-MJHT" in result.output
    assert "machine you control" in result.output
    assert "secret" not in result.output
    stored = credentials.load()
    assert stored is not None and stored["access_token"] == "secret-access"
    assert stored["token_kind"] == "oauth" and stored["device_name"] == "box"
    form = device_server.token_forms[0]
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
    assert form["client_id"] == "repowise-cli" and form["device_code"] == "dc-1"


@pytest.mark.parametrize(
    ("error", "says"),
    [
        ("access_denied", "denied"),
        ("expired_token", "repowise login --device' again"),
        ("unauthorized_client", "did not allow this CLI"),
    ],
)
def test_device_login_stops_on_a_terminal_answer(device_server, error: str, says: str) -> None:
    from repowise.cli.platform import credentials

    device_server.codes = [_code()]
    device_server.tokens = [(400, {"error": "authorization_pending"}), (400, {"error": error})]

    result = _login()

    assert result.exit_code == 1
    assert says in result.output
    assert credentials.load() is None


def test_device_login_restarts_once_on_invalid_grant(device_server) -> None:
    device_server.codes = [_code("dc-1"), _code("dc-2")]
    device_server.tokens = [(400, {"error": "invalid_grant"}), (200, TOKEN)]

    result = _login()

    assert result.exit_code == 0, result.output
    assert [f["device_code"] for f in device_server.token_forms] == ["dc-1", "dc-2"]


def test_device_login_gives_up_after_a_second_invalid_grant(device_server) -> None:
    device_server.codes = [_code("dc-1"), _code("dc-2")]
    device_server.tokens = [(400, {"error": "invalid_grant"}), (400, {"error": "invalid_grant"})]

    result = _login()

    assert result.exit_code == 1
    assert len(device_server.device_requests) == 2


def test_device_login_expires_when_nobody_approves(device_server, monkeypatch) -> None:
    from repowise.cli.platform import auth

    clock = iter(range(0, 10_000, 300))
    monkeypatch.setattr(auth.time, "monotonic", lambda: next(clock))
    device_server.codes = [_code()]
    device_server.tokens = [(400, {"error": "authorization_pending"})] * 5

    result = _login()

    assert result.exit_code == 1
    assert "expired" in result.output


@pytest.mark.parametrize(
    "answer", [(404, {"detail": "Not Found"}), (400, {"error": "unsupported_grant_type"})]
)
def test_device_login_explains_a_server_without_the_grant(device_server, answer) -> None:
    device_server.codes = [answer]

    result = _login()

    assert result.exit_code == 1
    assert "does not offer device sign-in yet" in result.output


def test_device_and_with_token_are_exclusive() -> None:
    result = _login("--with-token")
    assert result.exit_code == 2


def test_the_device_code_request_sends_a_plain_ascii_name(monkeypatch) -> None:
    from repowise.cli.platform import auth
    from repowise.cli.platform.client import PlatformClient

    sent: dict = {}
    monkeypatch.setattr(
        PlatformClient,
        "post_form",
        lambda self, path, form, timeout=None: sent.update(form) or (0, {}),
    )
    auth.request_device_code("Zoë's Mac")
    assert sent == {"client_id": "repowise-cli", "scope": "read write", "device_name": "Zo's Mac"}


# ---------------------------------------------------------------------------
# hosted status
# ---------------------------------------------------------------------------

STATUS = {
    "account": {"signed_in": True, "user": "octo"},
    "tier": "pro",
    "effective_tier": "pro",
    "repo": {"full_name": "acme/api", "status": "ready", "indexed_commit": "4f2a91c"},
    "steps": [
        {"id": "index", "done": True, "who": "agent", "action": "Index the repo", "url": None},
        {
            "id": "bot",
            "done": False,
            "who": "human",
            "action": "Install the PR bot",
            "url": "https://github.com/apps/repowise-bot?src=agent_setup",
        },
    ],
    "complete": False,
}


@pytest.fixture
def status_server(monkeypatch: pytest.MonkeyPatch):
    from repowise.cli.platform import auth
    from repowise.cli.platform.client import PlatformClient

    calls: list[dict] = []
    state = {"answer": (200, STATUS), "signed_in": True}
    monkeypatch.setattr(
        auth, "auth_headers", lambda: {"Authorization": "Bearer x"} if state["signed_in"] else {}
    )

    def get_json(self, path, *, params=None, timeout=None):
        calls.append({"path": path, "params": params})
        return state["answer"]

    monkeypatch.setattr(PlatformClient, "get_json", get_json)
    state["calls"] = calls
    return state


def _status(*args: str):
    from repowise.cli.commands.hosted_cmd import hosted_group

    return CliRunner().invoke(hosted_group, ["status", *args])


def test_hosted_status_json_is_the_platform_payload(status_server) -> None:
    result = _status("--repo", "acme/api", "--format", "json")
    assert result.exit_code == 0
    assert json.loads(result.output) == STATUS
    assert status_server["calls"] == [
        {"path": "me/setup-status", "params": {"via": "cli", "repo": "acme/api"}}
    ]


def test_hosted_status_table_names_the_steps_and_links(status_server) -> None:
    result = _status("--repo", "acme/api")
    assert result.exit_code == 0
    assert "octo" in result.output and "acme/api" in result.output
    assert "Install the PR bot" in result.output and "src=agent_setup" in result.output


def test_hosted_status_signed_out_is_a_state(status_server) -> None:
    status_server["signed_in"] = False
    result = _status("--repo", "acme/api", "--format", "json")
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["account"]["signed_in"] is False and payload["error"] == "signed_out"
    assert status_server["calls"] == []


@pytest.mark.parametrize(
    ("answer", "code"),
    [((404, {"detail": "Not Found"}), "unsupported"), ((0, {}), "offline"), ((500, {}), "http_500")],
)
def test_hosted_status_failures_exit_1_with_a_code(status_server, answer, code) -> None:
    status_server["answer"] = answer
    result = _status("--repo", "acme/api", "--format", "json")
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == code


def test_hosted_status_rejects_a_malformed_repo(status_server) -> None:
    assert _status("--repo", "not a repo").exit_code == 2


# ---------------------------------------------------------------------------
# hosted reviews on / hosted coverage-pr
# ---------------------------------------------------------------------------

BOT_SETUP = {
    **STATUS,
    "steps": [
        {"id": "index", "done": True, "who": "agent", "action": "Indexed"},
        {
            "id": "bot",
            "done": False,
            "who": "agent",
            "action": "repowise hosted reviews on --repo acme/api",
            "repo_id": "r-1",
        },
    ],
}


@pytest.fixture
def bot_server(status_server, monkeypatch: pytest.MonkeyPatch):
    from repowise.cli.platform.client import PlatformClient

    sent: list[tuple[str, str, object]] = []
    status_server["answer"] = (200, BOT_SETUP)
    status_server["action"] = (200, {"ok": True})

    def send_json(self, method, path, payload=None, *, timeout=None):
        sent.append((method, path, payload))
        return status_server["action"]

    monkeypatch.setattr(PlatformClient, "send_json", send_json)
    status_server["sent"] = sent
    return status_server


def _hosted(*args: str):
    from repowise.cli.commands.hosted_cmd import hosted_group

    return CliRunner().invoke(hosted_group, list(args))


def test_reviews_on_patches_the_bot_repo_it_resolved(bot_server) -> None:
    result = _hosted("reviews", "on", "--repo", "acme/api", "--format", "json")
    assert result.exit_code == 0, result.output
    assert bot_server["sent"] == [
        ("PATCH", "github-app/bot-repos/r-1/enabled", {"enabled": True})
    ]
    payload = json.loads(result.output)
    assert payload["ok"] is True and payload["repo_id"] == "r-1"


def test_coverage_pr_posts_to_the_setup_pr_route(bot_server) -> None:
    result = _hosted("coverage-pr", "--repo", "acme/api")
    assert result.exit_code == 0, result.output
    assert bot_server["sent"] == [("POST", "github-app/bot-repos/r-1/coverage-setup/pr", None)]
    assert "Merging it is yours" in result.output


def test_a_repo_the_bot_does_not_cover_sends_nothing(bot_server) -> None:
    bot_server["answer"] = (200, STATUS)  # no step carries a repo_id
    result = _hosted("reviews", "on", "--repo", "acme/other", "--format", "json")
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "not_installed"
    assert "repowise-bot/installations/new" in json.loads(result.output)["message"]
    assert bot_server["sent"] == []


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        ((402, {"detail": "Private repos require the Pro plan."}), "needs_plan"),
        ((403, {"detail": "Insufficient scope"}), "forbidden"),
        ((404, {"detail": "Repo not found"}), "not_installed"),
        ((409, {"detail": {"message": "Grant Workflows write."}}), "permission_needed"),
        ((504, {}), "pending"),
        ((0, {}), "offline"),
    ],
)
def test_bot_refusals_are_explained(bot_server, answer, code) -> None:
    bot_server["action"] = answer
    result = _hosted("reviews", "on", "--repo", "acme/api", "--format", "json")
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == code


def test_bot_actions_signed_out(bot_server) -> None:
    bot_server["signed_in"] = False
    result = _hosted("coverage-pr", "--repo", "acme/api", "--format", "json")
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "signed_out"


# ---------------------------------------------------------------------------
# Review follow-ups
# ---------------------------------------------------------------------------


def test_opencode_hosted_avoids_a_local_user_scope_name(repo: Path) -> None:
    """OpenCode merges project over user config per key: same name would half-convert."""
    opencode = get_target("opencode")
    assert opencode is not None
    opencode.install(Scope.USER, InstallOptions())

    assert _install_hosted("opencode", repo).server_name == HOSTED_NAME


def test_windsurf_repoint_says_where_it_came_from_and_drops_the_old_spelling(repo: Path) -> None:
    path = Path.home() / ".codeium" / "windsurf" / "mcp_config.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"mcpServers": {LOCAL_NAME: {"url": OTHER_URL, "headers": {"X": "1"}}}}),
        encoding="utf-8",
    )

    result = _install_hosted("windsurf", repo)

    assert result.files[0].reason == f"repointed from {OTHER_URL}"
    assert _read_json(path)["mcpServers"][LOCAL_NAME] == {"headers": {"X": "1"}, "serverUrl": URL}


def test_init_flags_a_hosted_local_scope_entry_that_would_shadow_the_local_one(
    repo: Path, capsys
) -> None:
    from rich.console import Console

    from repowise.cli.editor_integrations.claude import _suggest_hosted_rename

    _install_hosted("claude-code", repo)
    _suggest_hosted_rename(Console(force_terminal=False, width=400), repo)
    assert HOSTED_NAME in capsys.readouterr().out


def test_device_polling_rides_out_a_server_blip(device_server) -> None:
    device_server.codes = [_code()]
    device_server.tokens = [(503, {}), (429, {}), (200, TOKEN)]

    result = _login()

    assert result.exit_code == 0, result.output
    assert device_server.sleeps == [5, 5, 10]


def test_device_login_survives_unreadable_timings(device_server) -> None:
    code = _code()
    code[1].update(interval="soon", expires_in="later", user_code="[bold]X")
    device_server.codes = [code]
    device_server.tokens = [(200, TOKEN)]

    result = _login()

    assert result.exit_code == 0, result.output
    assert "[bold]X" in result.output


def test_bot_actions_on_an_older_server_say_unsupported(bot_server) -> None:
    bot_server["answer"] = (404, {"detail": "Not Found"})
    result = _hosted("reviews", "on", "--repo", "acme/api", "--format", "json")
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "unsupported"


def test_permission_needed_carries_the_card(bot_server) -> None:
    card = {"message": "Grant Workflows write.", "state": "permission_needed"}
    bot_server["action"] = (409, {"detail": card})
    result = _hosted("coverage-pr", "--repo", "acme/api", "--format", "json")
    assert json.loads(result.output)["card"] == card


def test_a_local_server_under_the_hosted_name_is_left_alone(repo: Path) -> None:
    path = repo / ".cursor" / "mcp.json"
    path.parent.mkdir()
    local = {"command": "repowise", "args": ["mcp"]}
    path.write_text(
        json.dumps({"mcpServers": {LOCAL_NAME: local, HOSTED_NAME: local}}), encoding="utf-8"
    )

    result = _install_hosted("cursor", repo)

    assert result.server_name is None
    assert result.files[0].action is FileAction.KEPT
    assert _read_json(path)["mcpServers"][HOSTED_NAME] == local


def test_opencode_local_user_write_refuses_under_a_hosted_project_entry(repo: Path) -> None:
    _install_hosted("opencode", repo)
    opencode = get_target("opencode")
    assert opencode is not None

    result = opencode.install(Scope.USER, InstallOptions(), repo_path=repo)

    assert any(f.action is FileAction.KEPT and f.reason == RENAME_HINT for f in result.files)
    assert not (Path.home() / ".config" / "opencode" / "opencode.json").exists()
