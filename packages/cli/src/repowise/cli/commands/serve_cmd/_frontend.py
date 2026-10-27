"""Launching the Next.js web UI next to the API server."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from repowise.cli import __version__
from repowise.cli.helpers import console

from . import _web_assets

_DOCKER_RUN = "  docker run -p 7337:7337 -p 3000:3000 -v .repowise:/data repowise[/dim]"


def _node_is_usable(node: str | None) -> bool:
    """Reject a missing node, or one older than the bundled Next.js needs.

    An old node otherwise fails inside the Next.js runtime with cryptic syntax
    errors (e.g. ``Unexpected token '?'`` on ``??``), see issue #276. An
    unreadable version is treated as usable.
    """
    min_major = _web_assets._NODE_MIN_MAJOR
    if not node:
        console.print(
            f"[yellow]Node.js not found, starting the API server only.[/yellow]\n"
            f"[dim]The web UI needs Node.js {min_major}+. Install it and run "
            "repowise serve again, or use Docker:\n"
            f"{_DOCKER_RUN}"
        )
        return False
    node_major = _web_assets._node_major_version(node)
    if node_major is None or node_major >= min_major:
        return True
    console.print(
        f"[yellow]Node.js {node_major} is too old for the web UI "
        f"(needs {min_major}+), starting the API server only.[/yellow]\n"
        f"[dim]Upgrade Node.js to {min_major}+ and run repowise serve again, or use Docker:\n"
        f"{_DOCKER_RUN}"
    )
    return False


def _rebuild_stale_local_web(local_web: Path) -> bool:
    """Rebuild a local bundle that is behind its source; False means fall back."""
    npm = _web_assets._npm_available()
    if not npm:
        console.print(
            "[yellow]Local web bundle is stale and npm was not found, "
            "using the released bundle instead.[/yellow]"
        )
        return False
    console.print("[dim]Local web bundle is older than its source, rebuilding...[/dim]")
    if _web_assets._build_local_web(local_web, npm):
        return True
    console.print("[yellow]Local rebuild failed, using the released bundle instead.[/yellow]")
    return False


def _resolve_web_bundle(refresh_ui: bool) -> tuple[bool, Path | None]:
    """Return (ready, local_web): a fresh local build, else the cache, else a download.

    ``--refresh-ui`` skips both the local build and the cache.
    """
    local_web = None if refresh_ui else _web_assets._find_local_web()
    if local_web and (
        not _web_assets._local_build_is_stale(local_web) or _rebuild_stale_local_web(local_web)
    ):
        return True, local_web
    if not refresh_ui and _web_assets._web_is_cached(__version__):
        return True, None
    return _web_assets._download_web(__version__), None


def _launch_web_ui(
    host: str, api_port: int, ui_port: int, *, refresh_ui: bool
) -> subprocess.Popen | None:
    """Start the web UI when node and a bundle are available; None runs API only."""
    node = _web_assets._node_available()
    if not _node_is_usable(node):
        return None

    ready, local_web = _resolve_web_bundle(refresh_ui)
    if not ready:
        # The download or build error is printed just above this line.
        console.print(
            f"[yellow]Could not get the web UI for v{__version__}, "
            "starting the API server only.[/yellow]\n"
            "[dim]Check the network and retry with repowise serve --refresh-ui, "
            "or use Docker:\n"
            f"  docker build -t repowise https://github.com/{_web_assets._GITHUB_REPO}.git\n"
            f"{_DOCKER_RUN}"
        )
        return None

    proc = _start_frontend(node, api_port, ui_port, local_web=local_web, host=host)
    if proc:
        console.print(f"[green]Web UI starting on http://localhost:{ui_port}[/green]")
    else:
        console.print(
            "[yellow]Could not start the web UI, running the API server only.[/yellow]\n"
            "[dim]Retry with repowise serve --refresh-ui to download it again.[/dim]"
        )
    return proc


def _start_frontend(
    node: str,
    backend_port: int,
    frontend_port: int,
    local_web: Path | None = None,
    host: str = "127.0.0.1",
) -> subprocess.Popen | None:
    """Start the Next.js frontend server. Returns the process or None.

    If ``local_web`` is provided, the local monorepo build is used as Option 1.
    Pass ``None`` to skip the local build (e.g. when it's stale or refresh was
    forced) and use only the cached tarball at ``~/.repowise/web``.

    ``host`` is the API's bind address, and the UI takes the same one. The UI
    rewrites ``/api/*`` to the API on loopback (packages/web/src/middleware.ts),
    so a UI on the wildcard while the API is loopback-only handed the whole API
    to the network through the proxy, with every request looking local.
    """
    env = {
        **os.environ,
        "REPOWISE_API_URL": f"http://localhost:{backend_port}",
        "HOSTNAME": host,
        "PORT": str(frontend_port),
    }

    # Option 1: Local repo build (preferred — uses latest source)
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
    cache_dir = _web_assets._WEB_CACHE_DIR
    cached_server = cache_dir / "server.js"
    if cached_server.exists():
        return subprocess.Popen(
            [node, str(cached_server)],
            cwd=str(cache_dir),
            env=env,
        )

    return None
