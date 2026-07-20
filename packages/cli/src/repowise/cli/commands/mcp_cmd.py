"""``repowise mcp`` — Start the MCP server for editor integration."""

from __future__ import annotations

from pathlib import Path

import click

from repowise.cli.helpers import console, find_repowise_repo_root, resolve_repo_path
from repowise.cli.ui import load_dotenv
from repowise.core.workspace.config import WorkspaceConfig, find_workspace_root


def _workspace_summary(path: Path) -> dict[str, object] | None:
    workspace_root = find_workspace_root(path)
    if workspace_root is None:
        return None

    try:
        ws_config = WorkspaceConfig.load(workspace_root)
    except Exception:
        return None

    aliases = ws_config.repo_aliases()
    default = ws_config.get_primary()
    default_alias = default.alias if default else (aliases[0] if aliases else None)
    resolved_path = path.resolve()

    for entry in ws_config.repos:
        entry_path = (workspace_root / entry.path).resolve()
        try:
            resolved_path.relative_to(entry_path)
        except ValueError:
            continue
        default_alias = entry.alias
        break

    return {
        "workspace_root": workspace_root,
        "default_repo": default_alias,
        "aliases": aliases,
    }


def _print_network_startup(
    transport: str,
    repo_path: Path,
    port: int,
    workspace: dict[str, object] | None,
) -> None:
    label = "streamable HTTP" if transport == "streamable-http" else "SSE"
    endpoint = "mcp" if transport == "streamable-http" else "sse"
    console.print(
        f"[bold green]Starting repowise MCP server ({label})[/bold green]\n"
        f"URL: http://127.0.0.1:{port}/{endpoint}"
    )

    if workspace is not None:
        aliases = workspace["aliases"]
        repo_list = ", ".join(aliases) if isinstance(aliases, list) else ""
        console.print(
            f"Workspace: {workspace['workspace_root']}\n"
            f"Default repo: {workspace['default_repo'] or 'none'}\n"
            f"Repos: {repo_list or 'none'}"
        )
    else:
        console.print(f"Repo: {repo_path}")


@click.command("mcp")
@click.argument("path", required=False, default=None)
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="stdio",
    help=(
        "Transport protocol: stdio (Claude Code/Codex/Cursor), "
        "streamable-http (HTTP clients), or sse (legacy web clients)."
    ),
)
@click.option(
    "--port",
    type=int,
    default=7338,
    help="Port for HTTP/SSE transports (default: 7338).",
)
@click.option(
    "--tools",
    default=None,
    help=(
        "Override which tools are exposed. A comma-separated list is an "
        "explicit allowlist; prefix names with + or - to adjust the default "
        "set (e.g. '+get_dependency_path,-get_dead_code'); 'lean' selects "
        "the six-tool agent-lean profile. Overrides the mcp.tools config "
        "block."
    ),
)
@click.option(
    "--all",
    "all_tools",
    is_flag=True,
    default=False,
    help="Expose every available tool, including opt-in and workspace tools.",
)
def mcp_command(
    path: str | None,
    transport: str,
    port: int,
    tools: str | None,
    all_tools: bool,
) -> None:
    """Start the MCP server for editor integration.

    Exposes a curated set of tools for querying the repowise wiki via the MCP
    protocol: eleven by default in single-repo mode, plus two more by default
    in workspace mode. Four more are opt-in via ``--tools`` or the
    ``mcp.tools`` config block. Supports stdio
    (for Claude Code, Codex, Cursor, Cline), streamable HTTP, and legacy SSE
    transports.

    Loads ``<repo>/.repowise/.env`` into the environment before starting so
    that MCP tools (e.g. ``get_answer``) can resolve the configured LLM
    provider and API keys.

    Examples:

        repowise mcp                     # stdio, current directory
        repowise mcp /path/to/repo       # stdio, specific repo
        repowise mcp --tools +get_execution_flows  # default set plus one
        repowise mcp --tools lean        # six-tool agent-lean profile
        repowise mcp --all               # every available tool
        repowise mcp --transport streamable-http  # HTTP on port 7338
    """
    if path is None:
        repo_path = find_repowise_repo_root(Path.cwd()) or resolve_repo_path(None)
    else:
        repo_path = resolve_repo_path(path)
    load_dotenv(repo_path)

    workspace = _workspace_summary(repo_path)
    repowise_dir = repo_path / ".repowise"
    if workspace is None and not repowise_dir.exists():
        console.print(
            f"[yellow]Warning: No .repowise directory found at {repo_path}.[/yellow]\n"
            "Run 'repowise init' first to generate documentation."
        )

    if transport in {"sse", "streamable-http"}:
        _print_network_startup(transport, repo_path, port, workspace)
    else:
        # stdio mode — no console output (it would corrupt the protocol)
        pass

    from repowise.server.mcp_server import run_mcp

    tools_override: str | None = "all" if all_tools else tools

    run_mcp(
        transport=transport,
        repo_path=str(repo_path),
        port=port,
        tools=tools_override,
    )
