"""Auto-generated MCP config for Claude Code, Claude Desktop, Cursor, and Cline."""

from __future__ import annotations

import json
import sys
from pathlib import Path


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
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
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
            try:
                existing = json.loads(config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = {}
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
    dependencies, symbols) from the local wiki.db.

    PostToolUse: detects git commits and notifies the agent when the wiki
    is stale.

    Merges into existing hooks without clobbering user-defined entries.
    Returns the settings path on success, None on failure.
    """
    settings_path = _claude_code_settings_path()

    pre_hook_entry = {
        "matcher": "Grep|Glob",
        "hooks": [
            {
                "type": "command",
                "command": "repowise augment",
                "timeout": 10,
                "statusMessage": "Enriching with codebase context...",
            }
        ],
    }

    post_hook_entry = {
        "matcher": "Bash",
        "hooks": [
            {
                "type": "command",
                "command": "repowise augment",
                "timeout": 10,
                "statusMessage": "Checking wiki freshness...",
            }
        ],
    }

    try:
        if settings_path.exists():
            try:
                existing = json.loads(settings_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = {}
        else:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}

        hooks = existing.setdefault("hooks", {})

        # Merge PreToolUse — avoid duplicates
        pre_hooks = hooks.setdefault("PreToolUse", [])
        if not _has_repowise_hook(pre_hooks):
            pre_hooks.append(pre_hook_entry)

        # Merge PostToolUse — avoid duplicates
        post_hooks = hooks.setdefault("PostToolUse", [])
        if not _has_repowise_hook(post_hooks):
            post_hooks.append(post_hook_entry)

        settings_path.write_text(
            json.dumps(existing, indent=2) + "\n", encoding="utf-8"
        )
        return settings_path
    except OSError:
        return None


def _has_repowise_hook(hook_list: list) -> bool:
    """Check if a repowise augment hook is already registered."""
    for entry in hook_list:
        for hook in entry.get("hooks", []):
            cmd = hook.get("command", "")
            if "repowise augment" in cmd:
                return True
    return False


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
