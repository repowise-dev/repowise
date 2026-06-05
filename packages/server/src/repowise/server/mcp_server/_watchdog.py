"""Parent-death watchdog for ``repowise mcp --transport stdio``.

FastMCP's stdio loop does not reliably exit when the MCP client goes away
abnormally (crash, force-quit, killed terminal). On POSIX an orphaned
server at least reparents to init; on Windows nothing kills children when
the parent dies, so every abnormal session end leaks a ``repowise mcp``
server holding wiki.db handles. They accumulate silently and contend with
later ``repowise update`` runs (WAL writer locks).

A plain ``os.getppid()`` watchdog is NOT sufficient: console-script installs
launch through a chain of shims (``repowise.exe`` → venv ``python`` launcher
→ real interpreter on Windows; sometimes ``uv``/``uvx`` wrappers anywhere).
The server's immediate parent is a shim that waits on *us*, not the client —
when the client dies, the shims stay alive, so getppid never changes. The
watchdog instead walks the ancestor chain at startup, skipping past known
launcher processes to find the client, and watches every recorded ancestor
(identity-checked by creation time to defeat PID reuse).

Failure policy is conservative: if the chain can't be resolved, or a probe
returns "unknown", the watchdog does nothing — a leaked server is better
than killing a live one. ``REPOWISE_MCP_NO_WATCHDOG=1`` disables it
entirely.
"""

from __future__ import annotations

import logging
import os
import threading

from repowise.core.procutils import ProcInfo, ancestor_chain, pid_alive, process_create_token

_log = logging.getLogger(__name__)

# Process names that are launcher shims between the MCP client and us, not
# the client itself. Compared against the lowercased name with a trailing
# ``.exe`` stripped: by prefix for interpreter/shim families (covers
# ``python``, ``python3.12``, ``pythonw``, ``repowise``, ``uv``/``uvx``),
# and by exact match for shells — some clients spawn stdio servers through
# ``cmd /c`` / ``sh -c`` wrappers, which survive the client's death exactly
# like the shims do. (Prefix-matching "sh" would swallow unrelated names.)
_LAUNCHER_PREFIXES = ("python", "repowise", "uv")
_LAUNCHER_SHELLS = ("cmd", "sh", "bash", "zsh", "dash", "powershell", "pwsh")

_DISABLE_ENV = "REPOWISE_MCP_NO_WATCHDOG"

_POLL_INTERVAL_SECONDS = 5.0


def _is_launcher(name: str | None) -> bool:
    if not name:
        # Unknown name: treat as a launcher so the walk continues to a
        # nameable ancestor rather than anointing a mystery process as
        # the client.
        return True
    normalized = name.lower()
    if normalized.endswith(".exe"):
        normalized = normalized[:-4]
    return normalized.startswith(_LAUNCHER_PREFIXES) or normalized in _LAUNCHER_SHELLS


def compute_watch_set() -> list[ProcInfo]:
    """Ancestors to watch: launcher shims plus the first non-launcher (client).

    Stops at the client so unrelated higher ancestors (shell, terminal,
    desktop session) are never watched — those may die while the client
    legitimately keeps running. Returns ``[]`` when nothing was resolvable.
    """
    watch: list[ProcInfo] = []
    for info in ancestor_chain(os.getpid()):
        watch.append(info)
        if not _is_launcher(info.name):
            break
    return watch


def _ancestor_died(info: ProcInfo) -> bool:
    """True only when *info*'s process is positively gone or replaced."""
    alive = pid_alive(info.pid)
    if alive is False:
        return True
    if alive is True and info.create_token is not None:
        current = process_create_token(info.pid)
        if current is not None and current != info.create_token:
            # PID recycled by an unrelated process — the ancestor is gone.
            return True
    return False


def start_parent_watchdog() -> threading.Thread | None:
    """Start the daemon watchdog thread; returns it, or ``None`` when inactive.

    Exits the process with ``os._exit(0)`` when a watched ancestor dies.
    ``_exit`` (not ``sys.exit``) is deliberate: the stdio event loop is
    blocked on a dead pipe and would never unwind; SQLite/LanceDB handles
    are released by the OS at process exit.
    """
    if os.environ.get(_DISABLE_ENV, "").strip().lower() in ("1", "true", "yes"):
        _log.debug("MCP watchdog disabled via %s", _DISABLE_ENV)
        return None

    try:
        watch = compute_watch_set()
    except Exception:
        _log.debug("MCP watchdog: failed to resolve ancestor chain", exc_info=True)
        return None
    if not watch:
        _log.debug("MCP watchdog: no resolvable ancestors; watchdog inactive")
        return None

    _log.info(
        "MCP watchdog: watching %s",
        ", ".join(f"{w.name or '?'}({w.pid})" for w in watch),
    )

    def _run() -> None:
        # threading.Event.wait gives an interruptible sleep and lets tests
        # poke the loop; the event is never set in production.
        idle = threading.Event()
        while True:
            idle.wait(_POLL_INTERVAL_SECONDS)
            try:
                for info in watch:
                    if _ancestor_died(info):
                        _log.info(
                            "MCP watchdog: ancestor %s(%d) is gone — exiting",
                            info.name or "?",
                            info.pid,
                        )
                        os._exit(0)
            except Exception:
                # Probe hiccup — never let the watchdog kill or crash the
                # server on uncertainty.
                _log.debug("MCP watchdog: probe failed", exc_info=True)

    thread = threading.Thread(target=_run, name="repowise-mcp-watchdog", daemon=True)
    thread.start()
    return thread
