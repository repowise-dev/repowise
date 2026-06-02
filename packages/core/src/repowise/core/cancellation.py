"""Cooperative cancellation for long synchronous pipeline phases.

Heavy CPU phases (duplication detection, graph centrality, parsing) run inside
``asyncio.to_thread`` workers. A bare Ctrl-C cannot interrupt such a worker —
the event loop's KeyboardInterrupt frees the main thread, but the non-daemon
worker keeps grinding, so the process never actually exits. That is the second
half of issue #341: "Ctrl-C didn't return the terminal."

The fix is cooperative: a process-global :class:`CancellationToken` is set by
the CLI for the duration of a run, the long synchronous loops poll
:func:`check_cancelled` at coarse intervals, and the first Ctrl-C flips the
token so those loops unwind promptly with :class:`PipelineCancelled`.

:class:`PipelineCancelled` deliberately subclasses :class:`BaseException` (like
:class:`KeyboardInterrupt` and ``asyncio.CancelledError``) so the pipeline's
many ``except Exception`` guards — e.g. the per-phase wrappers that downgrade a
failure to "phase skipped" — never swallow a cancellation. It always
propagates to the top, where the CLI turns it into a clean "run --resume" exit.
"""

from __future__ import annotations

import contextlib
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import structlog

logger = structlog.get_logger(__name__)


class PipelineCancelled(BaseException):
    """Raised by :func:`check_cancelled` when cancellation was requested."""


class CancellationToken:
    """A thread-safe one-way "please stop" flag.

    Backed by :class:`threading.Event` so it can be flipped from a signal
    handler on the main thread and observed from ``asyncio.to_thread`` workers.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


# Process-global active token. ``None`` means cancellation is not armed, so
# check_cancelled() is a no-op — the default everywhere except inside an active
# cancellation_scope(). A plain module global (not a ContextVar) is intentional:
# to_thread workers must see the same token the main thread armed.
_active_token: CancellationToken | None = None


def set_active_token(token: CancellationToken | None) -> None:
    global _active_token
    _active_token = token


def get_active_token() -> CancellationToken | None:
    return _active_token


def check_cancelled() -> None:
    """Raise :class:`PipelineCancelled` if the active token has been flipped.

    Cheap enough to call inside hot loops at a coarse cadence (once per file,
    once per hash bucket). A no-op when no token is armed.
    """
    token = _active_token
    if token is not None and token.cancelled:
        raise PipelineCancelled()


@contextmanager
def cancellation_scope(
    on_first_interrupt: Callable[[], None] | None = None,
) -> Iterator[CancellationToken]:
    """Arm a cancellation token + a two-stage SIGINT handler for the block.

    First Ctrl-C flips the token (long synchronous phases polling
    :func:`check_cancelled` unwind cleanly) and fires ``on_first_interrupt`` if
    given. A second Ctrl-C restores the previous handler and re-raises
    ``KeyboardInterrupt`` to force-quit, so an impatient user is never trapped.

    The previous token and signal handler are restored on exit. When not on the
    main thread (signal handlers can only be installed there) the token is still
    armed — callers may flip it manually — but no handler is installed.
    """
    token = CancellationToken()
    prev_token = _active_token
    set_active_token(token)

    presses = {"count": 0}
    installed = False
    prev_handler: object = None

    def _handler(signum: int, frame: object) -> None:
        presses["count"] += 1
        if presses["count"] >= 2:
            # Second interrupt — hand control back to the default behaviour.
            with contextlib.suppress(ValueError, OSError, TypeError):
                signal.signal(signal.SIGINT, prev_handler or signal.default_int_handler)  # type: ignore[arg-type]
            raise KeyboardInterrupt
        token.cancel()
        if on_first_interrupt is not None:
            # A notifier hiccup must not mask the interrupt.
            with contextlib.suppress(Exception):
                on_first_interrupt()

    try:
        try:
            prev_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, _handler)
            installed = True
        except (ValueError, OSError):
            # Not the main thread — proceed with the token armed but unsignaled.
            installed = False
        yield token
    finally:
        if installed:
            with contextlib.suppress(ValueError, OSError, TypeError):
                signal.signal(signal.SIGINT, prev_handler)  # type: ignore[arg-type]
        set_active_token(prev_token)
