"""``repowise serve`` — start the API server and web UI."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import click

from repowise.cli import __version__
from repowise.cli.helpers import console

_GITHUB_REPO = "RaghavChamadiya/repowise"
_WEB_CACHE_DIR = Path.home() / ".repowise" / "web"
_MARKER_FILE = _WEB_CACHE_DIR / ".version"


def _node_available() -> str | None:
    """Return the path to node binary, or None."""
    return shutil.which("node")


def _npm_available() -> str | None:
    """Return the path to npm binary, or None."""
    return shutil.which("npm")


def _web_is_cached(version: str) -> bool:
    """Check if the web frontend is cached and matches the current version."""
    server_js = _WEB_CACHE_DIR / "server.js"
    if not server_js.exists():
        return False
    if _MARKER_FILE.exists() and _MARKER_FILE.read_text().strip() == version:
        return True
    return False


def _find_local_web() -> Path | None:
    """Check if running from the repo with packages/web available."""
    # Walk up from this file to find the repo root
    candidate = Path(__file__).resolve()
    for _ in range(10):
        candidate = candidate.parent
        pkg_web = candidate / "packages" / "web"
        if (pkg_web / "package.json").exists():
            # Check if it's been built
            standalone = pkg_web / ".next" / "standalone" / "server.js"
            if standalone.exists():
                return pkg_web
            return pkg_web  # exists but may need build
    return None


def _download_web(version: str) -> bool:
    """Download pre-built web frontend from GitHub releases."""
    import urllib.request
    import urllib.error

    tag = f"v{version}"
    url = f"https://github.com/{_GITHUB_REPO}/releases/download/{tag}/repowise-web.tar.gz"

    console.print(f"[dim]Downloading web UI from {url}...[/dim]")
    try:
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            urllib.request.urlretrieve(url, tmp.name)
            tmp_path = tmp.name
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        console.print(f"[yellow]Could not download web UI: {exc}[/yellow]")
        return False

    try:
        _WEB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Clean old cache
        for item in _WEB_CACHE_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

        with tarfile.open(tmp_path, "r:gz") as tar:
            tar.extractall(_WEB_CACHE_DIR)

        _MARKER_FILE.write_text(version)
        console.print("[green]Web UI downloaded and cached.[/green]")
        return True
    except Exception as exc:
        console.print(f"[yellow]Failed to extract web UI: {exc}[/yellow]")
        return False
    finally:
        os.unlink(tmp_path)


def _build_local_web(web_dir: Path, npm: str) -> bool:
    """Build the Next.js frontend from source."""
    console.print("[dim]Building web UI (first time only)...[/dim]")
    try:
        # Install deps if needed
        if not (web_dir / "node_modules").exists():
            subprocess.run(
                [npm, "install"],
                cwd=str(web_dir),
                check=True,
                capture_output=True,
            )
        # Build
        subprocess.run(
            [npm, "run", "build"],
            cwd=str(web_dir),
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        console.print(f"[yellow]Web UI build failed: {exc}[/yellow]")
        return False


def _start_frontend(node: str, backend_port: int, frontend_port: int) -> subprocess.Popen | None:
    """Start the Next.js frontend server. Returns the process or None."""
    env = {
        **os.environ,
        "REPOWISE_API_URL": f"http://localhost:{backend_port}",
        "HOSTNAME": "0.0.0.0",
        "PORT": str(frontend_port),
    }

    # Option 1: Cached download
    cached_server = _WEB_CACHE_DIR / "server.js"
    if cached_server.exists():
        return subprocess.Popen(
            [node, str(cached_server)],
            cwd=str(_WEB_CACHE_DIR),
            env=env,
        )

    # Option 2: Local repo build
    local_web = _find_local_web()
    if local_web:
        standalone_dir = local_web / ".next" / "standalone"
        server_js = standalone_dir / "server.js"
        if server_js.exists():
            # Copy static files into standalone (Next.js requirement)
            static_src = local_web / ".next" / "static"
            static_dst = standalone_dir / ".next" / "static"
            if static_src.exists() and not static_dst.exists():
                shutil.copytree(str(static_src), str(static_dst))
            public_src = local_web / "public"
            public_dst = standalone_dir / "public"
            if public_src.exists() and not public_dst.exists():
                shutil.copytree(str(public_src), str(public_dst))

            return subprocess.Popen(
                [node, str(server_js)],
                cwd=str(standalone_dir),
                env=env,
            )

    return None


@click.command("serve")
@click.option("--port", default=7337, type=int, help="API server port.")
@click.option("--host", default="127.0.0.1", help="Host to bind to.")
@click.option("--workers", default=1, type=int, help="Number of uvicorn workers.")
@click.option("--ui-port", default=3000, type=int, help="Web UI port.")
@click.option("--no-ui", is_flag=True, help="Start API server only, skip the web UI.")
def serve_command(port: int, host: str, workers: int, ui_port: int, no_ui: bool) -> None:
    """Start the repowise server with the web UI.

    Starts the API backend and automatically launches the web frontend.
    The web UI is downloaded and cached on first run (~50 MB, one-time).

    Use --no-ui to start only the API server.
    """
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]uvicorn is not installed. Install it with: pip install repowise[/red]"
        )
        raise SystemExit(1)

    frontend_proc: subprocess.Popen | None = None

    if not no_ui:
        node = _node_available()
        npm = _npm_available()

        if not node:
            console.print(
                "[yellow]Node.js not found — starting API server only.[/yellow]\n"
                "[dim]To get the web UI, install Node.js 20+ or use Docker:\n"
                "  docker run -p 7337:7337 -p 3000:3000 -v .repowise:/data repowise[/dim]"
            )
        else:
            # Try to get the frontend running
            ready = False

            # Check cached download
            if _web_is_cached(__version__):
                ready = True

            # Check local repo build
            if not ready:
                local_web = _find_local_web()
                if local_web:
                    standalone = local_web / ".next" / "standalone" / "server.js"
                    if standalone.exists():
                        ready = True
                    elif npm:
                        ready = _build_local_web(local_web, npm)

            # Try downloading from GitHub releases
            if not ready:
                ready = _download_web(__version__)

            if ready:
                frontend_proc = _start_frontend(node, port, ui_port)
                if frontend_proc:
                    console.print(
                        f"[green]Web UI starting on http://localhost:{ui_port}[/green]"
                    )
                else:
                    console.print("[yellow]Could not start web UI — running API only.[/yellow]")
            else:
                console.print(
                    "[yellow]Web UI not available — starting API server only.[/yellow]\n"
                    "[dim]The web UI will be available in a future release.\n"
                    "For now, use Docker for the full experience:[/dim]\n"
                    f"[dim]  docker build -t repowise https://github.com/{_GITHUB_REPO}.git\n"
                    "  docker run -p 7337:7337 -p 3000:3000 -v .repowise:/data repowise[/dim]"
                )

    console.print(f"[green]API server starting on http://{host}:{port}[/green]")

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
        if frontend_proc:
            frontend_proc.terminate()
            frontend_proc.wait(timeout=5)
