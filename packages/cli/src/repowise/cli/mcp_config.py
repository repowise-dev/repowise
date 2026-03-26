"""Auto-generated MCP config for Claude Code, Cursor, and Cline."""

from __future__ import annotations

import json
from pathlib import Path


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
                "description": "repowise: live documentation for this codebase",
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


def format_setup_instructions(repo_path: Path) -> str:
    """Return human-readable setup instructions for MCP clients."""
    config = generate_mcp_config(repo_path)
    server_block = json.dumps(config["mcpServers"]["repowise"], indent=4)
    abs_path = str(repo_path.resolve()).replace("\\", "/")

    return f"""
MCP Server Configuration
========================

Add the following to your editor's MCP config:

Claude Code (~/.claude.json or ~/.claude/claude.json):
  "mcpServers": {{
    "repowise": {server_block}
  }}

Cursor (.cursor/mcp.json):
  {server_block}

Cline (cline_mcp_settings.json):
  "mcpServers": {{
    "repowise": {server_block}
  }}

Or run directly:
  repowise mcp {abs_path}
  repowise mcp {abs_path} --transport sse --port 7338

Config saved to: {repo_path / '.repowise' / 'mcp.json'}
""".strip()
