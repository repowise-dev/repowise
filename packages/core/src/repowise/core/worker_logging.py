"""Logging inside spawned worker processes."""

from __future__ import annotations

import logging


def quiet_worker_logs() -> None:
    """Drop a worker's log lines below ERROR. Call once at worker start.

    A spawn child starts with structlog's defaults, which print every level to
    stdout. The parent CLI's filter does not cross the process boundary, so a
    worker's debug lines landed in the middle of the parent's progress display.
    """
    import structlog

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR),
        cache_logger_on_first_use=False,
    )
