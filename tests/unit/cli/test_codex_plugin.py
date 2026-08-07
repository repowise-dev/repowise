from __future__ import annotations

import json
import re
from pathlib import Path

from repowise.cli.agent_adapters.codex import (
    SHELL_TOOL_MATCHER,
    SHELL_TOOL_NAMES,
    CodexAdapter,
)
from repowise.cli.commands.augment_cmd.command import _SHELL_TOOL_NAMES

ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = ROOT / "plugins" / "codex"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_codex_plugin_manifest_paths() -> None:
    manifest_path = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
    manifest = _load_json(manifest_path)

    assert manifest["name"] == "codex"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["hooks"] == "./hooks/hooks.json"
    assert "apps" not in manifest
    assert "[TODO" not in manifest_path.read_text(encoding="utf-8")


def test_codex_plugin_mcp_uses_repowise_no_path_mode() -> None:
    config = _load_json(PLUGIN_ROOT / ".mcp.json")

    assert config["repowise"]["command"] == "repowise"
    assert config["repowise"]["args"] == ["mcp"]
    assert config["repowise"]["startup_timeout_sec"] == 20


def test_codex_plugin_hooks_match_supported_codex_events() -> None:
    hooks = _load_json(PLUGIN_ROOT / "hooks" / "hooks.json")["hooks"]

    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "PostToolUse"}
    assert hooks["SessionStart"][0]["matcher"] == "startup|resume|clear"
    assert [entry["matcher"] for entry in hooks["PostToolUse"]] == [
        SHELL_TOOL_MATCHER,
        "apply_patch|Edit|Write",
    ]

    commands = [
        hook["command"]
        for entries in hooks.values()
        for entry in entries
        for hook in entry["hooks"]
    ]
    assert commands == ["repowise-augment --client codex"] * 4
    assert [
        hook["timeout"]
        for entries in hooks.values()
        for entry in entries
        for hook in entry["hooks"]
    ] == [30] * 4


def test_codex_shell_matcher_covers_the_names_codex_actually_sends() -> None:
    """The matcher, the dispatch and the rewrite gate agree on one set.

    This is a regression test with a real defect behind it: the matcher was
    the single literal ``"Bash"``, which Codex has not called its shell tool
    in any measured release — 18 rollouts from 0.145 carry ``shell_command``
    and ``exec`` and never ``Bash``. A hook that matches nothing is silent in
    exactly the way a working one is, so nothing caught it. Pinning the three
    sites to one another is what makes the next rename a failing test rather
    than a surface that quietly stops firing.
    """
    for name in ("Bash", "shell_command"):
        assert name in SHELL_TOOL_NAMES
        assert name in SHELL_TOOL_MATCHER.split("|")
        # The augment dispatch routes it to the shell handler...
        assert name in _SHELL_TOOL_NAMES
        # ...and the rewrite hook accepts rather than declines it.
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": name,
                "tool_input": {"command": "pytest -q"},
                "cwd": "/repo",
            }
        )
        assert CodexAdapter().parse_hook_payload(payload) is not None


def test_codex_exec_is_excluded_because_it_carries_no_command() -> None:
    """`exec` looks like a shell tool in the rollouts and is not one.

    It is a `custom_tool_call` whose input is a JavaScript program that calls
    `tools.shell_command(...)` internally — no command string, nothing to
    rewrite. Matching it would buy a hook subprocess per call (423 of them in
    the measured corpus) that can only ever decline.
    """
    assert "exec" not in SHELL_TOOL_NAMES
    assert "exec" not in SHELL_TOOL_MATCHER.split("|")


def test_codex_plugin_skills_have_metadata_and_neutral_wording() -> None:
    skill_paths = sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))

    assert {path.parent.name for path in skill_paths} == {
        "architectural-decisions",
        "change-review",
        "code-health",
        "codebase-exploration",
        "dead-code-cleanup",
        "pre-modification-check",
    }

    for path in skill_paths:
        text = path.read_text(encoding="utf-8")
        assert re.search(r"^---\nname: .+\ndescription: .+\n---", text)
        assert "Claude" not in text
        assert "/repowise:" not in text


def test_codex_plugin_marketplace_entry() -> None:
    marketplace = _load_json(ROOT / ".agents" / "plugins" / "marketplace.json")
    entry = marketplace["plugins"][0]

    assert marketplace["name"] == "repowise"
    assert marketplace["interface"]["displayName"] == "Repowise"
    assert entry["name"] == "codex"
    assert entry["source"] == {"source": "local", "path": "./plugins/codex"}
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert entry["category"] == "Productivity"
