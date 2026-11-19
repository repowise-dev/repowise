"""``doctor`` names a repowise server the host loads under two names, on every host.

Driven by each target's own :meth:`server_maps`, so the files written here are
the files the descriptor says the host reads. Home is redirected by the CLI
conftest, so nothing here reaches real user config.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.cli.agent_targets.formats.server_entry import launches_repowise
from repowise.cli.agent_targets.registry import (
    diagnose,
    get_target,
    list_target_ids,
    loaded_servers,
)
from repowise.cli.agent_targets.types import DoctorStatus, Scope

#: Where each host keeps its server map inside the file it reads.
SERVER_KEY = {
    "claude-code": "mcpServers",
    "codex": "mcp_servers",
    "vscode": "servers",
    "cursor": "mcpServers",
    "opencode": "mcp",
    "hermes": "mcp_servers",
    "copilot": "mcpServers",
    "kiro": "mcpServers",
    "windsurf": "mcpServers",
}

LOCAL = {"command": "repowise", "args": ["mcp", "--transport", "stdio"]}


def test_every_target_names_its_server_key() -> None:
    assert set(SERVER_KEY) == set(list_target_ids())


def _write(path: Path, key: str, servers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".toml":
        lines = []
        for name, entry in servers.items():
            lines.append(f'[{key}."{name}"]')
            lines += [f"{k} = {json.dumps(v)}" for k, v in entry.items()]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif path.suffix in (".yaml", ".yml"):
        import yaml

        path.write_text(yaml.safe_dump({key: servers}), encoding="utf-8")
    else:
        path.write_text(json.dumps({key: servers}), encoding="utf-8")


def _maps(target_id: str, repo: Path):
    target = get_target(target_id)
    assert target is not None
    return target, target.server_maps(repo)


@pytest.mark.parametrize("target_id", sorted(SERVER_KEY))
def test_a_second_name_launching_repowise_is_a_double_registration(
    target_id: str, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target, maps = _maps(target_id, repo)
    assert maps, "every host reads at least one server map"
    path = maps[-1].config_path
    _write(path, SERVER_KEY[target_id], {"repowise": LOCAL, "repowise-local": LOCAL})

    report = diagnose(target, repo_path=repo)

    issue = next(i for i in report.issues if "registered twice" in i)
    assert report.status is DoctorStatus.STALE
    assert not report.repairable
    assert "`repowise`" in issue and "`repowise-local`" in issue
    assert str(path) in issue
    assert "Keep `repowise`" in issue
    assert "—" not in issue
    # A hand-added name is a hand edit: no command claims to remove it.
    if report.fix_command:
        assert "agents remove" not in report.fix_command


@pytest.mark.parametrize("target_id", sorted(SERVER_KEY))
def test_one_name_in_one_file_is_not_a_double_registration(
    target_id: str, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target, maps = _maps(target_id, repo)
    _write(maps[-1].config_path, SERVER_KEY[target_id], {"repowise": LOCAL})

    assert list(loaded_servers(target, repo)) == ["repowise"]
    assert not any("registered" in i for i in diagnose(target, repo_path=repo).issues)


@pytest.mark.parametrize("target_id", ["claude-code", "codex", "cursor", "kiro", "opencode"])
def test_one_name_at_user_and_project_scope_is_one_server(target_id: str, tmp_path: Path) -> None:
    """Hosts merge their server maps by name, so the same name twice loads once."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target, maps = _maps(target_id, repo)
    scopes = {m.scope for m in maps}
    assert scopes == {Scope.USER, Scope.PROJECT}
    written = set()
    for server_map in maps:
        if server_map.config_path in written or server_map.servers:
            continue
        if target_id == "claude-code" and server_map.scope is Scope.PROJECT and (
            server_map.config_path.name != ".mcp.json"
        ):
            continue  # local scope lives in the user file, written above
        _write(server_map.config_path, SERVER_KEY[target_id], {"repowise": LOCAL})
        written.add(server_map.config_path)

    assert list(loaded_servers(target, repo)) == ["repowise"]
    assert not any("registered" in i for i in diagnose(target, repo_path=repo).issues)


def test_a_hosted_entry_beside_the_local_one_is_not_a_double(tmp_path: Path) -> None:
    """``agents add --hosted`` puts ``repowise-hosted`` beside a local server on purpose."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target, maps = _maps("cursor", repo)
    hosted = {"type": "http", "url": "https://api.example.test/mcp"}
    _write(maps[-1].config_path, "mcpServers", {"repowise": LOCAL, "repowise-hosted": hosted})

    assert list(loaded_servers(target, repo)) == ["repowise"]


def _install_plugin(tmp_path: Path, monkeypatch):
    from repowise.cli.agent_targets.targets import claude_code as target_mod

    install = tmp_path / "cache" / "repowise"
    _write(install / ".mcp.json", "mcpServers", {"repowise": {"command": "repowise", "args": ["mcp"]}})
    manifest = tmp_path / "installed_plugins.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    target_mod.PLUGIN_KEY: [
                        {"scope": "user", "version": "0", "installPath": str(install)}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(target_mod, "plugin_manifest_path", lambda: manifest)
    monkeypatch.setattr(target_mod, "plugin_version_skew", lambda: [])
    repo = tmp_path / "repo"
    repo.mkdir()
    return target_mod, repo


def test_the_plugin_server_is_read_from_the_plugin_install(tmp_path: Path, monkeypatch) -> None:
    """Named as Claude Code names it, and removed with the host's own command."""
    target_mod, repo = _install_plugin(tmp_path, monkeypatch)
    _write(repo / ".mcp.json", "mcpServers", {"repowise": LOCAL})

    report = diagnose(target_mod.TARGET, repo_path=repo)

    assert list(loaded_servers(target_mod.TARGET, repo)) == ["repowise", "plugin:repowise:repowise"]
    issue = next(i for i in report.issues if "registered twice" in i)
    assert "remove `plugin:repowise:repowise` (run `/plugin uninstall repowise@repowise`)" in issue
    assert report.fix_command == target_mod.PLUGIN_UNINSTALL_COMMAND


def test_no_fix_command_when_it_would_leave_a_dropped_copy(tmp_path: Path, monkeypatch) -> None:
    """Uninstalling the plugin alone leaves the hand-added name loaded."""
    target_mod, repo = _install_plugin(tmp_path, monkeypatch)
    _write(repo / ".mcp.json", "mcpServers", {"repowise": LOCAL, "repowise-local": LOCAL})

    report = diagnose(target_mod.TARGET, repo_path=repo)

    issue = next(i for i in report.issues if "registered 3 times" in i)
    assert "Keep `repowise`" in issue
    assert report.fix_command is None


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"command": "repowise", "args": ["mcp"]}, True),
        ({"command": "C:/venv/Scripts/repowise.exe", "args": ["mcp", "/repo"]}, True),
        ({"command": ["repowise", "mcp", "--transport", "stdio"]}, True),
        ({"command": r"C:\venv\Scripts\repowise.exe", "args": ["mcp"]}, True),
        ({"command": "repowise", "args": ["serve"]}, False),
        ({"command": "uvx", "args": ["mcp"]}, False),
        ({"url": "https://api.example.test/mcp"}, False),
    ],
)
def test_launches_repowise(entry: dict, expected: bool) -> None:
    assert launches_repowise(entry) is expected


# ---------------------------------------------------------------------------
# The MCP surface each wired host is served
# ---------------------------------------------------------------------------


def _surface(target_id: str, repo: Path, servers: dict) -> dict | None:
    from repowise.cli.commands.doctor_cmd._checks.mcp_surface import host_surface

    target, maps = _maps(target_id, repo)
    _write(maps[-1].config_path, SERVER_KEY[target_id], servers)
    return host_surface(target_id, loaded_servers(target, repo), repo)


def test_surface_keys_are_stable_and_follow_the_host_default(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    claude = _surface("claude-code", repo, {"repowise": LOCAL})
    codex = _surface("codex", repo, {"repowise": LOCAL})

    keys = {"profile", "tool_count", "pinned", "always_load", "duplicate"}
    assert set(claude) == keys and set(codex) == keys
    assert claude["profile"] == "standard" and claude["always_load"] is False
    # A host that loads every schema up front gets core, and always-load means nothing there.
    assert codex["profile"] == "core" and codex["always_load"] is None
    assert codex["tool_count"] is None or codex["tool_count"] < claude["tool_count"]
    assert claude["duplicate"] == [] and claude["pinned"] is None


def test_a_tools_pin_in_the_entry_wins_over_the_repo_setting(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    (repo / ".repowise" / "config.yaml").write_text("mcp:\n  tools: all\n", encoding="utf-8")
    pinned = {"command": "repowise", "args": ["mcp", "--tools", "lean"]}

    surface = _surface("cursor", repo, {"repowise": pinned})

    assert surface is not None
    assert surface["pinned"] == "lean" and surface["profile"] == "lean"


def test_an_unwired_host_has_no_surface(tmp_path: Path) -> None:
    from repowise.cli.commands.doctor_cmd._checks.mcp_surface import host_surface

    assert host_surface("windsurf", {}, tmp_path) is None


def test_surface_sentence_reads_like_the_settings(tmp_path: Path) -> None:
    from repowise.cli.commands.doctor_cmd._checks.mcp_surface import surface_line, with_surface

    surface = {
        "profile": "standard",
        "tool_count": 11,
        "pinned": None,
        "always_load": False,
        "duplicate": [],
    }
    assert surface_line(surface) == "MCP surface: standard profile, 11 tools, mcp.always_load off."
    assert with_surface("wired up", surface).startswith("wired up. MCP surface:")
    assert with_surface("wired up", None) == "wired up"


def test_json_rows_carry_data_only_when_they_have_facts() -> None:
    from repowise.cli.commands.doctor_cmd._types import _check

    plain = _check("Agent: kiro", True, "not wired", check_id="agent-kiro")
    wired = _check("Agent: codex", True, "wired up", check_id="agent-codex", data={"host": "codex"})
    assert "data" not in plain.as_json()
    assert wired.as_json()["data"] == {"host": "codex"}
