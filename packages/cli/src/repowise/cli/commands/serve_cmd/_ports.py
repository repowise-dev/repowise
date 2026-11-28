"""Port probing for the API and web UI binds."""

from __future__ import annotations

import os
import socket

from repowise.cli.helpers import console

#: The port `serve` tries first for the web UI; every surface that names it reads this.
DEFAULT_UI_PORT = 3000


def _is_port_free(host: str, port: int) -> bool:
    """Return True if *port* can be bound on *host* right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _find_free_port(host: str, preferred: int, label: str, max_attempts: int = 20) -> int:
    """Return *preferred* if free, otherwise the next free port within range.

    Falls back to an OS-assigned port if a contiguous slot can't be found.
    Prints a notice when the preferred port is unavailable so the user knows
    the bind moved.
    """
    if _is_port_free(host, preferred):
        return preferred

    for offset in range(1, max_attempts + 1):
        candidate = preferred + offset
        if candidate > 65535:
            break
        if _is_port_free(host, candidate):
            console.print(
                f"[yellow]Port {preferred} for {label} is in use, using {candidate} instead.[/yellow]"
            )
            return candidate

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        candidate = sock.getsockname()[1]
    console.print(
        f"[yellow]Port {preferred} for {label} and nearby ports are busy, using {candidate}.[/yellow]"
    )
    return candidate
