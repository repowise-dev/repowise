"""``repowise agents``, driven end to end through ``CliRunner``.

Every subcommand is exercised in **both** renderings. That is the point of the
file rather than a nicety: the payload and the table are built from one dict, so
a key that no renderer prints and a key a renderer needs but the payload dropped
are both invisible to a test that checks only one side. Running both through the
real command is what catches them.

The home directory is redirected for every test here. Detection reads
``~/.claude`` and ``~/.codex``, and a test that skipped the redirect would pass
or fail depending on whose machine ran it, and worse, the write paths would
reach the real global config.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli


@pytest.fixture
def repo(tmp_path_factory, monkeypatch) -> Path:
    """A git-less repo plus a redirected home, with the machine pinned out."""
    home = tmp_path_factory.mktemp("agents_home")
    repo_path = tmp_path_factory.mktemp("agents_repo")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOMEDRIVE", home.drive or "")
    monkeypatch.setenv("HOMEPATH", str(home)[len(home.drive) :])
    monkeypatch.setattr(Path, "home", lambda: home)
    # A benchmark or CI runner that exported this would silently turn the
    # user-scope assertions below into no-ops.
    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)

    (repo_path / ".repowise").mkdir()
    # The bare listing takes no path argument (a group with an optional
    # positional cannot tell one from a subcommand name), so it reads the cwd.
    monkeypatch.chdir(repo_path)
    return repo_path


def _run(args: list[str]) -> object:
    result = CliRunner().invoke(cli, args, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result


def _json(args: list[str]) -> dict:
    result = _run([*args, "--format", "json"])
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# list (the bare command)
# ---------------------------------------------------------------------------


def test_bare_agents_lists_every_registered_target_as_a_table(repo: Path) -> None:
    output = _run(["agents"]).output
    assert "claude-code" in output
    assert "codex" in output
    assert "vscode" in output


def test_bare_agents_lists_every_registered_target_as_json(repo: Path) -> None:
    payload = _json(["agents"])
    assert [row["id"] for row in payload["agents"]] == ["claude-code", "codex", "vscode"]
    for row in payload["agents"]:
        # The keys the table renders, so a dropped one is a broken table.
        assert set(row) >= {"id", "tier", "present", "registrations", "method"}
    assert payload["agents"][0]["tier"] == "full"
    assert payload["agents"][2]["tier"] == "good"


def test_list_reports_registrations_as_a_list_not_a_boolean(repo: Path) -> None:
    """'Configured' cannot express 'configured twice', which is the whole point."""
    _run(["agents", "add", str(repo), "--target", "vscode", "-y"])
    payload = _json(["agents"])
    vscode = next(row for row in payload["agents"] if row["id"] == "vscode")
    assert len(vscode["registrations"]) == 1
    assert vscode["registrations"][0]["scope"] == "project"


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


def test_add_writes_the_named_target_and_reports_it_as_a_table(repo: Path) -> None:
    output = _run(["agents", "add", str(repo), "--target", "vscode", "-y"]).output
    assert "created" in output
    assert (repo / ".vscode" / "mcp.json").exists()


def test_add_reports_every_file_and_action_as_json(repo: Path) -> None:
    payload = _json(["agents", "add", str(repo), "--target", "vscode", "-y"])

    assert payload["action"] == "add"
    assert payload["changed"] is True
    agent = payload["agents"][0]
    assert agent["method"] == "direct"
    actions = {f["action"] for f in agent["writes"]["project"]["files"]}
    assert actions == {"created"}


def test_add_is_idempotent_and_says_so(repo: Path) -> None:
    """A re-run must report unchanged rather than an update it did not make."""
    _run(["agents", "add", str(repo), "--target", "vscode", "-y"])
    payload = _json(["agents", "add", str(repo), "--target", "vscode", "-y"])

    assert payload["changed"] is False
    actions = {f["action"] for f in payload["agents"][0]["writes"]["project"]["files"]}
    assert actions == {"unchanged"}


def test_add_stands_down_when_the_host_plugin_already_provides_it(
    repo: Path, monkeypatch
) -> None:
    """The duplicate-registration fix, from the command's side.

    A machine with the Claude Code plugin installed already has the MCP server
    and the augment hooks. Writing them again costs a second process spawn per
    matched tool call and a second copy of every tool schema, for no benefit.
    """
    from repowise.cli.agent_targets.targets import claude_code as target_mod

    manifest = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {target_mod.PLUGIN_KEY: [{"scope": "user", "version": "0.41.0"}]},
            }
        ),
        encoding="utf-8",
    )

    payload = _json(["agents", "add", str(repo), "--target", "claude-code", "-y"])

    agent = payload["agents"][0]
    assert agent["writes"] == {}
    assert "host-managed" in agent["skipped"]
    assert not (repo / ".mcp.json").exists()


def test_add_honours_the_skip_env_var_for_user_scope(repo: Path, monkeypatch) -> None:
    """The flag means what it says: never touch the developer's global config."""
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")

    payload = _json(["agents", "add", str(repo), "--target", "claude-code", "-y"])

    agent = payload["agents"][0]
    assert "user" not in agent["writes"]
    assert "project" in agent["writes"]
    assert not (Path.home() / ".claude" / "settings.json").exists()


def test_add_rejects_an_unknown_target_by_name(repo: Path) -> None:
    """A typo must not resolve to nothing and report success."""
    result = CliRunner().invoke(cli, ["agents", "add", str(repo), "--target", "cursr", "-y"])
    assert result.exit_code != 0
    assert "cursr" in result.output
    assert "claude-code" in result.output


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_takes_the_entry_out_and_reports_it_both_ways(repo: Path) -> None:
    _run(["agents", "add", str(repo), "--target", "vscode", "-y"])

    payload = _json(["agents", "remove", str(repo), "--target", "vscode"])
    assert payload["action"] == "remove"
    assert payload["agents"][0]["writes"]["project"]["files"][0]["action"] == "removed"

    config = json.loads((repo / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    assert "repowise" not in config["servers"]

    output = _run(["agents", "remove", str(repo), "--target", "vscode"]).output
    assert "not-found" in output


def test_remove_requires_an_explicit_target(repo: Path) -> None:
    """'Remove whatever you detect' is a bad default for a destructive verb."""
    result = CliRunner().invoke(cli, ["agents", "remove", str(repo)])
    assert result.exit_code != 0
    assert "--target" in result.output


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


def test_refresh_rewrites_what_is_wired_and_adds_nothing(repo: Path) -> None:
    _run(["agents", "add", str(repo), "--target", "vscode", "-y"])

    payload = _json(["agents", "refresh", str(repo)])

    assert [agent["id"] for agent in payload["agents"]] == ["vscode"]
    assert not (repo / ".codex").exists()


def test_refresh_repoints_a_stale_entry(repo: Path) -> None:
    """The reason refresh exists: a config that exists but points elsewhere."""
    _run(["agents", "add", str(repo), "--target", "vscode", "-y"])
    config_path = repo / ".vscode" / "mcp.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["servers"]["repowise"]["args"] = ["mcp", "/gone", "--transport", "stdio"]
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    payload = _json(["agents", "refresh", str(repo)])

    assert payload["changed"] is True
    refreshed = json.loads(config_path.read_text(encoding="utf-8"))
    assert "/gone" not in refreshed["servers"]["repowise"]["args"]


def test_refresh_on_a_clean_repo_says_nothing_is_wired(repo: Path) -> None:
    output = _run(["agents", "refresh", str(repo)]).output
    assert "No agent is wired up yet" in output
    assert _json(["agents", "refresh", str(repo)])["agents"] == []


# ---------------------------------------------------------------------------
# print-config
# ---------------------------------------------------------------------------


def test_print_config_emits_a_bare_snippet_and_writes_nothing(repo: Path) -> None:
    output = _run(["agents", "print-config", "vscode", str(repo)]).output
    assert json.loads(output)["servers"]["repowise"]["type"] == "stdio"
    assert not (repo / ".vscode").exists()


def test_print_config_json_wraps_the_snippet_with_its_context(repo: Path) -> None:
    payload = _json(["agents", "print-config", "codex", str(repo)])
    assert payload["target"] == "codex"
    assert payload["scope"] == "project"
    assert "[mcp_servers.repowise]" in payload["config"]
    assert not (repo / ".codex").exists()


def test_print_config_names_the_known_ids_for_an_unknown_one(repo: Path) -> None:
    result = CliRunner().invoke(cli, ["agents", "print-config", "zed", str(repo)])
    assert result.exit_code != 0
    assert "vscode" in result.output


# ---------------------------------------------------------------------------
# doctor integration
# ---------------------------------------------------------------------------


def test_doctor_reports_one_row_per_agent_from_its_own_descriptor(repo: Path) -> None:
    """Nothing in doctor knows what any particular agent's health means."""
    from repowise.cli.commands.doctor_cmd.repo_checks import _agent_target_checks

    checks, needs_refresh = _agent_target_checks()

    assert [c.name for c in checks] == [
        "Agent: claude-code",
        "Agent: codex",
        "Agent: vscode",
    ]
    # Nothing is wired on a clean machine, and an agent you do not use is not a
    # problem with your setup.
    assert all(c.ok for c in checks)
    assert needs_refresh is False


def test_doctor_surfaces_a_stale_hook_matcher_rather_than_calling_it_ok(
    repo: Path, monkeypatch
) -> None:
    """The agreed mitigation for gating the self-heal on the skip env var.

    A hook whose matcher names a tool the host has since renamed is installed,
    parses, and will never fire. Someone who exports REPOWISE_SKIP_EDITOR_SETUP
    permanently never gets the migration that would fix it, so this row is the
    only place the staleness becomes visible.
    """
    from repowise.cli.commands.doctor_cmd.repo_checks import _agent_target_checks
    from repowise.cli.editor_integrations import codex_config

    monkeypatch.setattr(codex_config, "codex_rewrite_hook_matcher", lambda: "local_shell")

    checks, needs_refresh = _agent_target_checks()

    codex = next(c for c in checks if c.name == "Agent: codex")
    assert codex.ok is False
    assert "will never fire" in codex.detail
    assert "run:" in codex.detail
    assert needs_refresh is True


def test_doctor_calls_a_damaged_config_broken_not_missing(repo: Path) -> None:
    """'Not installed' would send the user to run an install that refuses too."""
    from repowise.cli.agent_targets.targets import claude_code as target_mod

    settings = Path.home() / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"mcpServers": {,}', encoding="utf-8")

    report = target_mod.TARGET.doctor()

    assert report.status.value == "broken"
    assert "not valid JSON" in report.issues[0]
    assert report.fix_command


def test_doctor_repair_routes_to_the_same_refresh_the_command_runs(repo: Path) -> None:
    """One implementation, so a repair cannot drift from `agents refresh`."""
    from repowise.cli.commands.agents_cmd import refresh_wired_agents

    _run(["agents", "add", str(repo), "--target", "vscode", "-y"])
    config_path = repo / ".vscode" / "mcp.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["servers"]["repowise"]["args"] = ["mcp", "/gone", "--transport", "stdio"]
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    payload = refresh_wired_agents(repo)

    assert payload["changed"] is True
    assert "/gone" not in config_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The interactive gate
# ---------------------------------------------------------------------------


def test_the_gate_is_a_tty_and_no_target_never_a_tty_alone(monkeypatch) -> None:
    """An explicit ``--target`` already answers the question the prompt asks."""
    from repowise.cli.commands import agents_cmd

    monkeypatch.setattr(agents_cmd.sys.stdin, "isatty", lambda: True, raising=False)

    assert agents_cmd._can_prompt(None, yes=False)
    assert not agents_cmd._can_prompt("vscode", yes=False)
    assert not agents_cmd._can_prompt(None, yes=True)


def test_a_tty_that_cannot_answer_falls_through_to_the_defaults(repo: Path, monkeypatch) -> None:
    """isatty lies: Git Bash on Windows, pty wrappers, ``docker run -t``.

    The prompt returning None must leave the pre-ticked set intact rather than
    take the run down with an EOFError.
    """
    from repowise.cli.commands import agents_cmd
    from repowise.cli.ui import agent_selection

    monkeypatch.setattr(agents_cmd.sys.stdin, "isatty", lambda: True, raising=False)

    def _eof(*_args, **_kwargs):
        raise EOFError

    monkeypatch.setattr(agent_selection.Prompt, "ask", _eof)
    (repo / ".vscode").mkdir()  # makes VS Code "present", so it starts ticked

    result = CliRunner().invoke(cli, ["agents", "add", str(repo)], catch_exceptions=False)

    assert result.exit_code == 0
    assert (repo / ".vscode" / "mcp.json").exists()
