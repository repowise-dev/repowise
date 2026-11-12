"""Hook commands run the script of the install that wrote them, not whatever PATH finds."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from repowise.cli import mcp_config, self_heal
from repowise.cli.editor_integrations import claude_config
from repowise.cli.editor_setup import unresolved_hook_scripts

_SUFFIX = ".exe" if sys.platform == "win32" else ""


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A fake install: an interpreter with the repowise scripts beside it."""
    bindir = tmp_path / "env" / "bin"
    bindir.mkdir(parents=True)
    for script in ("repowise-augment", "repowise-rewrite"):
        (bindir / f"{script}{_SUFFIX}").write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    monkeypatch.setattr(mcp_config, "_looks_transient", lambda _p: False)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    return bindir


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "home" / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr(claude_config, "_claude_code_settings_path", lambda: path)
    return path


def _script(bindir: Path, name: str) -> str:
    return str((bindir / f"{name}{_SUFFIX}").resolve()).replace("\\", "/")


def _commands(settings_path: Path) -> list[str]:
    hooks = json.loads(settings_path.read_text(encoding="utf-8"))["hooks"]
    return [h["command"] for entries in hooks.values() for e in entries for h in e["hooks"]]


def _write_settings(settings_path: Path, command: str) -> None:
    entry = {"matcher": claude_config._AUGMENT_MATCHER, "hooks": [
        {"type": "command", "command": command, "timeout": 10,
         "statusMessage": "Checking codebase context..."}]}
    settings_path.write_text(json.dumps({"hooks": {"PostToolUse": [entry]}}), encoding="utf-8")


def test_the_command_pins_the_running_install_and_keeps_path_as_fallback(install) -> None:
    command = claude_config._augment_hook_command()
    pinned = _script(install, "repowise-augment")
    assert command == (
        f"if [ -x '{pinned}' ]; then exec '{pinned}'; "
        "elif command -v repowise-augment >/dev/null 2>&1; then exec repowise-augment; fi"
    )
    assert mcp_config.hook_pinned_paths(command) == [pinned]


def test_without_the_script_beside_the_interpreter_it_is_the_path_lookup(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    assert claude_config._augment_hook_command() == claude_config._AUGMENT_HOOK_COMMAND
    assert claude_config._capture_hook_command() == claude_config._CAPTURE_HOOK_COMMAND


def test_a_direct_command_falls_back_to_the_bare_name_for_a_path_with_a_space(
    tmp_path, monkeypatch
) -> None:
    bindir = tmp_path / "my env" / "bin"
    bindir.mkdir(parents=True)
    (bindir / f"repowise-rewrite{_SUFFIX}").write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    monkeypatch.setattr(mcp_config, "_looks_transient", lambda _p: False)
    assert mcp_config.hook_direct_command("repowise-rewrite", "--agent codex") == (
        "repowise-rewrite --agent codex"
    )


def test_the_self_heal_pins_a_hook_that_relied_on_path(install, settings_path) -> None:
    _write_settings(settings_path, claude_config._AUGMENT_HOOK_COMMAND)
    assert unresolved_hook_scripts() == [("repowise-augment", claude_config.CLAUDE_SETTINGS_LABEL)]

    assert claude_config.migrate_claude_code_hooks()

    assert claude_config._augment_hook_command() in _commands(settings_path)
    assert unresolved_hook_scripts() == []


def test_the_self_heal_leaves_a_live_pin_to_another_install(install, settings_path, tmp_path) -> None:
    other = tmp_path / "other" / f"repowise-augment{_SUFFIX}"
    other.parent.mkdir()
    other.write_text("", encoding="utf-8")
    elsewhere = claude_config._augment_hook_command().replace(
        _script(install, "repowise-augment"), other.as_posix()
    )
    _write_settings(settings_path, elsewhere)
    claude_config.migrate_claude_code_hooks()
    assert elsewhere in _commands(settings_path)

    other.unlink()  # that install is gone: the hook is repointed at this one
    claude_config.migrate_claude_code_hooks()
    assert claude_config._augment_hook_command() in _commands(settings_path)


def test_install_repins_to_the_install_running_it(install, settings_path) -> None:
    gone = claude_config._augment_hook_command().replace(
        _script(install, "repowise-augment"), "/nowhere/repowise-augment"
    )
    _write_settings(settings_path, gone)
    claude_config.install_claude_code_hooks()
    claude_config.install_claude_code_rewrite_hook()
    commands = _commands(settings_path)
    assert gone not in commands
    assert claude_config._augment_hook_command() in commands
    assert claude_config._capture_hook_command() in commands
    assert claude_config._rewrite_hook_command() in commands


#: The shell gate releases before this one wrote: ``git commit`` alone.
_COMMIT_ONLY_GATE = 'input=$(cat); case "$input" in *"git commit"*) '
_PATH_RUN = (
    'command -v repowise-augment >/dev/null 2>&1; then printf "%s" "$input" | repowise-augment'
)


def _commit_only_capture(pinned: str | None = None) -> str:
    """The capture command exactly as a commit-only release wrote it."""
    if pinned is None:
        run = f"if {_PATH_RUN}; fi"
    else:
        run = f"if [ -x '{pinned}' ]; then printf \"%s\" \"$input\" | '{pinned}'; elif {_PATH_RUN}; fi"
    return f"{_COMMIT_ONLY_GATE}{run};; esac"


def _write_capture_settings(settings_path: Path, command: str, **extra) -> None:
    """Settings with the augment entry and a capture entry running *command*."""
    capture = claude_config._capture_entry()
    capture["hooks"][0]["command"] = command
    augment = {
        "matcher": claude_config._AUGMENT_MATCHER,
        "hooks": [{"type": "command", "command": claude_config._augment_hook_command()}],
    }
    settings = {"hooks": {"PostToolUse": [augment, capture]}, **extra}
    settings_path.write_text(json.dumps(settings), encoding="utf-8")


def _capture_commands(settings_path: Path) -> list[str]:
    entries = json.loads(settings_path.read_text(encoding="utf-8"))["hooks"]["PostToolUse"]
    return [h["command"] for e in entries if claude_config._is_capture_entry(e) for h in e["hooks"]]


def _elsewhere(tmp_path: Path) -> str:
    """A live ``repowise-augment`` belonging to another install on this machine."""
    other = tmp_path / "other" / f"repowise-augment{_SUFFIX}"
    other.parent.mkdir()
    other.write_text("", encoding="utf-8")
    return other.as_posix()


def test_the_self_heal_widens_a_commit_only_gate_with_nothing_to_pin(
    settings_path, tmp_path, monkeypatch
) -> None:
    """No script beside the interpreter: the PATH form is both old and new pin."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    _write_capture_settings(settings_path, _commit_only_capture())

    assert claude_config.migrate_claude_code_hooks()

    assert _capture_commands(settings_path) == [claude_config._CAPTURE_HOOK_COMMAND]
    assert not claude_config.migrate_claude_code_hooks()  # settled


def test_the_self_heal_widens_a_commit_only_gate_and_keeps_a_live_pin(
    install, settings_path, tmp_path
) -> None:
    """Two installs must not take turns rewriting the file, so the pin stays."""
    other = _elsewhere(tmp_path)
    _write_capture_settings(settings_path, _commit_only_capture(other))

    assert claude_config.migrate_claude_code_hooks()

    widened = claude_config._capture_hook_command().replace(
        _script(install, "repowise-augment"), other
    )
    assert _capture_commands(settings_path) == [widened]
    assert not claude_config.migrate_claude_code_hooks()


def test_the_self_heal_widens_and_pins_a_commit_only_gate_on_path(install, settings_path) -> None:
    _write_capture_settings(settings_path, _commit_only_capture())

    assert claude_config.migrate_claude_code_hooks()

    assert _capture_commands(settings_path) == [claude_config._capture_hook_command()]


def test_install_widens_a_commit_only_gate_with_nothing_to_pin(
    settings_path, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    _write_capture_settings(settings_path, _commit_only_capture())

    claude_config.install_claude_code_hooks()

    assert _capture_commands(settings_path) == [claude_config._CAPTURE_HOOK_COMMAND]


def test_install_widens_a_commit_only_gate_and_repins_it_here(
    install, settings_path, tmp_path
) -> None:
    """``init`` keeps its policy: the hook runs the install that ran it."""
    _write_capture_settings(settings_path, _commit_only_capture(_elsewhere(tmp_path)))

    claude_config.install_claude_code_hooks()

    assert _capture_commands(settings_path) == [claude_config._capture_hook_command()]


def test_widening_the_gate_leaves_everything_else_alone(install, settings_path) -> None:
    from repowise.core.owned_paths import COVERAGE_HOOK_COMMAND

    # A user's own gate on the same words runs their tool, not ours.
    theirs = f"{_COMMIT_ONLY_GATE}my-tool --after-commit;; esac"
    _write_capture_settings(
        settings_path,
        _commit_only_capture(),
        permissions={"allow": ["Bash(git status:*)"]},
    )
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["hooks"]["PostToolUse"] += [
        {"matcher": "Bash|PowerShell", "hooks": [{"type": "command", "command": theirs}]},
        claude_config._coverage_entry(),
    ]
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    claude_config.migrate_claude_code_hooks()

    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert saved["permissions"] == {"allow": ["Bash(git status:*)"]}
    commands = _commands(settings_path)
    assert theirs in commands
    assert COVERAGE_HOOK_COMMAND in commands
    assert _capture_commands(settings_path) == [claude_config._capture_hook_command()]


def test_a_plugin_with_the_commit_only_gate_still_stands_in_for_ours(
    install, settings_path
) -> None:
    """The CLI never rewrites plugin files; the plugin's own update widens its gate.

    Until then it still carries the shell entry, so settings must not gain a
    second one that would start the shell twice on every shell call.
    """
    capture = claude_config._capture_entry()
    capture["hooks"][0]["command"] = _commit_only_capture()
    _enable_plugin(settings_path, {"PostToolUse": [capture]})
    plugin_file = next(settings_path.parent.glob("plugins/cache/repowise/*/hooks/hooks.json"))
    shipped = plugin_file.read_text(encoding="utf-8")

    claude_config.install_claude_code_hooks()

    assert _capture_commands(settings_path) == []
    assert plugin_file.read_text(encoding="utf-8") == shipped


def test_a_hook_whose_script_is_nowhere_is_reported(install, settings_path) -> None:
    from repowise.cli.commands.doctor_cmd._checks.integrations import _hook_scripts_row

    _write_settings(settings_path, claude_config._AUGMENT_HOOK_COMMAND)
    row = _hook_scripts_row()
    assert row.level == "warn"
    assert "repowise-augment" in row.detail
    assert row.fix == "repowise agents refresh"

    claude_config.install_claude_code_hooks()
    assert _hook_scripts_row().level == "ok"


def test_init_says_when_a_hook_cannot_find_its_script(install, settings_path, monkeypatch) -> None:
    from unittest.mock import MagicMock

    from repowise.cli.commands.init_cmd._interactive import warn_unresolved_hooks

    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)
    _write_settings(settings_path, claude_config._AUGMENT_HOOK_COMMAND)
    console = MagicMock()
    warn_unresolved_hooks(console)
    assert "repowise-augment" in str(console.print.call_args)

    quiet = MagicMock()
    warn_unresolved_hooks(quiet, no_editor_setup=True)
    quiet.print.assert_not_called()


def test_the_self_heal_stamp_names_the_running_install(install) -> None:
    assert install.as_posix() in self_heal._expected_stamp()


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX script stands in for the console script")
def test_the_pinned_command_runs_the_script_in_a_shell(tmp_path, monkeypatch) -> None:
    bindir = tmp_path / "env" / "bin"
    bindir.mkdir(parents=True)
    script = bindir / "repowise-augment"
    script.write_text("#!/bin/sh\necho ran\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    monkeypatch.setattr(mcp_config, "_looks_transient", lambda _p: False)

    env = {**os.environ, "PATH": "/usr/bin:/bin"}  # the script is not on PATH
    out = subprocess.run(
        ["sh", "-c", claude_config._augment_hook_command()],
        capture_output=True, text=True, env=env, check=True,
    )
    assert out.stdout.strip() == "ran"


@pytest.mark.parametrize(
    ("command", "repin", "moves"),
    [
        ("repowise-rewrite --agent codex", False, True),  # bare: pin it
        ("/gone/bin/repowise-rewrite --agent codex", False, True),  # dead pin
        ("repowise-rewrite --agent codex --verbose", True, False),  # user's args
        ("my-wrapper repowise-rewrite --agent codex", True, False),  # not ours
    ],
)
def test_a_direct_command_moves_only_when_it_is_ours_and_stale(
    install, command, repin, moves
) -> None:
    want = mcp_config.refreshed_direct_command(
        command, "repowise-rewrite", "--agent codex", repin=repin
    )
    assert (want is not None) is moves
    if moves:
        assert want == f"{_script(install, 'repowise-rewrite')} --agent codex"


def test_the_codex_self_heal_repoints_a_dead_pin(install, tmp_path, monkeypatch) -> None:
    from repowise.cli.editor_integrations import codex_config

    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    monkeypatch.setattr(codex_config, "_codex_hooks_path", lambda: hooks_path)
    entry = codex_config._rewrite_hook_entry()
    entry["hooks"][0]["command"] = "/gone/bin/repowise-rewrite --agent codex"
    hooks_path.write_text(json.dumps({"hooks": {"PreToolUse": [entry]}}), encoding="utf-8")

    assert codex_config.migrate_codex_rewrite_hook()
    written = json.loads(hooks_path.read_text(encoding="utf-8"))
    command = written["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command == f"{_script(install, 'repowise-rewrite')} --agent codex"
    assert not codex_config.migrate_codex_rewrite_hook()  # settled


def test_a_cursor_reinstall_repoints_the_hook(install, tmp_path, monkeypatch) -> None:
    from repowise.cli.agent_adapters import cursor

    path = tmp_path / ".cursor" / "hooks.json"
    path.parent.mkdir()
    monkeypatch.setattr(cursor, "user_hooks_path", lambda: path)
    old = {"command": "repowise-rewrite --agent cursor", "matcher": "Shell", "timeout": 5}
    path.write_text(json.dumps({"version": 1, "hooks": {"preToolUse": [old]}}), encoding="utf-8")

    assert cursor.install_cursor_rewrite_hook() == path
    entries = json.loads(path.read_text(encoding="utf-8"))["hooks"]["preToolUse"]
    assert [e["command"] for e in entries] == [
        f"{_script(install, 'repowise-rewrite')} --agent cursor"
    ]


def test_cursor_capture_install_pins_and_replaces_the_legacy_entry(
    install, tmp_path, monkeypatch
) -> None:
    from repowise.cli.agent_adapters import cursor

    path = tmp_path / ".cursor" / "hooks.json"
    path.parent.mkdir()
    monkeypatch.setattr(cursor, "user_hooks_path", lambda: path)
    pinned = f"{_script(install, 'repowise-augment')} --client cursor"

    assert cursor.install_cursor_capture_hook() == path
    entries = json.loads(path.read_text(encoding="utf-8"))["hooks"]["afterFileEdit"]
    assert entries == [{"command": pinned, "timeout": 5}]
    assert cursor.cursor_capture_hook_installed()

    legacy = {"command": "repowise augment --client cursor", "timeout": 5}
    foreign = {"command": "./hooks/format.sh"}
    path.write_text(
        json.dumps({"version": 1, "hooks": {"afterFileEdit": [foreign, legacy]}}),
        encoding="utf-8",
    )
    assert cursor.cursor_capture_hook_installed()
    assert cursor.install_cursor_capture_hook() == path
    entries = json.loads(path.read_text(encoding="utf-8"))["hooks"]["afterFileEdit"]
    assert entries == [foreign, {"command": pinned, "timeout": 5}]

    stale = {"command": "/gone/bin/repowise-augment --client cursor", "timeout": 5}
    path.write_text(
        json.dumps({"version": 1, "hooks": {"afterFileEdit": [foreign, stale]}}),
        encoding="utf-8",
    )
    assert cursor.install_cursor_capture_hook() == path
    entries = json.loads(path.read_text(encoding="utf-8"))["hooks"]["afterFileEdit"]
    assert entries == [foreign, {"command": pinned, "timeout": 5}]

    assert cursor.uninstall_cursor_capture_hook() is True
    assert json.loads(path.read_text(encoding="utf-8"))["hooks"]["afterFileEdit"] == [foreign]


def _enable_plugin(settings_path: Path, hooks: dict) -> None:
    """An enabled repowise plugin carrying *hooks*, laid out as Claude Code keeps it."""
    install_path = settings_path.parent / "plugins" / "cache" / "repowise" / "9.9.9"
    (install_path / "hooks").mkdir(parents=True)
    (install_path / "hooks" / "hooks.json").write_text(json.dumps({"hooks": hooks}), "utf-8")
    (settings_path.parent / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"repowise@repowise": [{"installPath": str(install_path)}]}}),
        encoding="utf-8",
    )
    settings_path.write_text(
        json.dumps({"enabledPlugins": {"repowise@repowise": True}}), encoding="utf-8"
    )


def _hooks(command: str, event: str = "PostToolUse") -> dict:
    return {event: [{"matcher": "Bash", "hooks": [{"type": "command", "command": command}]}]}


def test_a_plugin_hook_off_path_is_reported_with_the_path_fix(install, settings_path) -> None:
    from repowise.cli.editor_setup import unresolved_hooks_fix

    _enable_plugin(settings_path, _hooks(claude_config._AUGMENT_HOOK_COMMAND))
    missing = unresolved_hook_scripts()
    assert missing == [("repowise-augment", "the repowise Claude Code plugin")]
    # The plugin keeps the bare name, so the fix is PATH, not a repin.
    assert unresolved_hooks_fix(missing) == (None, f"Add {install.resolve().as_posix()} to PATH.")


def test_repo_local_hooks_off_path_are_reported(install, settings_path, tmp_path) -> None:
    from repowise.core.owned_paths import (
        CODEX_HOOK_COMMAND,
        COVERAGE_HOOK_COMMAND,
        HEALTH_GATE_HOOK_COMMAND,
    )

    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".codex").mkdir()
    local = {**_hooks(COVERAGE_HOOK_COMMAND), **_hooks(HEALTH_GATE_HOOK_COMMAND, "PreToolUse")}
    (repo / ".claude" / "settings.local.json").write_text(json.dumps({"hooks": local}), "utf-8")
    (repo / ".codex" / "hooks.json").write_text(
        json.dumps({"hooks": _hooks(CODEX_HOOK_COMMAND)}), "utf-8"
    )
    # A user's own hook in the same file is never ours to report.
    mine = _hooks("my-tool --check")
    (repo / ".claude" / "settings.local.json").write_text(
        json.dumps({"hooks": {**local, "Stop": mine["PostToolUse"]}}), "utf-8"
    )

    assert unresolved_hook_scripts(repo) == [
        ("repowise-augment", "repo/.claude/settings.local.json"),
        ("repowise-augment", "repo/.codex/hooks.json"),
    ]
    assert unresolved_hook_scripts() == []  # no repo, no repo-local files


def test_repo_local_hooks_resolve_when_the_script_is_on_path(
    install, settings_path, tmp_path, monkeypatch
) -> None:
    from repowise.core.owned_paths import CODEX_HOOK_COMMAND

    repo = tmp_path / "repo"
    (repo / ".codex").mkdir(parents=True)
    (repo / ".codex" / "hooks.json").write_text(
        json.dumps({"hooks": _hooks(CODEX_HOOK_COMMAND)}), "utf-8"
    )
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    assert unresolved_hook_scripts(repo) == []


def test_doctor_and_init_name_each_place_and_the_fix(install, settings_path, tmp_path, monkeypatch) -> None:
    from unittest.mock import MagicMock

    from repowise.cli.commands.doctor_cmd._checks.integrations import _hook_scripts_row
    from repowise.cli.commands.init_cmd._interactive import warn_unresolved_hooks

    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)
    _enable_plugin(settings_path, _hooks(claude_config._AUGMENT_HOOK_COMMAND))
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["hooks"] = _hooks(claude_config._AUGMENT_HOOK_COMMAND)
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    row = _hook_scripts_row(tmp_path)
    assert row.level == "warn"
    assert "the repowise Claude Code plugin" in row.detail
    assert "~/.claude/settings.json" in row.detail
    # fix is one runnable command (the Setup page copies it); PATH goes in detail.
    assert row.fix == "repowise agents refresh"
    assert row.detail.endswith(f"Add {install.resolve().as_posix()} to PATH.")

    console = MagicMock()
    warn_unresolved_hooks(console, [tmp_path])
    printed = str(console.print.call_args)
    assert "the repowise Claude Code plugin" in printed and "to PATH" in printed


def test_without_scripts_here_the_fix_is_a_reinstall(tmp_path, monkeypatch) -> None:
    from repowise.cli.editor_setup import unresolved_hooks_fix

    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    missing = [("repowise-augment", claude_config.CLAUDE_SETTINGS_LABEL)]
    assert unresolved_hooks_fix(missing) == (
        None,
        "Reinstall repowise so repowise-augment is on PATH.",
    )


def test_init_never_fails_over_the_hook_check(monkeypatch) -> None:
    from unittest.mock import MagicMock

    from repowise.cli import editor_setup
    from repowise.cli.commands.init_cmd._interactive import warn_unresolved_hooks

    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)

    def boom(_repo=None):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(editor_setup, "unresolved_hook_scripts", boom)
    console = MagicMock()
    warn_unresolved_hooks(console, [Path("repo")])
    console.print.assert_not_called()
