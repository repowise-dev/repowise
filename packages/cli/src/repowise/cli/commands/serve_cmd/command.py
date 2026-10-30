"""The ``repowise serve`` Click command."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import click

from repowise.cli.helpers import console

from . import _advisory, _browser, _embedder, _frontend, _lock, _ports

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _import_uvicorn() -> ModuleType:
    try:
        import uvicorn
    except ImportError:
        console.print("[red]uvicorn is not installed. Install it with: pip install repowise[/red]")
        raise SystemExit(1) from None
    return uvicorn


def _resolve_db_url() -> None:
    """Point ``REPOWISE_DB_URL`` at ``./.repowise/wiki.db`` when nothing set it.

    Skipped inside a workspace: app.py reads a set ``REPOWISE_DB_URL`` there as
    "one shared database for the whole workspace" and routes every repo through
    the root's near-empty coordinator wiki.db, so every repo reports
    needs_index although each one's own index is complete on disk.
    """
    from repowise.core.workspace.config import find_workspace_root

    if (
        os.environ.get("REPOWISE_DB_URL")
        or os.environ.get("REPOWISE_DATABASE_URL")
        or find_workspace_root() is not None
    ):
        return
    local_repowise = Path.cwd() / ".repowise"
    if not local_repowise.exists():
        return
    local_db = local_repowise / "wiki.db"
    os.environ["REPOWISE_DB_URL"] = f"sqlite+aiosqlite:///{local_db.as_posix()}"
    console.print(f"[dim]Using local database: {local_db}[/dim]")


def _configure_host_env(host: str) -> None:
    """Publish the bind as ``REPOWISE_HOST`` and warn on a keyless wide bind.

    The auth layer reads ``REPOWISE_HOST`` to describe its own bind, so it is
    set before uvicorn imports the app factory (workers inherit it).
    """
    os.environ["REPOWISE_HOST"] = host
    if os.environ.get("REPOWISE_API_KEY") or host in _LOOPBACK_HOSTS:
        return
    console.print(
        f"[yellow]Binding to {host} without REPOWISE_API_KEY: non-local requests "
        f"will be refused.[/yellow]\n"
        "[dim]Set REPOWISE_API_KEY to allow them, or bind to 127.0.0.1.[/dim]"
    )


def _run_api(
    uvicorn: ModuleType,
    *,
    host: str,
    port: int,
    workers: int,
    ui_port: int | None,
    frontend_proc: subprocess.Popen | None,
) -> None:
    """Run uvicorn with the discovery lockfile held; clean up on exit."""
    if ui_port is not None:
        # The setup routes accept browser writes only from the UI's own origin,
        # and the UI proxies /api here, so the API has to be told its port.
        os.environ["REPOWISE_UI_PORT"] = str(ui_port)
    lock_path = _lock._serve_lock_path()
    if lock_path:
        _lock._write_serve_lock(lock_path, host=host, port=port, ui_port=ui_port)

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


def _maybe_open_browser(
    frontend_proc: subprocess.Popen | None, ui_port: int, *, no_open: bool
) -> None:
    from repowise.cli.platform.telemetry.environment import is_ci

    if _browser.should_open_browser(
        ui_started=frontend_proc is not None,
        no_open=no_open,
        interactive=sys.stdin.isatty() and sys.stdout.isatty(),
        ci=is_ci(),
        has_display=_browser.has_display_for_browser(),
    ):
        _browser.open_when_ready(ui_port, frontend_proc)


@click.command("serve")
@click.option("--port", default=7337, type=int, help="API server port.", envvar="REPOWISE_PORT")
@click.option("--host", default="127.0.0.1", help="Host to bind to.")
@click.option("--workers", default=1, type=int, help="Number of uvicorn workers.")
@click.option(
    "--ui-port",
    default=_ports.DEFAULT_UI_PORT,
    type=int,
    help="Web UI port (the next free one if taken).",
)
@click.option("--no-ui", is_flag=True, help="Start API server only, skip the web UI.")
@click.option(
    "--no-open",
    is_flag=True,
    envvar="REPOWISE_NO_OPEN",
    help="Don't open the web UI in a browser. Env: REPOWISE_NO_OPEN.",
)
@click.option(
    "--refresh-ui",
    is_flag=True,
    help="Force re-download of the web UI tarball, ignoring any cache.",
)
def serve_command(
    port: int,
    host: str,
    workers: int,
    ui_port: int,
    no_ui: bool,
    no_open: bool,
    refresh_ui: bool,
) -> None:
    """Start the repowise server with the web UI.

    Starts the API backend and automatically launches the web frontend.
    The web UI is downloaded and cached on first run (~50 MB, one-time).

    In an interactive terminal the web UI opens in your browser once it
    answers. Use --no-open to skip that, --no-ui to start only the API server.
    """
    uvicorn = _import_uvicorn()
    _advisory._start_update_advisory()

    # Local .repowise/.env and config.yaml first, so a configured embedder is
    # found instead of re-prompting. Existing env vars always win.
    _embedder._load_local_provider_config()
    _embedder._setup_embedder()

    _resolve_db_url()
    _configure_host_env(host)

    # Resolve the API port up front: uvicorn would crash later with a bare
    # OSError, and the chosen port flows into the frontend's REPOWISE_API_URL.
    port = _ports._find_free_port(host, port, "API server")

    frontend_proc: subprocess.Popen | None = None
    if not no_ui:
        # The UI takes the same bind as the API, so probe there.
        ui_port = _ports._find_free_port(host, ui_port, "web UI")
        frontend_proc = _frontend._launch_web_ui(host, port, ui_port, refresh_ui=refresh_ui)
    _maybe_open_browser(frontend_proc, ui_port, no_open=no_open)

    console.print(f"[green]API server starting on http://{host}:{port}[/green]")
    _run_api(
        uvicorn,
        host=host,
        port=port,
        workers=workers,
        ui_port=None if frontend_proc is None else ui_port,
        frontend_proc=frontend_proc,
    )
