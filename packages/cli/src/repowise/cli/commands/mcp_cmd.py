"""``repowise mcp`` — Start the MCP server for editor integration."""

from __future__ import annotations

from pathlib import Path

import click

from repowise.cli.helpers import console, find_repowise_repo_root, resolve_repo_path
from repowise.cli.ui import load_dotenv


@click.command("mcp")
@click.argument("path", required=False, default=None)
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse"]),
    default="stdio",
    help="Transport protocol: stdio (Claude Code/Codex/Cursor) or sse (web clients).",
)
@click.option(
    "--port",
    type=int,
    default=7338,
    help="Port for SSE transport (default: 7338).",
)
def mcp_command(path: str | None, transport: str, port: int) -> None:
    """Start the MCP server for editor integration.

    Exposes 16 tools for querying the repowise wiki via the MCP protocol.
    Supports both stdio (for Claude Code, Codex, Cursor, Cline) and SSE transports.

    Loads ``<repo>/.repowise/.env`` into the environment before starting so
    that MCP tools (e.g. ``get_answer``) can resolve the configured LLM
    provider and API keys.

    Examples:

        repowise mcp                     # stdio, current directory
        repowise mcp /path/to/repo       # stdio, specific repo
        repowise mcp --transport sse     # SSE on port 7338
    """
    if path is None:
        repo_path = find_repowise_repo_root(Path.cwd()) or resolve_repo_path(None)
    else:
        repo_path = resolve_repo_path(path)
    load_dotenv(repo_path)

    repowise_dir = repo_path / ".repowise"
    if not repowise_dir.exists():
        console.print(
            f"[yellow]Warning: No .repowise directory found at {repo_path}.[/yellow]\n"
            "Run 'repowise init' first to generate documentation."
        )

    if transport == "sse":
        console.print(
            f"[bold green]Starting repowise MCP server (SSE) on port {port}...[/bold green]"
        )
    else:
        # stdio mode — no console output (it would corrupt the protocol)
        pass

    from repowise.server.mcp_server import run_mcp

    run_mcp(
        transport=transport,
        repo_path=str(repo_path),
        port=port,
    )
