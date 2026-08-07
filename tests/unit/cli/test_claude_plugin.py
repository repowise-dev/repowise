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

    assert rows("PostToolUse") == [(claude_config._AUGMENT_MATCHER, command)]
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
        "coverage",
        "dead-code",
        "decision",
        "doctor",
        "health",
        "impacted-tests",
        "init",
        "reindex",
        "risk",
        "search",
        "status",
        "update",
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
