from __future__ import annotations

import json
import re
from pathlib import Path

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
        "Bash",
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


def test_codex_plugin_skills_have_metadata_and_neutral_wording() -> None:
    skill_paths = sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))

    assert {path.parent.name for path in skill_paths} == {
        "architectural-decisions",
        "change-review",
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
