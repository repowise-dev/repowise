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
    assert claude_config.claude_code_unresolved_hooks() == ["repowise-augment"]

    assert claude_config.migrate_claude_code_hooks()

    assert claude_config._augment_hook_command() in _commands(settings_path)
    assert claude_config.claude_code_unresolved_hooks() == []


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
