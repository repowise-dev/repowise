"""Keep log output off the stdio transport's protocol channel.

On a stdio transport, **stdout is the JSON-RPC channel**. Anything else
written there is framed as a protocol message by the client, which then fails
to parse it:

    Failed to parse JSONRPC message from server ...
    Invalid JSON: trailing characters ... input_value='2026-08-01 20:12:13 [deb...'

structlog's default logger factory prints to ``sys.stdout``, so every debug
line the answer path emits during synthesis lands in the middle of the
protocol stream: three malformed frames per ``get_answer`` call, reproducibly,
on both repos of the 2026-08 bake-off.

The Python ``mcp`` client logs those frames and drops them, so from the outside
nothing looks broken. That is what makes this worth fixing rather than
tolerating: a stricter JSON-RPC parser is entitled to close the connection on a
malformed frame, and a dropped frame that happened to carry a real response
leaves the call hanging with no error. stdio is the default transport every
editor uses.

Rich's console output already goes to stderr; this closes the structlog and
stdlib-``logging`` halves.
"""

from __future__ import annotations

import logging
import sys


def route_logging_to_stderr() -> None:
    """Point every log sink at stderr, leaving stdout to the protocol.

    Idempotent and safe to call before any logging happens. Never raises: a
    server that cannot reconfigure its logging should still start.
    """
    _redirect_stdlib_handlers()
    _redirect_structlog()


def _redirect_stdlib_handlers() -> None:
    """Repoint any ``StreamHandler`` currently writing to stdout at stderr.

    Handlers are rebound rather than removed so a caller that deliberately
    installed one keeps its formatter and level; only the destination moves.
    Loggers with no handler at all already fall back to ``logging.lastResort``,
    which writes to stderr.
    """
    manager_loggers = list(logging.Logger.manager.loggerDict.values())
    for obj in [logging.getLogger(), *manager_loggers]:
        for handler in list(getattr(obj, "handlers", ()) or ()):
            if getattr(handler, "stream", None) is sys.stdout:
                handler.setStream(sys.stderr)


def _redirect_structlog() -> None:
    """Make structlog print to stderr.

    ``cache_logger_on_first_use=False`` is load-bearing: modules that called
    ``structlog.get_logger(__name__)`` at import time hold a logger bound
    before this runs, and with caching left on their lines would keep going to
    stdout. The same flag is set for the same reason in the CLI's
    ``configure_cli_logging``.
    """
    try:
        import structlog
    except ImportError:  # pragma: no cover - structlog is a hard dependency
        return
    structlog.configure(
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
