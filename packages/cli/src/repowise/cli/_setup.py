"""Shared CLI process setup helpers."""

from __future__ import annotations


def configure_cli_logging(*, verbose: bool = False) -> None:
    """Set library + structlog verbosity for a progress-bar command.

    ``init`` / ``update`` render their own Rich progress bars. By default the
    underlying library loggers (httpx/httpcore) and the ``repowise.core`` /
    ``repowise.server`` structlog loggers are quieted to ERROR so they don't
    interleave debug/info lines with the bars. ``verbose=True`` lets the
    repowise loggers through at DEBUG so a user can see provider/pipeline
    activity (``repowise update -v``); httpx/httpcore stay quiet either way,
    since their debug stream is HTTP-level noise that buries the useful lines.

    ``cache_logger_on_first_use=False`` is load-bearing: modules that called
    ``structlog.get_logger(__name__)`` at import time hold a bound logger
    snapshotted before this ``configure`` runs, so without disabling the cache,
    debug lines from ``core/ingestion/*`` (graph, traverser, parser, ...) leak
    past the filter on the first run of a session.
    """
    import logging

    level = logging.DEBUG if verbose else logging.ERROR

    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("httpcore").setLevel(logging.ERROR)
    for _logger_name in ("repowise.core", "repowise.server"):
        logging.getLogger(_logger_name).setLevel(level)

    try:
        import structlog

        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(level),
            cache_logger_on_first_use=False,
        )
    except ImportError:
        pass


def setup_logging_silence() -> None:
    """Quiet library + structlog output so progress bars are the only output.

    Thin wrapper over :func:`configure_cli_logging` for the quiet (default)
    path, kept for the call sites that never want logs regardless of a verbose
    flag (e.g. the multi-repo workspace flow).
    """
    configure_cli_logging(verbose=False)
