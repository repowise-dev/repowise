"""Shared CLI process setup helpers."""

from __future__ import annotations


def setup_logging_silence() -> None:
    """Quiet library + structlog output so progress bars are the only output.

    ``init`` / ``update`` render their own Rich progress bars; the underlying
    library loggers (httpx/httpcore) and the ``repowise.core`` / ``repowise.server``
    structlog loggers would otherwise interleave debug/info lines with the bars.

    ``cache_logger_on_first_use=False`` is load-bearing: modules that called
    ``structlog.get_logger(__name__)`` at import time hold a bound logger
    snapshotted before this ``configure`` runs — so without disabling the cache,
    debug lines from ``core/ingestion/*`` (graph, traverser, parser, …) leak past
    the ERROR filter on the first run of a session.
    """
    import logging

    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("httpcore").setLevel(logging.ERROR)
    for _logger_name in ("repowise.core", "repowise.server"):
        logging.getLogger(_logger_name).setLevel(logging.ERROR)

    try:
        import structlog

        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR),
            cache_logger_on_first_use=False,
        )
    except ImportError:
        pass
