"""Logging inside spawned worker processes."""

from __future__ import annotations

import logging


def quiet_worker_logs() -> None:
    """Quiet a worker's log lines below WARNING. Call once at worker start.

    A spawn child starts with structlog's defaults, which print every level to
    stdout. The parent CLI's filter does not cross the process boundary, so a
    worker's debug lines landed in the middle of the parent's progress display.

    WARNING-and-above are kept: at ERROR, worker-emitted warnings such as
    ``parser.symbol_bloat`` (issue #4482) were dropped entirely and never
    reached the user. Kept lines print to stderr so they cross the process
    boundary without corrupting the parent's stdout progress bars.
    """
    import sys

    import structlog

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
