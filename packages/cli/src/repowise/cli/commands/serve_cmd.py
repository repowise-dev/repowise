"""``repowise serve`` — start the API server and web UI."""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import click

from repowise.cli import __version__
from repowise.cli.helpers import console, load_config

_GLOBAL_CONFIG_DIR = Path.home() / ".repowise"


def _setup_embedder() -> None:
    """Ensure REPOWISE_EMBEDDER is set before the server starts.

    Priority:
      1. Already set in environment → nothing to do.
      2. Saved in ~/.repowise/config.yaml → restore it (and its API key).
      3. Prompt the user interactively → save choice for next time.
    """
    if os.environ.get("REPOWISE_EMBEDDER"):
        return

    # Check global config saved by a previous serve/init run.
    cfg = load_config(Path.home())
    saved_embedder = cfg.get("embedder", "")
    if saved_embedder and saved_embedder != "mock":
        os.environ["REPOWISE_EMBEDDER"] = saved_embedder
        # Restore API key if saved alongside the config.
        if cfg.get("embedder_api_key"):
            _set_api_key_env(saved_embedder, cfg["embedder_api_key"])
        return

    # Detect which providers already have keys in the environment.
    has_gemini = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))

    console.print(
        "\n[bold]Chat & search require an embedder.[/bold] "
        "Choose one or skip (other features still work).\n"
    )

    options = []
    labels = []
    if has_gemini:
        options.append("gemini")
        labels.append("[1] gemini  [green]✓ key set[/green]")
    else:
        options.append("gemini")
        labels.append("[1] gemini  [dim]needs GEMINI_API_KEY / GOOGLE_API_KEY[/dim]")
    if has_openai:
        options.append("openai")
        labels.append("[2] openai  [green]✓ key set[/green]")
    else:
        options.append("openai")
        labels.append("[2] openai  [dim]needs OPENAI_API_KEY[/dim]")
    options.append("skip")
    labels.append("[3] skip    [dim]no chat/search[/dim]")

    for label in labels:
        console.print(f"  {label}")
    console.print()

    default = "1" if (has_gemini or has_openai) else "3"
    raw = click.prompt("  Select", default=default).strip()

    # Map number or name to option.
    choice = (
        raw
        if raw in options
        else (options[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(options) else "skip")
    )

    if choice == "skip":
        console.print("[dim]Skipping embedder — chat and search will be unavailable.[/dim]\n")
        return

    os.environ["REPOWISE_EMBEDDER"] = choice

    # Ensure the API key is present; prompt if missing.
    api_key = _get_or_prompt_api_key(choice)
    if api_key:
        _set_api_key_env(choice, api_key)

    # Save choice (and key) to ~/.repowise/config.yaml for future runs.
    _save_global_embedder(choice, api_key)
    console.print()


def _get_or_prompt_api_key(embedder: str) -> str:
    """Return existing API key for *embedder* or prompt the user for one."""
    if embedder == "gemini":
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if key:
            return key
        return click.prompt("  GEMINI_API_KEY", default="", show_default=False).strip()
    if embedder == "openai":
        key = os.environ.get("OPENAI_API_KEY", "")
        if key:
            return key
        return click.prompt("  OPENAI_API_KEY", default="", show_default=False).strip()
    return ""


def _set_api_key_env(embedder: str, key: str) -> None:
    if not key:
        return
    if embedder == "gemini":
        os.environ.setdefault("GEMINI_API_KEY", key)
    elif embedder == "openai":
        os.environ.setdefault("OPENAI_API_KEY", key)


def _save_global_embedder(embedder: str, api_key: str) -> None:
    """Persist embedder choice to ~/.repowise/config.yaml."""
    _GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_path = _GLOBAL_CONFIG_DIR / "config.yaml"
    try:
        existing: dict = {}
        if config_path.exists():
            import yaml  # type: ignore[import-untyped]

            existing = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        existing["embedder"] = embedder
        if api_key:
            existing["embedder_api_key"] = api_key
        import yaml  # type: ignore[import-untyped]

        config_path.write_text(
            yaml.dump(existing, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
    except Exception:
        pass  # Non-fatal — user just gets prompted again next time.


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
    return _MARKER_FILE.exists() and _MARKER_FILE.read_text().strip() == version


def _find_local_web() -> Path | None:
    """Check if running from the repo with packages/web available."""
    # Check from both __file__ (source installs) and cwd (pip-installed runs)
    roots = [Path(__file__).resolve(), Path.cwd().resolve()]
    for start in roots:
        candidate = start
        for _ in range(10):
            candidate = candidate.parent
            pkg_web = candidate / "packages" / "web"
            if (pkg_web / "package.json").exists():
                # Next.js standalone in monorepos nests server under package path
                standalone = pkg_web / ".next" / "standalone" / "packages" / "web" / "server.js"
                if standalone.exists():
                    return pkg_web
                return pkg_web  # exists but may need build
    return None


def _download_web(version: str) -> bool:
    """Download pre-built web frontend from GitHub releases."""
    import httpx

    tag = f"v{version}"
    url = f"https://github.com/{_GITHUB_REPO}/releases/download/{tag}/repowise-web.tar.gz"

    console.print(f"[dim]Downloading web UI ({url})...[/dim]")
    try:
        tmp_path = None
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = tmp.name
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes(chunk_size=65536):
                    tmp.write(chunk)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        console.print(f"[yellow]Could not download web UI: {exc}[/yellow]")
        if tmp_path:
            os.unlink(tmp_path)
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

    # Option 1: Local repo build (preferred — uses latest source)
    local_web = _find_local_web()
    if local_web:
        standalone_dir = local_web / ".next" / "standalone"
        server_js = standalone_dir / "packages" / "web" / "server.js"
        if server_js.exists():
            # Copy static files into standalone (Next.js requirement)
            static_src = local_web / ".next" / "static"
            static_dst = standalone_dir / "packages" / "web" / ".next" / "static"
            if static_src.exists() and not static_dst.exists():
                shutil.copytree(str(static_src), str(static_dst))
            public_src = local_web / "public"
            public_dst = standalone_dir / "packages" / "web" / "public"
            if public_src.exists() and not public_dst.exists():
                shutil.copytree(str(public_src), str(public_dst))

            return subprocess.Popen(
                [node, str(server_js)],
                cwd=str(standalone_dir / "packages" / "web"),
                env=env,
            )

    # Option 2: Cached download (fallback for pip-installed users)
    cached_server = _WEB_CACHE_DIR / "server.js"
    if cached_server.exists():
        return subprocess.Popen(
            [node, str(cached_server)],
            cwd=str(_WEB_CACHE_DIR),
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
        console.print("[red]uvicorn is not installed. Install it with: pip install repowise[/red]")
        raise SystemExit(1) from None

    _setup_embedder()

    # Auto-detect local .repowise/ directory if REPOWISE_DB_URL is not set.
    # repowise init writes to <repo>/.repowise/wiki.db, so honour it when
    # the user runs `repowise serve` from the same directory.
    if not os.environ.get("REPOWISE_DB_URL"):
        local_repowise = Path.cwd() / ".repowise"
        if local_repowise.exists():
            local_db = local_repowise / "wiki.db"
            os.environ["REPOWISE_DB_URL"] = f"sqlite+aiosqlite:///{local_db.as_posix()}"
            console.print(f"[dim]Using local database: {local_db}[/dim]")

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
                    standalone = (
                        local_web / ".next" / "standalone" / "packages" / "web" / "server.js"
                    )
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
                    console.print(f"[green]Web UI starting on http://localhost:{ui_port}[/green]")
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
