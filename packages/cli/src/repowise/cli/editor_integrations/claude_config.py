"""Claude Desktop and Claude Code MCP config helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from repowise.cli.mcp_config import (
    generate_mcp_config,
    load_existing_config,
    merge_mcp_entry,
)
from repowise.core.workspace.config import find_workspace_root


def _claude_desktop_config_path() -> Path | None:
    """Return the Claude Desktop config path for this OS, or None if unsupported."""
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    if sys.platform == "win32":
        appdata = Path.home() / "AppData" / "Roaming"
        return appdata / "Claude" / "claude_desktop_config.json"
    # Linux / other: Claude Desktop not officially supported yet
    return None


def _claude_code_settings_path() -> Path:
    """Return the global Claude Code settings path (~/.claude/settings.json)."""
    return Path.home() / ".claude" / "settings.json"


def _resolve_mcp_target(repo_path: Path) -> Path:
    """Pick the right path to register as the MCP server target.

    The Claude Desktop / Claude Code MCP config is global — there is only one
    ``"repowise"`` server key. When the user is operating inside a workspace,
    registering the per-repo path means every ``repowise init`` against a
    sibling repo silently overwrites the entry to point at whichever repo was
    indexed last, breaking workspace mode.

    If ``repo_path`` lives inside a workspace (``.repowise-workspace.yaml`` in
    any ancestor), return the workspace root instead so the MCP server is
    invoked in workspace mode and ``repo="<alias>"`` queries work across all
    repos. Otherwise fall back to the per-repo path, preserving single-repo
    behavior.
    """
    workspace_root = find_workspace_root(repo_path)
    return workspace_root if workspace_root is not None else repo_path


def register_with_claude_desktop(repo_path: Path) -> Path | None:
    """Add repowise MCP server to Claude Desktop's config.

    When ``repo_path`` is inside a workspace, the registration targets the
    workspace root so the MCP server starts in workspace mode.

    Returns the config path if successful, None if Claude Desktop is not
    present or the platform is unsupported.
    """
    config_path = _claude_desktop_config_path()
    if config_path is None:
        return None
    if not config_path.parent.exists():
        # Claude Desktop not installed
        return None
    target = _resolve_mcp_target(repo_path)
    entry = generate_mcp_config(target)["mcpServers"]
    return config_path if merge_mcp_entry(config_path, entry) else None


def register_with_claude_code(repo_path: Path) -> Path | None:
    """Add repowise MCP server to global Claude Code settings (~/.claude/settings.json).

    When ``repo_path`` is inside a workspace, the registration targets the
    workspace root so the MCP server starts in workspace mode and subsequent
    inits against sibling repos do not overwrite the entry.

    Returns the settings path if successful, None on failure.
    """
    settings_path = _claude_code_settings_path()
    target = _resolve_mcp_target(repo_path)
    entry = generate_mcp_config(target)["mcpServers"]
    return settings_path if merge_mcp_entry(settings_path, entry) else None


def install_claude_code_hooks() -> Path | None:
    """Register PostToolUse hooks in ~/.claude/settings.json.

    PostToolUse detects git staleness and can enrich Grep/Glob results when
    the hook output has useful extra context. Existing user hooks are preserved.
    """
    settings_path = _claude_code_settings_path()

    post_hook_entry = {
        "matcher": "Bash|Grep|Glob",
        "hooks": [
            {
                "type": "command",
                "command": "repowise-augment",
                "timeout": 10,
                "statusMessage": "Checking codebase context...",
            }
        ],
    }

    try:
        if settings_path.exists():
            existing = load_existing_config(settings_path)
        else:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}

        hooks = existing.setdefault("hooks", {})

        # Drop any pre-existing repowise PreToolUse entry; the current design
        # routes everything through PostToolUse.
        pre_hooks = hooks.setdefault("PreToolUse", [])
        _strip_repowise_pretool(pre_hooks)
        if not pre_hooks:
            hooks.pop("PreToolUse", None)

        # PostToolUse: migrate legacy command + matcher, then add if missing.
        post_hooks = hooks.setdefault("PostToolUse", [])
        _migrate_legacy_hook(post_hooks)
        if not _has_repowise_hook(post_hooks):
            post_hooks.append(post_hook_entry)

        settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        return settings_path
    except OSError:
        return None


def _has_repowise_hook(hook_list: list) -> bool:
    """Check if a repowise hook is already registered, current or legacy."""
    for entry in hook_list:
        for hook in entry.get("hooks", []):
            cmd = hook.get("command", "")
            if "repowise-augment" in cmd or "repowise augment" in cmd:
                return True
    return False


def _is_repowise_hook(hook: dict) -> bool:
    cmd = hook.get("command", "")
    return "repowise-augment" in cmd or "repowise augment" in cmd


def _strip_repowise_pretool(hook_list: list) -> bool:
    """Remove repowise's PreToolUse entry from a hook bucket in place."""
    changed = False
    for entry in list(hook_list):
        kept = [h for h in entry.get("hooks", []) if not _is_repowise_hook(h)]
        if len(kept) != len(entry.get("hooks", [])):
            changed = True
            if kept:
                entry["hooks"] = kept
            else:
                hook_list.remove(entry)
    return changed


def _migrate_legacy_hook(hook_list: list) -> bool:
    """In-place migration of legacy PostToolUse entries to current shape."""
    changed = False
    for entry in hook_list:
        for hook in entry.get("hooks", []):
            cmd = hook.get("command", "")
            if cmd == "repowise augment":
                hook["command"] = "repowise-augment"
                changed = True
        matcher = entry.get("matcher", "")
        only_repowise = entry.get("hooks") and all(_is_repowise_hook(h) for h in entry["hooks"])
        if only_repowise and matcher == "Bash":
            entry["matcher"] = "Bash|Grep|Glob"
            changed = True
    return changed


def migrate_claude_code_hooks() -> bool:
    """Self-healing migration of legacy Claude Code hook entries."""
    settings_path = _claude_code_settings_path()
    if not settings_path.exists():
        return False

    try:
        existing = load_existing_config(settings_path)
    except Exception:
        return False

    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return False

    changed = False

    pre = hooks.get("PreToolUse")
    if isinstance(pre, list) and _strip_repowise_pretool(pre):
        changed = True
        if not pre:
            hooks.pop("PreToolUse", None)

    post = hooks.get("PostToolUse")
    if isinstance(post, list) and _migrate_legacy_hook(post):
        changed = True

    if not changed:
        return False

    try:
        settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True
