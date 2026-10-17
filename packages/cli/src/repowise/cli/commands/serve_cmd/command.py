"""The ``repowise serve`` Click command."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import click

from repowise.cli import __version__
from repowise.cli.helpers import console

from . import _advisory, _embedder, _frontend, _lock, _ports, _web_assets


@click.command("serve")
@click.option("--port", default=7337, type=int, help="API server port.", envvar="REPOWISE_PORT")
@click.option("--host", default="127.0.0.1", help="Host to bind to.")
@click.option("--workers", default=1, type=int, help="Number of uvicorn workers.")
@click.option("--ui-port", default=3000, type=int, help="Web UI port.")
@click.option("--no-ui", is_flag=True, help="Start API server only, skip the web UI.")
@click.option(
    "--refresh-ui",
    is_flag=True,
    help="Force re-download of the web UI tarball, ignoring any cache.",
)
def serve_command(
    port: int, host: str, workers: int, ui_port: int, no_ui: bool, refresh_ui: bool
) -> None:
    """Start the repowise server with the web UI.

    Starts the API backend and automatically launches the web frontend.
    The web UI is downloaded and cached on first run (~50 MB, one-time).

    Use --no-ui to start only the API server.
    """
    try:
        import uvicorn
    except ImportError:
        console.print("[red]uvicorn is not installed. Install it with: pip install repowise[/red]")
        raise SystemExit(1) from None

    # One-line, non-blocking "newer release available" advisory at startup and
    # then once per interval for as long as the server runs. Best-effort and
    # interactive-only; the cached check keeps it off the network on most
    # passes.
    if console.is_terminal:
        announced: set[str] = set()
        _advisory._update_advisory_tick(announced)
        _advisory._start_update_advisory_loop(announced)

    # Load the local .repowise/.env (API keys written by `repowise init`) and
    # seed the chat/search provider + embedder from .repowise/config.yaml. This
    # runs before _setup_embedder so a configured embedder/key is detected
    # instead of re-prompting, and so the in-process API server starts with a
    # working provider (otherwise chat fails even though `init` saved a key).
    # Existing env vars always take precedence.
    _embedder._load_local_provider_config()

    _embedder._setup_embedder()

    # Auto-detect local .repowise/ directory if REPOWISE_DB_URL is not set.
    # repowise init writes to <repo>/.repowise/wiki.db, so honour it when
    # the user runs `repowise serve` from the same directory.
    #
    # Skip this when cwd is inside a workspace (has an upstream
    # .repowise-workspace.yaml). Setting REPOWISE_DB_URL here is read back
    # by the workspace-detection code in app.py as "the user explicitly
    # configured one shared database for the whole workspace" (the real,
    # separate shared-DB/Postgres feature), which then routes every repo
    # lookup through the workspace root's own near-empty coordinator
    # wiki.db instead of each repo's own <repo>/.repowise/wiki.db. Every
    # repo in the workspace then reports needs_index with zeroed stats,
    # and the UI can't open any of them even though each repo's own index
    # is complete and correct on disk.
    from repowise.core.workspace.config import find_workspace_root

    if not os.environ.get("REPOWISE_DB_URL") and find_workspace_root() is None:
        local_repowise = Path.cwd() / ".repowise"
        if local_repowise.exists():
            local_db = local_repowise / "wiki.db"
            os.environ["REPOWISE_DB_URL"] = f"sqlite+aiosqlite:///{local_db.as_posix()}"
            console.print(f"[dim]Using local database: {local_db}[/dim]")

    # The server reads REPOWISE_HOST to describe its own bind. Nothing used to
    # set it from --host, so `serve --host 0.0.0.0` left the auth layer
    # believing it was loopback-only. Set before the app is imported (uvicorn
    # imports the factory below, and workers inherit the environment).
    os.environ["REPOWISE_HOST"] = host
    if not os.environ.get("REPOWISE_API_KEY") and host not in ("127.0.0.1", "localhost", "::1"):
        console.print(
            f"[yellow]Binding to {host} without REPOWISE_API_KEY: non-local requests "
            f"will be refused.[/yellow]\n"
            "[dim]Set REPOWISE_API_KEY to allow them, or bind to 127.0.0.1.[/dim]"
        )

    # Resolve a usable API port up front — uvicorn would otherwise crash later
    # with a bare OSError, and the chosen port needs to flow into the frontend
    # via REPOWISE_API_URL.
    port = _ports._find_free_port(host, port, "API server")

    frontend_proc: subprocess.Popen | None = None

    if not no_ui:
        # The Next.js server takes the same bind as the API (see HOSTNAME
        # below), so probe there.
        ui_port = _ports._find_free_port(host, ui_port, "web UI")

        node = _web_assets._node_available()
        npm = _web_assets._npm_available()

        # Reject Node versions older than what the bundled Next.js needs.
        # Without this check, an old node silently produces cryptic syntax
        # errors from inside the Next.js runtime (e.g. `Unexpected token '?'`
        # on `??`) once the frontend tries to start — see issue #276.
        node_major = _web_assets._node_major_version(node) if node else None
        node_too_old = node is not None and node_major is not None and node_major < _web_assets._NODE_MIN_MAJOR

        if not node:
            console.print(
                f"[yellow]Node.js not found — starting API server only.[/yellow]\n"
                f"[dim]To get the web UI, install Node.js {_web_assets._NODE_MIN_MAJOR}+ or use Docker:\n"
                f"  docker run -p 7337:7337 -p 3000:3000 -v .repowise:/data repowise[/dim]"
            )
        elif node_too_old:
            console.print(
                f"[yellow]Node.js {node_major} is too old for the bundled web UI "
                f"(requires {_web_assets._NODE_MIN_MAJOR}+) — starting API server only.[/yellow]\n"
                f"[dim]Upgrade Node.js to {_web_assets._NODE_MIN_MAJOR}+ or use Docker:\n"
                f"  docker run -p 7337:7337 -p 3000:3000 -v .repowise:/data repowise[/dim]"
            )
        else:
            # Try to get the frontend running
            ready = False
            local_web: Path | None = None if refresh_ui else _web_assets._find_local_web()

            # Option 1: Local repo build (preferred — uses latest source).
            # Only honoured when the bundle isn't behind the source tree.
            if local_web:
                if _web_assets._local_build_is_stale(local_web):
                    if npm:
                        console.print(
                            "[dim]Local web bundle is older than source — rebuilding...[/dim]"
                        )
                        ready = _web_assets._build_local_web(local_web, npm)
                        if not ready:
                            console.print(
                                "[yellow]Local rebuild failed — falling back to "
                                "released tarball.[/yellow]"
                            )
                            local_web = None
                    else:
                        console.print(
                            "[yellow]Local web bundle is stale and npm not found — "
                            "falling back to released tarball.[/yellow]"
                        )
                        local_web = None
                else:
                    ready = True

            # Option 2: Cached download (skip when --refresh-ui is set).
            if not ready and not refresh_ui and _web_assets._web_is_cached(__version__):
                ready = True

            # Option 3: Download from GitHub releases.
            if not ready:
                ready = _web_assets._download_web(__version__)

            if ready:
                frontend_proc = _frontend._start_frontend(
                    node, port, ui_port, local_web=local_web, host=host
                )
                if frontend_proc:
                    console.print(f"[green]Web UI starting on http://localhost:{ui_port}[/green]")
                else:
                    console.print("[yellow]Could not start web UI — running API only.[/yellow]")
            else:
                console.print(
                    "[yellow]Web UI not available — starting API server only.[/yellow]\n"
                    "[dim]The web UI will be available in a future release.\n"
                    "For now, use Docker for the full experience:[/dim]\n"
                    f"[dim]  docker build -t repowise https://github.com/{_web_assets._GITHUB_REPO}.git\n"
                    "  docker run -p 7337:7337 -p 3000:3000 -v .repowise:/data repowise[/dim]"
                )

    console.print(f"[green]API server starting on http://{host}:{port}[/green]")

    lock_path = _lock._serve_lock_path()
    if lock_path:
        _lock._write_serve_lock(
            lock_path,
            host=host,
            port=port,
            ui_port=None if (no_ui or frontend_proc is None) else ui_port,
        )

    try:
        uvicorn.run(
            "repowise.server.app:create_app",
            factory=True,
            host=host,
            port=port,
            workers=workers,
            log_level="info",
        )
    finally:
        if lock_path:
            _lock._remove_serve_lock(lock_path)
        if frontend_proc:
            frontend_proc.terminate()
            frontend_proc.wait(timeout=5)
