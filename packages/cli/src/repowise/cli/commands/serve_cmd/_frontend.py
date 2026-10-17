"""Launching the Next.js web UI next to the API server."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import _web_assets


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
