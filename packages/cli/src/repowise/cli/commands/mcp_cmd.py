"""``repowise mcp`` — Start the MCP server for editor integration."""

from __future__ import annotations

import os
from pathlib import Path

import click

from repowise.cli.errors import reasoned_error
from repowise.cli.helpers import console, find_repowise_repo_root, resolve_repo_path, warn
from repowise.cli.ui import load_dotenv


def _borrowed_index(start: Path, found: Path) -> tuple[Path, Path] | None:
    """``(worktree, main checkout)`` when *start* is in a linked worktree with
    no index of its own whose main checkout is indexed, else ``None``.

    *found* is the directory the server would otherwise serve. An index at or
    under the worktree's top level is the worktree's own and wins.
    """
    from repowise.cli._helpers.paths import borrowed_index_base
    from repowise.core.git_head import find_checkout

    checkout = find_checkout(start)
    if checkout is None:
        return None
    root = checkout.root
    if (found / ".repowise").is_dir() and found.resolve().is_relative_to(root):
        return None
    base = borrowed_index_base(root)
    return (root, base) if base is not None else None


def _workspace_summary(path: Path, *, no_workspace: bool = False) -> dict[str, object] | None:
    if no_workspace:
        return None
    # Deferred so ``repowise mcp --help`` skips the server module. Same detection
    # the server runs, so the banner names the repo that is actually served.
    from repowise.server.mcp_server._server import _detect_workspace

    workspace_root, ws_config, repo_alias = _detect_workspace(str(path))
    if ws_config is None:
        return None

    return {
        "workspace_root": workspace_root,
        "default_repo": repo_alias,
        "aliases": ws_config.repo_aliases(),
    }


#: Transports the MCP spec has moved past, mapped to what replaces them. SSE
#: stays selectable for now because editor and client configs already name it
#: on disk, and removing it outright turns a working config into a startup
#: failure with no migration window. The notice names the replacement rather
#: than the transport being withdrawn.
_DEPRECATED_TRANSPORTS = {"sse": "streamable-http"}


def _print_network_startup(
    transport: str,
    repo_path: Path,
    host: str,
    port: int,
    workspace: dict[str, object] | None,
) -> None:
    replacement = _DEPRECATED_TRANSPORTS.get(transport)
    if replacement is not None:
        console.print(
            f"[yellow]--transport {transport} is deprecated and will be removed "
            f"in a future release; use --transport {replacement}.[/yellow]"
        )
    label = "streamable HTTP" if transport == "streamable-http" else "SSE"
    endpoint = "mcp" if transport == "streamable-http" else "sse"
    console.print(
        f"[bold green]Starting repowise MCP server ({label})[/bold green]\n"
        f"URL: http://{host}:{port}/{endpoint}"
    )

    if not os.environ.get("REPOWISE_API_KEY") and host in ("0.0.0.0", "::"):
        console.print(
            "[bold yellow]SECURITY WARNING:[/bold yellow] MCP server is binding to "
            f"[bold]{host}[/bold] without REPOWISE_API_KEY set. "
            "All tools are unauthenticated and network-accessible. "
            "Set REPOWISE_API_KEY or bind to 127.0.0.1."
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
        "streamable-http (HTTP clients), or sse (deprecated, use "
        "streamable-http)."
    ),
)
@click.option(
    "--port",
    type=int,
    default=7338,
    help="Port for HTTP/SSE transports (default: 7338).",
)
@click.option(
    "--host",
    default=None,
    help=(
        "Host to bind for HTTP/SSE transports. Defaults to the REPOWISE_HOST "
        "environment variable, or 127.0.0.1. Use 0.0.0.0 to listen on all "
        "interfaces (without REPOWISE_API_KEY a security warning will be printed)."
    ),
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
    help="Expose every tool eligible in the current repository/workspace mode.",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help=(
        "Force single-repo mode even when the path is inside a workspace. "
        "Useful for nested indexed repos that should not inherit the "
        "enclosing workspace's default repo."
    ),
)
def mcp_command(
    path: str | None,
    transport: str,
    port: int,
    host: str | None,
    tools: str | None,
    all_tools: bool,
    no_workspace: bool,
) -> None:
    """Start the MCP server for editor integration.

    Exposes a curated set of tools for querying the repowise wiki via the MCP
    protocol: ten by default in single-repo mode, plus one more by default
    in workspace mode. Seven more are opt-in via ``--tools`` or the
    ``mcp.tools`` config block. Supports stdio
    (for Claude Code, Codex, Cursor), streamable HTTP, and the deprecated
    SSE transport.

    Loads ``<repo>/.repowise/.env`` into the environment before starting so
    that MCP tools (e.g. ``get_answer``) can resolve the configured LLM
    provider and API keys.

    Examples:

        repowise mcp                     # stdio, current directory
        repowise mcp /path/to/repo       # stdio, specific repo
        repowise mcp --tools +get_execution_flows  # default set plus one
        repowise mcp --tools lean        # six-tool agent-lean profile
        repowise mcp --all               # every available tool
        repowise mcp --no-workspace      # force single-repo mode
        repowise mcp --transport streamable-http  # HTTP on port 7338
    """
    if path is None:
        repo_path = find_repowise_repo_root(Path.cwd()) or resolve_repo_path(None)
    else:
        repo_path = resolve_repo_path(path)
    borrowed = _borrowed_index(Path.cwd() if path is None else repo_path, repo_path)
    if borrowed is not None:
        worktree, repo_path = borrowed
    load_dotenv(repo_path)

    workspace = _workspace_summary(repo_path, no_workspace=no_workspace)
    if borrowed is not None and workspace is None:
        from repowise.server.mcp_server import _checkout

        # Ceiling: single-repo only; a workspace server answers a worktree
        # path passed as `repo`, and a default caller there needs the
        # registry to know which member the worktree belongs to.
        _checkout.serve_worktree(worktree, repo_path)
    repowise_dir = repo_path / ".repowise"
    if workspace is None and not repowise_dir.exists():
        warn(
            f"No .repowise directory found at {repo_path}.\nRun 'repowise init' first to generate documentation."
        )

    resolved_host = host or os.environ.get("REPOWISE_HOST", "127.0.0.1")

    if transport in {"sse", "streamable-http"}:
        _print_network_startup(transport, repo_path, resolved_host, port, workspace)
    else:
        # stdio mode — no console output (it would corrupt the protocol)
        pass

    from repowise.server.mcp_server import run_mcp
    from repowise.server.mcp_server._server import StoreUnavailableError

    tools_override: str | None = "all" if all_tools else tools

    # Which of the ways a session can end this one was. Recorded in a finally
    # so a fault is reported as one: leaving the field off for every failed
    # session would make its absence mean either "faulted" or "older build".
    outcome = "server_fault"
    try:
        outcome = run_mcp(
            transport=transport,
            repo_path=str(repo_path),
            host=resolved_host,
            port=port,
            tools=tools_override,
            workspace_mode=not no_workspace,
        )
    except StoreUnavailableError as exc:
        # One line on stderr and exit 1, not a traceback the host respawns on.
        # The reason slug becomes the event's failure_reason.
        raise reasoned_error(str(exc), reason=exc.reason) from exc
    finally:
        from repowise.cli.platform import telemetry
        from repowise.server.mcp_server import _state

        telemetry.add_command_outcome(
            transport=transport,
            transport_outcome=outcome,
            served_no_index=bool(_state._serving_no_index),
        )
