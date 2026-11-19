"""Periodic "newer release available" advisory while ``serve`` runs."""

from __future__ import annotations

from repowise.cli.helpers import console

#: How often a running ``serve`` re-reads release currency. The PyPI fetch is
#: TTL-cached on disk for a day, so this bounds the print, not the network.
_UPDATE_ADVISORY_INTERVAL_S = 6 * 3600


def _update_advisory_tick(announced: set[str]) -> bool:
    """Print the advisory for a release not yet announced. Never raises."""
    try:
        from repowise.cli.update_check import get_cli_update_check_cached
        from repowise.cli.whats_new import render_update_advisory

        check = get_cli_update_check_cached()
        if not check.update_available or check.latest_version in announced:
            return False
        if render_update_advisory(console, check):
            announced.add(check.latest_version)
            return True
    except Exception:
        pass
    return False


def _start_update_advisory_loop(announced: set[str]) -> None:
    """Re-run the advisory tick on a daemon thread; it dies with the process."""
    import threading
    import time

    def _loop() -> None:
        while True:
            time.sleep(_UPDATE_ADVISORY_INTERVAL_S)
            _update_advisory_tick(announced)

    threading.Thread(target=_loop, name="update-advisory", daemon=True).start()


def _start_update_advisory() -> None:
    """Announce now, then once per interval; interactive terminals only."""
    if not console.is_terminal:
        return
    announced: set[str] = set()
    _update_advisory_tick(announced)
    _start_update_advisory_loop(announced)
