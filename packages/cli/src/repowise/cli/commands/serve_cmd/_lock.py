"""Discovery lockfile for a running ``serve`` (port, pid)."""

from __future__ import annotations

import contextlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from repowise.cli import __version__

_SERVE_LOCK_NAME = "serve.lock.json"


def _serve_lock_path(cwd: Path | None = None) -> Path | None:
    """Return where the serve lockfile belongs for this directory, if anywhere.

    The lockfile lets other local tooling discover a running server (port,
    pid) without port-scanning. It lives next to the index the server is
    serving: ``.repowise/`` for a single repo, ``.repowise-workspace/`` for a
    workspace root. Returns None when neither exists (nothing to serve).
    """
    base = cwd or Path.cwd()
    for dirname in (".repowise", ".repowise-workspace"):
        candidate = base / dirname
        if candidate.is_dir():
            return candidate / _SERVE_LOCK_NAME
    return None


def _write_serve_lock(lock_path: Path, *, host: str, port: int, ui_port: int | None) -> None:
    """Best-effort write of the discovery lockfile. Never blocks startup.

    Consumers must treat ``pid`` as the liveness check: a killed server
    cannot clean up after itself, so a stale file with a dead pid means
    "not running".
    """
    # A wildcard bind isn't a connectable URL; loopback always is.
    url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    payload = {
        "pid": os.getpid(),
        "host": host,
        "port": port,
        "url": f"http://{url_host}:{port}",
        "ui_port": ui_port,
        "server_version": __version__,
        "started_at": datetime.now(UTC).isoformat(),
    }
    with contextlib.suppress(OSError):
        lock_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _remove_serve_lock(lock_path: Path) -> None:
    """Best-effort removal; only removes a lock owned by this process."""
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        if data.get("pid") == os.getpid():
            lock_path.unlink()
    except (OSError, ValueError):
        pass


def live_serve_lock(lock_path: Path) -> dict | None:
    """The lockfile's payload when it names a process that still exists, else ``None``.

    A stale lock is the common case: ``serve`` only removes a lock it owns, so
    a killed server leaves one behind forever. Without a liveness probe the
    lock reads as stale.
    """
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = payload.get("pid") if isinstance(payload, dict) else None
    if not isinstance(pid, int):
        return None
    try:
        from repowise.core.procutils import pid_alive

        return payload if pid_alive(pid) else None
    except Exception:
        return None


def live_web_url(repo_path: Path) -> str | None:
    """The web app's address while ``repowise serve`` runs it for *repo_path*."""
    lock = _serve_lock_path(repo_path)
    payload = live_serve_lock(lock) if lock is not None else None
    ui_port = (payload or {}).get("ui_port")
    if not isinstance(ui_port, int):
        return None
    # The web UI listens on loopback whatever the API binds; the browser opener uses this too.
    return f"http://localhost:{ui_port}"
