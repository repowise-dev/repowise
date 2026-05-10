"""Auto-generated MCP config for Claude Code, Claude Desktop, Cursor, and Cline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click


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


def generate_mcp_config(repo_path: Path) -> dict:
    """Generate MCP config JSON for a repository.

    Returns a dict in the standard mcpServers format.
    """
    abs_path = str(repo_path.resolve()).replace("\\", "/")
    return {
        "mcpServers": {
            "repowise": {
                "command": "repowise",
                "args": ["mcp", abs_path, "--transport", "stdio"],
                "description": "repowise: codebase intelligence — docs, graph, git signals, dead code, decisions",
            }
        }
    }


def save_mcp_config(repo_path: Path) -> Path:
    """Save MCP config to .repowise/mcp.json and return the path."""
    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir(parents=True, exist_ok=True)
    config_path = repowise_dir / "mcp.json"
    config = generate_mcp_config(repo_path)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config_path


def save_root_mcp_config(repo_path: Path) -> Path:
    """Write .mcp.json at repo root for Claude Code auto-discovery.

    Merges the repowise server entry into any existing mcpServers block
    so other MCP servers configured by the user are preserved.
    """
    config_path = repo_path / ".mcp.json"
    new_entry = generate_mcp_config(repo_path)["mcpServers"]

    if config_path.exists():
        existing = _load_existing_config(config_path)
        servers = dict(existing.get("mcpServers", {}))
        servers.update(new_entry)
        existing["mcpServers"] = servers
        merged = existing
    else:
        merged = {"mcpServers": new_entry}

    config_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return config_path


def _merge_mcp_entry(config_path: Path, new_entry: dict) -> bool:
    """Merge *new_entry* into the mcpServers block of *config_path*.

    Creates the file if it doesn't exist. Returns True on success.
    """
    try:
        if config_path.exists():
            existing = _load_existing_config(config_path)
        else:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}

        servers = dict(existing.get("mcpServers", {}))
        servers.update(new_entry)
        existing["mcpServers"] = servers
        config_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def _load_existing_config(config_path: Path) -> dict:
    """Load an existing JSON config without silently replacing bad content."""
    try:
        existing = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.ClickException(
            f"Cannot update {config_path}: existing file is not valid JSON. "
            "Fix or remove it and retry; no changes were written."
        ) from exc
    except OSError as exc:
        raise click.ClickException(
            f"Cannot update {config_path}: existing file could not be read. "
            "Fix the file permissions and retry; no changes were written."
        ) from exc
    if not isinstance(existing, dict):
        raise click.ClickException(
            f"Cannot update {config_path}: existing file must contain a JSON object. "
            "Fix or remove it and retry; no changes were written."
        )
    return existing


def register_with_claude_desktop(repo_path: Path) -> Path | None:
    """Add repowise MCP server to Claude Desktop's config.

    Returns the config path if successful, None if Claude Desktop is not
    present or the platform is unsupported.
    """
    config_path = _claude_desktop_config_path()
    if config_path is None:
        return None
    if not config_path.parent.exists():
        # Claude Desktop not installed
        return None
    entry = generate_mcp_config(repo_path)["mcpServers"]
    return config_path if _merge_mcp_entry(config_path, entry) else None


def register_with_claude_code(repo_path: Path) -> Path | None:
    """Add repowise MCP server to global Claude Code settings (~/.claude/settings.json).

    Returns the settings path if successful, None on failure.
    """
    settings_path = _claude_code_settings_path()
    entry = generate_mcp_config(repo_path)["mcpServers"]
    return settings_path if _merge_mcp_entry(settings_path, entry) else None


def install_claude_code_hooks() -> Path | None:
    """Register PreToolUse and PostToolUse hooks in ~/.claude/settings.json.

    PreToolUse: enriches Grep/Glob searches with graph context (importers,
    symbols) from the local wiki.db.

    PostToolUse: detects git commits and notifies the agent when the wiki
    is stale.

    Merges into existing hooks without clobbering user-defined entries.
    Returns the settings path on success, None on failure.
    """
    settings_path = _claude_code_settings_path()

    # Single PostToolUse hook covers Bash (stale-wiki nudge) and Grep/Glob
    # (zero-result rescue + big-result triage). PreToolUse enrichment was
    # removed: PostToolUse can see the actual result count and is strictly
    # more informed about whether enrichment will help.
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
            existing = _load_existing_config(settings_path)
        else:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}

        hooks = existing.setdefault("hooks", {})

        # Drop any pre-existing repowise PreToolUse entry — the new design
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
    """Check if a repowise hook is already registered (current or legacy)."""
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
    """Remove repowise's PreToolUse entry from a hook bucket in place.

    Pre-0.6.2 versions registered a PreToolUse Grep|Glob hook that
    enriched every search unconditionally. The new design moves all
    Grep/Glob enrichment into PostToolUse so the hook can see the
    actual result count and stay silent on focused searches. Existing
    users have a stale PreToolUse entry that needs to be removed
    rather than rewritten — the function it served is now folded into
    the PostToolUse handler.

    Returns True if anything was removed (caller may want to know).
    """
    changed = False
    for entry in list(hook_list):
        # Drop only repowise hooks within this entry; if that empties
        # the entry, drop the entry too. User-defined sibling hooks in
        # the same matcher block are preserved.
        kept = [h for h in entry.get("hooks", []) if not _is_repowise_hook(h)]
        if len(kept) != len(entry.get("hooks", [])):
            changed = True
            if kept:
                entry["hooks"] = kept
            else:
                hook_list.remove(entry)
    return changed


def _migrate_legacy_hook(hook_list: list) -> bool:
    """In-place migration of legacy PostToolUse entries to current shape.

    Two layers of legacy:

      * Pre-0.6.1 used the ``repowise augment`` Click subcommand, which
        crashes if any module in the full CLI fails to import (a single
        missing dep nukes every Bash/Grep tool call). Rewrite to the
        import-isolated ``repowise-augment`` console script.

      * Pre-0.6.2 used matcher ``"Bash"`` for the PostToolUse entry.
        The new design folds Grep/Glob enrichment into the same hook,
        so the matcher widens to ``"Bash|Grep|Glob"``.

    Idempotent: a hook already in the current shape is left alone.
    Returns True if anything was changed.
    """
    changed = False
    for entry in hook_list:
        # Migrate command names.
        for hook in entry.get("hooks", []):
            cmd = hook.get("command", "")
            if cmd == "repowise augment":
                hook["command"] = "repowise-augment"
                changed = True
        # Widen matcher on entries that only carry a repowise hook.
        matcher = entry.get("matcher", "")
        only_repowise = entry.get("hooks") and all(
            _is_repowise_hook(h) for h in entry["hooks"]
        )
        if only_repowise and matcher == "Bash":
            entry["matcher"] = "Bash|Grep|Glob"
            changed = True
    return changed


def migrate_claude_code_hooks() -> bool:
    """Self-healing migration of legacy hook entries in settings.json.

    Idempotent and silent — does nothing if the file is missing, malformed,
    or already migrated. Writes settings.json only when a legacy entry
    was actually rewritten or stripped.

    Two migrations are applied:

      1. PostToolUse: legacy ``repowise augment`` command names are
         rewritten to ``repowise-augment`` (pre-0.6.1), and the
         matcher is widened from ``"Bash"`` to ``"Bash|Grep|Glob"``
         (pre-0.6.2) so a single hook serves all three tool types.
      2. PreToolUse: any repowise-owned entry is removed entirely.
         The new design moves Grep/Glob enrichment into PostToolUse,
         which can see the actual result count.

    Called from both ``cli.main`` (so any ``repowise <command>`` after
    upgrade self-heals) and ``augment_hook`` (so users whose only repowise
    invocation is via the hook are also migrated on first successful fire).
    Wrapped failures: a malformed settings.json or a permissions error
    must never break the CLI or the hook.

    Returns True if a migration was performed.
    """
    settings_path = _claude_code_settings_path()
    if not settings_path.exists():
        return False

    try:
        existing = _load_existing_config(settings_path)
    except Exception:
        return False

    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        return False

    changed = False

    # Strip stale PreToolUse repowise entry.
    pre = hooks.get("PreToolUse")
    if isinstance(pre, list):
        if _strip_repowise_pretool(pre):
            changed = True
            if not pre:
                hooks.pop("PreToolUse", None)

    # Migrate PostToolUse.
    post = hooks.get("PostToolUse")
    if isinstance(post, list):
        if _migrate_legacy_hook(post):
            changed = True

    if not changed:
        return False

    try:
        settings_path.write_text(
            json.dumps(existing, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        return False
    return True


def format_setup_instructions(repo_path: Path) -> str:
    """Return human-readable setup instructions for MCP clients."""
    config = generate_mcp_config(repo_path)
    server_block = json.dumps(config["mcpServers"]["repowise"], indent=4)
    abs_path = str(repo_path.resolve()).replace("\\", "/")

    return f"""
MCP Server Configuration
========================

Claude Code: automatically configured via .mcp.json (no manual steps needed).

Cursor (.cursor/mcp.json):
  {server_block}

Cline (cline_mcp_settings.json):
  "mcpServers": {{
    "repowise": {server_block}
  }}

Or run directly:
  repowise mcp {abs_path}
  repowise mcp {abs_path} --transport sse --port 7338

Config saved to: {repo_path / ".repowise" / "mcp.json"}
""".strip()
