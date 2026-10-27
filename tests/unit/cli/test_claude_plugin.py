from __future__ import annotations

import json
import os
import re
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

    # Every event the installer registers, and nothing else: a surface that
    # ships only to fresh CLI installs and not to the plugin is the failure
    # this guards, and it is invisible from either side alone.
    assert set(hooks) == {"PostToolUse", "SessionStart", "PostToolUseFailure"}

    commands = [
        hook["command"]
        for entries in hooks.values()
        for entry in entries
        for hook in entry["hooks"]
        if entry.get("matcher") != claude_config._CAPTURE_MATCHER
    ]
    assert commands == [command] * len(hooks)


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
    """A ``repowise-augment`` on PATH that records the payload it was given."""
    script = bin_dir / "repowise-augment"
    script.write_text(f"#!/bin/sh\ncat > '{marker.as_posix()}'\n", encoding="utf-8")
    script.chmod(0o755)


def _run_capture_hook(shell: str, payload: dict, bin_dir: Path) -> None:
    import subprocess

    proc = subprocess.run(
        [shell, "-c", claude_config._CAPTURE_HOOK_COMMAND],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": bin_dir.as_posix() + os.pathsep + os.environ.get("PATH", "")},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""


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


def test_the_capture_hook_starts_nothing_for_a_shell_call_that_is_not_a_commit(
    tmp_path: Path,
) -> None:
    """The shell entry fires on every shell call, so the gate lives in the shell.

    A Python start is what made the shell matcher cost 51% of hook invocations;
    a call that is not a commit must never reach the console script.
    """
    shells = _shells()
    if not shells:
        pytest.skip("no POSIX shell available to execute a hook command")
    marker = tmp_path / "ran.json"
    _fake_augment(tmp_path, marker)

    for shell in shells:
        _run_capture_hook(shell, _shell_payload("npm test"), tmp_path)
        assert not marker.exists(), shell


def test_the_capture_hook_hands_a_commit_to_augment_intact(tmp_path: Path) -> None:
    shells = _shells()
    if not shells:
        pytest.skip("no POSIX shell available to execute a hook command")
    marker = tmp_path / "ran.json"
    _fake_augment(tmp_path, marker)
    payload = _shell_payload('git commit -m "use sqlite, not postgres"')

    for shell in shells:
        _run_capture_hook(shell, payload, tmp_path)
        assert json.loads(marker.read_text(encoding="utf-8")) == payload, shell
        marker.unlink()


def test_the_capture_hook_is_silent_when_the_console_script_is_absent(tmp_path: Path) -> None:
    bash = _posix_bash()
    if bash is None:
        pytest.skip("no POSIX bash available to execute a hook command")
    import shutil
    import subprocess

    # Coreutils stay reachable; only the console script is missing.
    cat_dir = Path(shutil.which("cat", path=os.environ.get("PATH")) or "/usr/bin/cat").parent
    proc = subprocess.run(
        [bash, "-c", claude_config._CAPTURE_HOOK_COMMAND],
        input=json.dumps(_shell_payload("git commit -m x")),
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{cat_dir}"},
    )

    assert proc.returncode == 0, proc.stderr
    assert (proc.stdout, proc.stderr) == ("", "")


def test_claude_plugin_skills_have_metadata() -> None:
    skill_paths = sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))

    assert {path.parent.name for path in skill_paths} == {
        "architectural-decisions",
        "change-review",
        "code-health",
        "codebase-exploration",
        "dead-code-cleanup",
        "pre-modification",
    }

    for path in skill_paths:
        text = path.read_text(encoding="utf-8")
        assert _has_skill_frontmatter(text)
        assert re.search(r"^name: \S+", text, re.MULTILINE)


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
