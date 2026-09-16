"""How an MCP session ended, named rather than left to look like success.

A stdio server is spawned per session by the client that owns the pipe, and
the ordinary end of one is that client hanging up. It arrives as EOF, a broken
pipe, or a cancelled task, and until this module existed all three were
indistinguishable from a server that had crashed: the hang-up escaped as a
traceback the host respawns on, and a clean end produced no line at all. The
outcomes here are what a probe needs to tell a closure the client chose from a
fault this server is answerable for.

Nothing here reconnects anything. A stdio transport belongs to the client that
spawned the process, so a server that "reconnected" would be a second server
writing down a pipe nobody reads. Retry-once belongs in a client adapter, and
only where Repowise owns that adapter.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import sys
from collections.abc import Iterator
from typing import Any, Protocol

_log = logging.getLogger(__name__)

#: The client closed a transport it owns: stdin at EOF, a pipe broken from the
#: far end, or the run cancelled under us. Not a fault, and not silence either.
CLIENT_CLOSED = "client_closed"

#: The watchdog saw the client process itself die, so nothing will ever read
#: what we write. Distinct from CLIENT_CLOSED: nobody hung up, the far end
#: stopped existing.
CLIENT_GONE = "client_gone"

#: Something in this process wrote to stdout beside the JSON-RPC frames. The
#: guard below moves it to stderr, so this names a session whose protocol
#: channel was written to, not one whose frames were definitely mangled.
PROTOCOL_CORRUPTED = "protocol_corrupted"

#: A network transport's own run returned. Nobody hung up on us; we stopped.
SERVER_STOPPED = "server_stopped"

#: Anything else that ended the run. The only outcome that is this server's
#: fault, and the only one that exits non-zero.
SERVER_FAULT = "server_fault"


def client_closure_types() -> tuple[type[BaseException], ...]:
    """Exception classes that mean the far end of the transport went away.

    Public because an ``except`` clause naming exactly these is how the caller
    catches a hang-up without also catching an interrupt or a fault.

    Resolved per call rather than at import: anyio's cancelled class is
    backend-dependent, and importing it at module scope would pull anyio into
    every process that merely imports the server package.

    Deliberately narrow. ``OSError`` itself is not here — "address in use" is a
    server fault and shares that base class with a broken pipe.
    """
    types: list[type[BaseException]] = [
        BrokenPipeError,
        ConnectionResetError,
        EOFError,
        # A cancelled run is how a client-initiated shutdown arrives. The
        # asyncio class is named outright because the lookup below only works
        # from inside a running loop, and by the time anything classifies a
        # termination the loop has already unwound.
        asyncio.CancelledError,
    ]
    with contextlib.suppress(Exception):
        import anyio

        types += [
            anyio.EndOfStream,
            anyio.ClosedResourceError,
            anyio.BrokenResourceError,
        ]
    # Separate from the block above on purpose: outside an event loop this
    # raises, and sharing one suppress silently discarded every class before
    # it.
    with contextlib.suppress(Exception):
        import anyio

        types.append(anyio.get_cancelled_exc_class())
    return tuple(types)


def is_client_closure(exc: BaseException) -> bool:
    """Whether *exc* is the far end hanging up rather than a fault here."""
    return isinstance(exc, client_closure_types())


class _LeafReader(Protocol):
    def __call__(self, exc: BaseException) -> list[BaseException]: ...


def classify_termination(
    transport: str,
    error: BaseException | None,
    *,
    stray_writes: int = 0,
    leaves: _LeafReader | None = None,
) -> str:
    """Name the outcome of one session.

    *error* is what ended the run, or ``None`` when it returned on its own.
    *leaves* unwraps a task group; a group counts as a closure only when every
    leaf does, because one genuine fault beside a hang-up is still a fault.
    """
    if error is not None:
        found = leaves(error) if leaves is not None else [error]
        return CLIENT_CLOSED if all(map(is_client_closure, found)) else SERVER_FAULT
    if stray_writes:
        return PROTOCOL_CORRUPTED
    # stdin reaching EOF is how a stdio client says the session is over; it is
    # the only way that run returns without an error.
    return CLIENT_CLOSED if transport == "stdio" else SERVER_STOPPED


def log_outcome(outcome: str, transport: str, detail: str = "") -> None:
    """Emit the one line that says how a session ended, on stderr.

    Every outcome is logged, including the clean ones. A stdio session that
    ends without a word is the case this exists to remove: it reads as a
    server that answered nothing, which is also what a crash looks like.
    """
    tail = f" ({detail})" if detail else ""
    level = logging.ERROR if outcome == SERVER_FAULT else logging.INFO
    if outcome == PROTOCOL_CORRUPTED:
        level = logging.WARNING
    _log.log(level, "MCP session (%s) ended: %s%s", transport, outcome, tail)


class _StrayStdout(io.TextIOBase):
    """Stands in for ``sys.stdout`` while a stdio session runs.

    The MCP SDK re-wraps ``sys.stdout.buffer`` in its own text layer and writes
    every frame through that, so by the time a write reaches *this* object it
    is by definition not the protocol: it is a ``print`` that would interleave
    with the frames and mangle whichever one it landed inside. Forward it to
    stderr, where it is merely noise, and count it.

    ``buffer`` is the real one, which is what keeps the SDK writing frames to
    the client rather than into stderr with everything else.

    :class:`io.TextIOBase` supplies the rest of the text-stream protocol —
    ``writelines``, ``writable``, ``closed`` and the no-op close — so only what
    actually differs from a text stream is written out here.
    """

    def __init__(self, real: Any, stderr: Any) -> None:
        super().__init__()
        self._real = real
        self._stderr = stderr
        self.writes = 0

    @property
    def buffer(self) -> Any:
        return self._real.buffer

    @property
    def encoding(self) -> str:
        return getattr(self._real, "encoding", "utf-8")

    def write(self, data: str) -> int:
        if data:
            self.writes += 1
        return self._stderr.write(data)

    def flush(self) -> None:
        with contextlib.suppress(ValueError):
            self._stderr.flush()

    def fileno(self) -> int:
        return self._real.fileno()

    def isatty(self) -> bool:
        return False


@contextlib.contextmanager
def guard_stdout() -> Iterator[_StrayStdout]:
    """Keep stray ``print`` output off the JSON-RPC channel for the duration.

    Restores the real stream on the way out, including when the body raises,
    so a fault does not leave the process with a swapped stdout.
    """
    real = sys.stdout
    guard = _StrayStdout(real, sys.stderr)
    sys.stdout = guard  # type: ignore[assignment]
    try:
        yield guard
    finally:
        sys.stdout = real
        if guard.writes:
            _log.warning(
                "MCP stdio: %d write(s) to stdout were moved to stderr; stdout "
                "carries JSON-RPC frames and nothing else may write there.",
                guard.writes,
            )
