"""Opening the web UI in the default browser once it answers."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable

from repowise.cli.helpers import console

logger = logging.getLogger(__name__)

#: How long to wait for the UI before giving up quietly. A first start of the
#: Next.js server takes a few seconds; a slow disk can take longer.
_READY_TIMEOUT_S = 60.0
_POLL_INTERVAL_S = 0.5

# localhost never goes through a proxy, even when HTTP_PROXY is set.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _has_display() -> bool:
    """False on a Linux box with no graphical session.

    There ``webbrowser`` falls back to a text browser that would take over the
    terminal the server logs to.
    """
    if not sys.platform.startswith("linux"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def should_open_browser(
    *, ui_started: bool, no_open: bool, interactive: bool, ci: bool, has_display: bool
) -> bool:
    """Open only for a person at a terminal whose UI actually started.

    ``ui_started`` is False both for ``--no-ui`` and for a UI that could not
    start (no Node, failed download).
    """
    return ui_started and not no_open and interactive and not ci and has_display


def _ui_answers(url: str) -> bool:
    """True when anything answers HTTP at *url*, an error status included."""
    try:
        with _NO_PROXY_OPENER.open(url, timeout=1):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError):
        return False


def _wait_and_open(
    url: str,
    proc: subprocess.Popen | None,
    *,
    probe: Callable[[str], bool] | None = None,
    opener: Callable[[str], bool] | None = None,
    timeout_s: float = _READY_TIMEOUT_S,
    interval_s: float = _POLL_INTERVAL_S,
) -> bool:
    """Poll *url* until it answers, then open it once. Never raises."""
    probe = probe or _ui_answers
    opener = opener or webbrowser.open
    try:
        deadline = time.monotonic() + timeout_s
        while not probe(url):
            if (proc is not None and proc.poll() is not None) or time.monotonic() > deadline:
                logger.debug("web UI at %s never answered, not opening a browser", url)
                return False
            time.sleep(interval_s)
        if not opener(url):
            logger.debug("no browser available to open %s", url)
            return False
        console.print(f"[dim]Opening {url} in your browser[/dim]")
        return True
    except Exception:
        logger.debug("could not open %s in a browser", url, exc_info=True)
        return False


def open_when_ready(ui_port: int, proc: subprocess.Popen | None) -> None:
    """Open the UI root on localhost from a daemon thread, so the server never waits."""
    url = f"http://localhost:{ui_port}"
    threading.Thread(
        target=_wait_and_open, args=(url, proc), name="repowise-open-browser", daemon=True
    ).start()
