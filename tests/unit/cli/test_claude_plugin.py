from __future__ import annotations

import json
import re
from pathlib import Path

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

    assert rows("PostToolUse") == [(claude_config._AUGMENT_MATCHER, "repowise-augment")]
    assert rows("SessionStart") == [(claude_config._SESSION_START_MATCHER, "repowise-augment")]

    commands = [
        hook["command"]
        for entries in hooks.values()
        for entry in entries
        for hook in entry["hooks"]
    ]
    assert commands == ["repowise-augment"] * 2


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
        "dead-code",
        "decision",
        "doctor",
        "health",
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
