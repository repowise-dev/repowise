from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

from repowise.cli.editor_integrations import claude_config

ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = ROOT / "plugins" / "claude-code"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _has_skill_frontmatter(text: str) -> bool:
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---", 4)
    if end == -1:
        return False
    frontmatter = text[4:end]
    return "name:" in frontmatter and "description:" in frontmatter


def _frontmatter(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1]) or {}


#: Claude Code lists every model-invocable skill and command in context on every
#: turn. A person types a command; the agent runs the CLI or calls the MCP tool
#: it wraps, so only setup stays listed for the model.
_MODEL_LISTED_COMMANDS = {"init"}
#: One short line each: the listing is paid per turn, the body only on use.
_SKILL_DESCRIPTION_CHARS = 90


def test_only_setup_commands_are_listed_for_the_model() -> None:
    listed = {
        p.stem
        for p in (PLUGIN_ROOT / "commands").glob("*.md")
        if not _frontmatter(p).get("disable-model-invocation")
    }
    assert listed == _MODEL_LISTED_COMMANDS


def test_skill_descriptions_are_one_short_line() -> None:
    skills = sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))
    assert skills
    for path in skills:
        description = _frontmatter(path)["description"]
        assert "\n" not in description.strip(), path
        assert len(description) <= _SKILL_DESCRIPTION_CHARS, path


def test_claude_plugin_manifest_paths() -> None:
    manifest_path = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    manifest = _load_json(manifest_path)

    assert manifest["name"] == "repowise"
    assert "version" in manifest
    assert "[TODO" not in manifest_path.read_text(encoding="utf-8")


def test_claude_plugin_lens_module() -> None:
    hooks = _load_json(PLUGIN_ROOT / "hooks" / "hooks.json")

    # The engine takes exactly one module, relative to hooks.json.
    assert hooks["modules"] == ["./lens/lens.js"]
    bundle = PLUGIN_ROOT / "hooks" / "lens" / "lens.js"
    assert bundle.is_file()
    assert re.search(r"export\s*\{\s*register\s*\}", bundle.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("field", "default", "read_as"),
    [
        # On by default: the bundle treats only `false` as off.
        ("lens_margin", True, "!== false"),
        ("lens_squeeze", True, "!== false"),
        ("lens_review", True, "!== false"),
        ("lens_flow", True, "!== false"),
        # Off by default: the map opens unasked only for someone who turned this on.
        ("lens_pane_autoopen", False, "=== true"),
        # Off by default: the map is quiet tiles; health colours are a button away.
        ("lens_map_health", False, "=== true"),
    ],
)
def test_claude_plugin_lens_toggles(field: str, default: bool, read_as: str) -> None:
    config = _load_json(PLUGIN_ROOT / ".claude-plugin" / "plugin.json")["userConfig"]

    assert set(config[field]) == {"type", "title", "description", "default"}
    assert config[field]["type"] == "boolean"
    assert config[field]["default"] is default
    assert "—" not in config[field]["description"]
    bundle = (PLUGIN_ROOT / "hooks" / "lens" / "lens.js").read_text(encoding="utf-8")
    assert f'options["{field}"] {read_as}' in bundle


def test_claude_plugin_lens_toggles_are_all_tested() -> None:
    config = _load_json(PLUGIN_ROOT / ".claude-plugin" / "plugin.json")["userConfig"]

    assert set(config) == {"lens_margin", "lens_squeeze", "lens_review", "lens_flow", "lens_pane_autoopen", "lens_map_health"}


def test_claude_plugin_mcp_uses_repowise() -> None:
    config = _load_json(PLUGIN_ROOT / ".mcp.json")

    assert config["mcpServers"]["repowise"]["command"] == "repowise"
    assert config["mcpServers"]["repowise"]["args"] == ["mcp"]


def test_claude_plugin_hooks_match_installer() -> None:
    hooks = _load_json(PLUGIN_ROOT / "hooks" / "hooks.json")["hooks"]

    def rows(bucket: str) -> list:
        return [
            (entry.get("matcher"), h["command"])
            for entry in hooks.get(bucket, [])
            for h in entry["hooks"]
        ]

    command = claude_config._AUGMENT_HOOK_COMMAND

    assert rows("PostToolUse") == [
        (claude_config._AUGMENT_MATCHER, command),
        (claude_config._CAPTURE_MATCHER, claude_config._CAPTURE_HOOK_COMMAND),
    ]
    assert rows("SessionStart") == [(claude_config._SESSION_START_MATCHER, command)]
    assert rows("PostToolUseFailure") == [(claude_config._FAILURE_MATCHER, command)]
    # The rewrite hook is on by default for a CLI install, so the plugin
    # ships it too, guarded the same way for a plugin installed without the CLI.
    assert rows("PreToolUse") == [
        (claude_config._REWRITE_MATCHER, claude_config._PLUGIN_REWRITE_HOOK_COMMAND)
    ]
    assert claude_config._is_rewrite_hook(hooks["PreToolUse"][0]["hooks"][0])

    # Every event the installer registers, and nothing else: a surface that
    # ships only to fresh CLI installs and not to the plugin is the failure
    # this guards, and it is invisible from either side alone.
    assert set(hooks) == {"PostToolUse", "SessionStart", "PostToolUseFailure", "PreToolUse"}

    commands = [
        hook["command"]
        for event, entries in hooks.items()
        if event != "PreToolUse"
        for entry in entries
        for hook in entry["hooks"]
        if entry.get("matcher") != claude_config._CAPTURE_MATCHER
    ]
    assert commands == [command] * (len(hooks) - 1)


def _posix_bash() -> str | None:
    """Path to a POSIX bash, or None.

    On Windows ``shutil.which("bash")`` finds ``system32\\bash.exe`` first — the
    WSL launcher, which is not the shell Claude Code runs hooks with and does
    not behave like one here. Prefer Git Bash explicitly, which is what a hook
    actually gets on this platform.
    """
    import shutil

    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ):
            if Path(candidate).is_file():
                return candidate
        found = shutil.which("bash")
        return None if found is None or "system32" in found.lower() else found
    return shutil.which("bash")


def test_the_augment_hook_is_silent_when_the_console_script_is_absent(tmp_path: Path) -> None:
    """The plugin installs independently of the CLI, so the script may not exist.

    An unguarded command name makes that state print ``command not found`` on
    every matched tool call — non-blocking noise the user cannot act on and
    cannot escape short of disabling the plugin. Run the real command with a
    PATH that resolves nothing: it must say nothing and exit 0.
    """
    import subprocess

    bash = _posix_bash()
    if bash is None:  # pragma: no cover - hooks need a shell to run at all
        pytest.skip("no POSIX bash available to execute a hook command")

    # An empty PATH would also strip the variables process creation needs on
    # Windows; an empty *directory* is the honest "script isn't installed" state.
    proc = subprocess.run(
        [bash, "-c", claude_config._AUGMENT_HOOK_COMMAND],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": str(tmp_path)},
    )

    assert proc.returncode == 0, f"exited {proc.returncode}: {proc.stderr!r}"
    assert proc.stdout == ""
    assert proc.stderr == ""


def _fake_augment(bin_dir: Path, marker: Path) -> None:
    """A ``repowise-augment`` that records the payload it was given.

    It appends, so a payload handed over twice no longer reads back as one.
    """
    script = bin_dir / "repowise-augment"
    script.write_text(f"#!/bin/sh\ncat >> '{marker.as_posix()}'\n", encoding="utf-8")
    script.chmod(0o755)


def _run_hook(shell: str, hook: str, payload: dict, path: str):
    import subprocess

    proc = subprocess.run(
        [shell, "-c", hook],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": path},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    return proc


def _run_capture_hook(shell: str, payload: dict, bin_dir: Path) -> None:
    path = bin_dir.as_posix() + os.pathsep + os.environ.get("PATH", "")
    _run_hook(shell, claude_config._CAPTURE_HOOK_COMMAND, payload, path)


def _without_the_console_script(empty_dir: Path) -> str:
    """A PATH where coreutils resolve and no ``repowise-augment`` does."""
    import shutil

    cat_dir = Path(shutil.which("cat", path=os.environ.get("PATH")) or "/usr/bin/cat").parent
    return f"{empty_dir}{os.pathsep}{cat_dir}"


def _shells() -> list[str]:
    """Git Bash or bash, plus a plain ``sh`` where it is a different shell (dash)."""
    import shutil

    found = [s for s in (_posix_bash(),) if s]
    sh = shutil.which("sh") if os.name != "nt" else None
    if sh and os.path.realpath(sh) != os.path.realpath(found[0] if found else ""):
        found.append(sh)
    return found


def _shell_payload(command: str) -> dict:
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"stdout": "x" * 40_000, "stderr": ""},
    }


#: The Git operations that can leave the index behind ``HEAD``: the ones the
#: stale-wiki check answers. The capture prompt answers only the commit.
_HEAD_MOVING_COMMANDS = (
    'git commit -m "use sqlite, not postgres"',
    "git pull",
    "git merge feature",
    "git rebase main",
    "git cherry-pick abc123",
)

#: Shell calls that must cost only the shell start.
_OTHER_SHELL_COMMANDS = ("ls", "npm test", "git status")


def test_the_shell_gate_admits_every_operation_the_staleness_check_answers() -> None:
    """Two lists, one shell and one Python, that only work as the same list.

    An operation missing from the gate never reaches the check, which is how
    four of these went silent on Claude Code.
    """
    from repowise.cli.commands.augment_cmd.bash_staleness import _GIT_COMMIT_PATTERNS

    admitted = re.findall(r'\*"([^"]+)"\*', claude_config._CAPTURE_GATE)
    assert sorted(admitted) == sorted(_GIT_COMMIT_PATTERNS)


@pytest.mark.parametrize("command", _OTHER_SHELL_COMMANDS)
def test_the_capture_hook_starts_nothing_for_a_shell_call_that_moves_no_head(
    tmp_path: Path, command: str
) -> None:
    """The shell entry fires on every shell call, so the gate lives in the shell.

    A Python start is what made the shell matcher cost 51% of hook invocations;
    a call that names none of the gated Git operations must never reach the
    console script.
    """
    shells = _shells()
    if not shells:
        pytest.skip("no POSIX shell available to execute a hook command")
    marker = tmp_path / "ran.json"
    _fake_augment(tmp_path, marker)

    for shell in shells:
        _run_capture_hook(shell, _shell_payload(command), tmp_path)
        assert not marker.exists(), shell


@pytest.mark.parametrize("command", _HEAD_MOVING_COMMANDS)
def test_the_capture_hook_hands_each_head_moving_git_command_to_augment_once(
    tmp_path: Path, command: str
) -> None:
    shells = _shells()
    if not shells:
        pytest.skip("no POSIX shell available to execute a hook command")
    marker = tmp_path / "ran.json"
    _fake_augment(tmp_path, marker)
    payload = _shell_payload(command)

    for shell in shells:
        _run_capture_hook(shell, payload, tmp_path)
        assert json.loads(marker.read_text(encoding="utf-8")) == payload, shell
        marker.unlink()


@pytest.mark.parametrize("command", _HEAD_MOVING_COMMANDS)
def test_the_capture_hook_is_silent_when_the_console_script_is_absent(
    tmp_path: Path, command: str
) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("no POSIX bash available to execute a hook command")

    # Coreutils stay reachable; only the console script is missing.
    proc = _run_hook(
        bash,
        claude_config._CAPTURE_HOOK_COMMAND,
        _shell_payload(command),
        _without_the_console_script(tmp_path),
    )

    assert proc.stderr == ""


_POSIX_SCRIPT = pytest.mark.skipif(
    sys.platform == "win32", reason="a POSIX script stands in for the console script"
)


@pytest.fixture
def pinned_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The running install's scripts directory, where the generated command pins."""
    from repowise.cli import mcp_config

    bindir = tmp_path / "env" / "bin"
    bindir.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    monkeypatch.setattr(mcp_config, "_looks_transient", lambda _p: False)
    return bindir


def _pinned_capture_command(bindir: Path, marker: Path) -> str:
    from repowise.cli import mcp_config

    _fake_augment(bindir, marker)
    command = claude_config._capture_hook_command()
    assert mcp_config.hook_pinned_paths(command), "expected the pinned form"
    return command


@_POSIX_SCRIPT
@pytest.mark.parametrize(
    ("command", "runs"),
    [(c, True) for c in _HEAD_MOVING_COMMANDS] + [(c, False) for c in _OTHER_SHELL_COMMANDS],
)
def test_the_pinned_capture_hook_gates_the_same_git_commands(
    pinned_install: Path, tmp_path: Path, command: str, runs: bool
) -> None:
    """``init`` writes the pinned form, so its gate is checked by running it too."""
    shells = _shells()
    if not shells:
        pytest.skip("no POSIX shell available to execute a hook command")
    marker = tmp_path / "ran.json"
    hook = _pinned_capture_command(pinned_install, marker)
    payload = _shell_payload(command)
    empty = tmp_path / "empty"
    empty.mkdir()

    for shell in shells:
        # Off PATH: only the pinned script can answer.
        _run_hook(shell, hook, payload, _without_the_console_script(empty))
        if runs:
            assert json.loads(marker.read_text(encoding="utf-8")) == payload, shell
            marker.unlink()
        else:
            assert not marker.exists(), shell


@_POSIX_SCRIPT
def test_the_pinned_capture_hook_falls_back_to_path_then_stays_silent(
    pinned_install: Path, tmp_path: Path
) -> None:
    shells = _shells()
    if not shells:
        pytest.skip("no POSIX shell available to execute a hook command")
    marker = tmp_path / "ran.json"
    hook = _pinned_capture_command(pinned_install, marker)
    (pinned_install / "repowise-augment").unlink()  # the install that wrote it is gone
    on_path = tmp_path / "on-path"
    on_path.mkdir()
    _fake_augment(on_path, marker)
    payload = _shell_payload("git pull")

    for shell in shells:
        _run_hook(shell, hook, payload, _without_the_console_script(on_path))
        assert json.loads(marker.read_text(encoding="utf-8")) == payload, shell
        marker.unlink()

    (on_path / "repowise-augment").unlink()
    for shell in shells:
        proc = _run_hook(shell, hook, payload, _without_the_console_script(on_path))
        assert proc.stderr == "", shell
    assert not marker.exists()


def test_claude_plugin_skills_have_metadata() -> None:
    skill_paths = sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))

    assert {path.parent.name for path in skill_paths} == {
        "architectural-decisions",
        "change-review",
        "code-health",
        "codebase-exploration",
        "dead-code-cleanup",
        "pre-modification",
        "settings",
    }

    for path in skill_paths:
        text = path.read_text(encoding="utf-8")
        assert _has_skill_frontmatter(text)
        match = re.search(r"^name:\s*(\S+)", text, re.MULTILINE)
        assert match and match.group(1) == path.parent.name


def _has_command_frontmatter(text: str) -> bool:
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---", 4)
    if end == -1:
        return False
    frontmatter = text[4:end]
    return "description:" in frontmatter and "allowed-tools:" in frontmatter


def test_claude_plugin_commands_have_frontmatter() -> None:
    command_paths = sorted((PLUGIN_ROOT / "commands").glob("*.md"))

    assert {path.stem for path in command_paths} == {
        "ask",
        "context",
        "coverage",
        "dead-code",
        "decision",
        "doctor",
        "export",
        "health",
        "impacted-tests",
        "init",
        "reindex",
        "risk",
        "search",
        "security",
        "status",
        "symbol",
        "update",
        "why",
    }

    for path in command_paths:
        text = path.read_text(encoding="utf-8")
        assert _has_command_frontmatter(text), path.name
        assert re.search(r"^description: .+", text, re.MULTILINE)
        assert re.search(r"^allowed-tools: .+", text, re.MULTILINE)


def test_claude_plugin_marketplace_version_sync() -> None:
    manifest = _load_json(PLUGIN_ROOT / ".claude-plugin" / "plugin.json")
    marketplace = _load_json(ROOT / ".claude-plugin" / "marketplace.json")
    entry = marketplace["plugins"][0]

    assert marketplace["name"] == "repowise"
    assert entry["name"] == "repowise"
    assert entry["source"] == "./plugins/claude-code"
    assert entry["version"] == manifest["version"]
