"""Console streams, warnings, log silencing for machine output, and the async bridge."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from typing import Any

import click
from rich.console import Console

from repowise.cli.output import resolve_console_width
from repowise.cli.ui.brand import THEME
from repowise.cli.ui.glyphs import ascii_only


class _CliConsole(Console):
    """The CLI console: the brand theme, and ASCII where the terminal needs it.

    Stdout is reconfigured to UTF-8 at import, so Rich would always see UTF-8
    and draw Unicode boxes, bars and rules. Reporting the terminal's own
    encoding instead lets Rich pick its ASCII forms exactly when the glyph
    module does. A console pointed at another file keeps that file's encoding.
    Both streams follow stdout's encoding: one choice per run keeps them consistent.
    """

    @property
    def encoding(self) -> str:
        if self._file is None and ascii_only():
            return "ascii"
        return super().encoding


# Width is pinned only when the stream is not a terminal — see
# `output.resolve_console_width`. Without it rich renders a pipe at 80 columns
# and ellipsises the very paths an agent needs to act on.
console = _CliConsole(theme=THEME, width=resolve_console_width(sys.stdout))
err_console = _CliConsole(theme=THEME, stderr=True, width=resolve_console_width(sys.stderr))


def warn(text: str) -> None:
    """Print a warning to stderr with the shared yellow ``Warning:`` prefix.

    Every CLI warning funnels through this helper so warnings render on the
    same ``err_console`` stream (never stdout) and use one ``[yellow]Warning:[/yellow]``
    spelling instead of hand-synced copies scattered across commands.
    """
    from repowise.cli import helpers as _facade  # patch point for tests

    _facade.err_console.print(f"[yellow]Warning:[/yellow] {text}")

# ---------------------------------------------------------------------------
# Logging / structlog helpers
# ---------------------------------------------------------------------------

MACHINE_OUTPUT_LOGGER_NAMES = ("httpx", "httpcore", "repowise.core", "repowise.server")

@contextlib.contextmanager
def silence_logs_for_machine_output():
    """Suppress info/debug log output while stdout is machine-readable (JSON/md).

    Structlog and stdlib loggers write to stdout by default. When a command
    emits JSON or Markdown, those lines corrupt the output for downstream
    consumers (e.g. ``repowise health --format json | jq .kpis``).

    Context manager, not a bare call: logger levels and the structlog
    wrapper class are process-global with no other owner, so a caller that
    forgot to restore them would permanently silence its own process — the
    case that mattered in practice was a test session, where the mutation
    outlived the test that made it and broke unrelated caplog assertions
    later in the same run (see #1976).

    Use as:

        with silence_logs_for_machine_output():
            emit_json_or_markdown(...)
    """
    import logging

    loggers = [logging.getLogger(name) for name in MACHINE_OUTPUT_LOGGER_NAMES]
    previous_levels = [logger.level for logger in loggers]

    previous_structlog_config: dict[str, Any] | None = None
    try:
        import structlog

        previous_structlog_config = dict(structlog.get_config())
    except ImportError:
        pass

    try:
        for logger in loggers:
            logger.setLevel(logging.ERROR)
        if previous_structlog_config is not None:
            import structlog

            # cache_logger_on_first_use=False is required: module-level
            # ``structlog.get_logger`` calls snapshot the logger before
            # configure() runs and would bypass this filter without it.
            structlog.configure(
                wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR),
                cache_logger_on_first_use=False,
            )
        yield
    finally:
        for logger, level in zip(loggers, previous_levels, strict=True):
            logger.setLevel(level)
        if previous_structlog_config is not None:
            import structlog

            structlog.configure(**previous_structlog_config)

def silence_logs_for_machine_output_until_close() -> None:
    """Enter ``silence_logs_for_machine_output`` and restore it when the
    current click command finishes.

    ``silence_logs_for_machine_output`` is a context manager because it must
    always restore what it mutates — but not every call site has a single
    lexical block to wrap it around. An option callback (see the ``--format``
    and ``--json`` callbacks in ``output.py``) returns before the command body
    even starts running, so a ``with`` block there would restore the levels
    before the command does any work. Re-indenting an entire command
    function's body under one ``with`` is also a large, easy-to-get-wrong
    diff at call sites deep inside long functions.

    Solved the same way ``update_cmd`` already solves it for restoring
    ``console.file``: register the undo against click's context instead of a
    lexical scope, so it fires when the command finishes regardless of how
    much code runs in between or where the call sits.
    """
    from repowise.cli import helpers as _facade  # patch point for tests

    ctx = click.get_current_context()
    cm = _facade.silence_logs_for_machine_output()
    cm.__enter__()
    ctx.call_on_close(lambda: cm.__exit__(None, None, None))


# ---------------------------------------------------------------------------
# Async bridge
# ---------------------------------------------------------------------------


def run_async(coro: Any) -> Any:
    """Run an async coroutine from synchronous Click code.

    Each call is its own event loop, and a command runs one LLM provider
    through several of them. The provider's SDK client pools connections on
    the loop that opened them, so they are closed here, before that loop goes
    away: left pooled, they print ``Task exception was never retrieved ...
    Event loop is closed`` from a later step (issue #2946). With no such
    provider alive there is nothing to close.
    """
    from repowise.core.providers.llm.base import close_provider_clients

    async def _run() -> Any:
        try:
            return await coro
        finally:
            await close_provider_clients()

    return asyncio.run(_run())
