"""Doctor check that actually launches the MCP server and talks to it.

Registration checks answer "is there a config entry pointing somewhere that
exists". They cannot answer "does the server start", which is the failure the
field data is dominated by: a host restarting a server that dies on import
(``ModuleNotFoundError`` from a partial install, ``PermissionError`` on a
locked ``.repowise/``). Those installs pass every existing doctor check.

This launches the registered command, completes the MCP ``initialize`` round
trip, and reports what the process actually did - exit code and its own
stderr - when it does not come up. The caller resolves the registration, so
nothing here depends on where a registration is stored.

The handshake is hand-rolled rather than driven through the ``mcp`` SDK's
``stdio_client``, which was considered and does not fit: it writes the child's
stderr to a real file descriptor rather than handing it back, and that stderr
is the entire diagnostic here; and it rejects the whole stream on a line it
cannot parse, where a server that logs to stdout should still be reported as
working. Two JSON messages is less code than working around either.
"""

from __future__ import annotations

import collections
import contextlib
import json
import os
import subprocess
import threading
import time

from ._types import DoctorCheck, _check

# The server opens the store and builds the full-text index before it answers,
# so this is a startup budget, not a round-trip budget. It bounds how long
# doctor can be delayed by a server that hangs instead of exiting.
_SMOKE_TIMEOUT_S = 20.0

# Enough of the server's own stderr to carry a traceback's final frames, which
# is where an import or permission failure names itself.
_STDERR_TAIL_CHARS = 400

# The child's stderr is drained from the moment it starts, into a bounded ring.
# It cannot be left in the pipe until the round trip finishes: a pipe buffer is
# ~4KB on Windows, and the server routes every log sink to stderr and does its
# noisiest work - opening the store, building the full-text index - before it
# answers. One warning traceback is enough to fill the buffer and block the
# child forever in write(), which would report a perfectly healthy server as
# broken. Lines rather than bytes, so the tail never splits a character.
_STDERR_RING_LINES = 200

_TEARDOWN_ERRORS = (OSError, ValueError, subprocess.TimeoutExpired)

# A server that dies on import closes its stdout, so the read ends at EOF long
# before the process is reaped. Polling at that instant reports "still running"
# for a process that is already on its way out - which is the crash loop, the
# one case this check exists to name. Wait briefly for the exit status first.
_EXIT_GRACE_S = 5.0

CHECK_NAME = "MCP server responds"


def _read_response(stdout, request_id: int, deadline: float) -> dict | None:
    """Return the JSON-RPC response for *request_id*, or None if none arrives.

    Lines that are not JSON, or are JSON but not this response, are skipped: a
    server that logs to stdout is misbehaving but not broken, and skipping is
    what a real client does.
    """
    while time.monotonic() < deadline:
        line = stdout.readline()
        if not line:
            return None
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id:
            return message
    return None


def _initialize(proc: subprocess.Popen, deadline: float) -> dict | None:
    """Send ``initialize`` and return the response, or None if none arrives.

    The read runs on a worker thread because a blocking ``readline`` against a
    server that never answers cannot be interrupted portably, and Windows - where
    the crash loops this check exists for are concentrated - is exactly where the
    non-blocking pipe tricks do not work.
    """
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "repowise-doctor", "version": "1"},
        },
    }
    try:
        # Text-mode pipes translate "\n" to os.linesep, which would frame the
        # request with a trailing carriage return on Windows. Real clients send
        # a bare newline and a strict server is entitled to expect one.
        proc.stdin.reconfigure(newline="\n")
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        # The server died before it could read the request. The caller reports
        # its exit code and stderr, which say more than this write error does.
        return None

    holder: list[dict | None] = [None]

    def _reader() -> None:
        # Teardown can close stdout while this thread is still inside
        # readline(). Uncaught, that prints a thread traceback into the middle
        # of the doctor table; the timed-out result is already correct.
        with contextlib.suppress(*_TEARDOWN_ERRORS):
            holder[0] = _read_response(proc.stdout, 1, deadline)

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    thread.join(timeout=max(0.0, deadline - time.monotonic()))
    return holder[0]


def _drain_stderr(proc: subprocess.Popen) -> collections.deque:
    """Start draining the child's stderr immediately; return the ring it fills.

    Started before the handshake, not after, so the child can never block
    writing into a full pipe while we wait for its reply.
    """
    ring: collections.deque = collections.deque(maxlen=_STDERR_RING_LINES)

    def _drain() -> None:
        try:
            for line in proc.stderr:
                ring.append(line)
        except _TEARDOWN_ERRORS:
            pass

    threading.Thread(target=_drain, daemon=True).start()
    return ring


def _stderr_tail(ring: collections.deque) -> str:
    """The tail of the server's stderr, collapsed onto one line."""
    return " ".join("".join(ring).split())[-_STDERR_TAIL_CHARS:]


def _shutdown(proc: subprocess.Popen) -> None:
    """Stop the smoke-tested server and release its pipes."""
    if proc.poll() is None:
        proc.kill()
    with contextlib.suppress(*_TEARDOWN_ERRORS):
        proc.wait(timeout=5)
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            with contextlib.suppress(*_TEARDOWN_ERRORS):
                stream.close()


def mcp_smoke_check(command: str, args: list[str], env: dict | None = None) -> DoctorCheck:
    """Launch *command* as an MCP server and complete one ``initialize`` trip."""
    child_env = None
    if env:
        child_env = {**os.environ, **{str(k): str(v) for k, v in env.items()}}

    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
        )
    except OSError as exc:
        return _check(CHECK_NAME, False, f"could not launch {command}: {exc}")

    stderr_ring = _drain_stderr(proc)
    try:
        response = _initialize(proc, started + _SMOKE_TIMEOUT_S)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        if response is not None and "result" in response:
            server = response["result"].get("serverInfo") or {}
            return _check(
                CHECK_NAME, True, f"{server.get('name', 'repowise')} initialised in {elapsed_ms}ms"
            )
        if response is not None:
            error = response.get("error") or {}
            return _check(
                CHECK_NAME, False, f"initialize failed: {error.get('message', response)}"
            )

        # No response. Whether the process is gone (the crash-loop case) or up
        # and mute changes the remedy, so the detail says which.
        exit_code = proc.poll()
        if exit_code is None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                exit_code = proc.wait(timeout=_EXIT_GRACE_S)
        if exit_code is None:
            return _check(
                CHECK_NAME,
                False,
                f"no initialize response within {int(_SMOKE_TIMEOUT_S)}s (still running)",
            )
        detail = f"server exited with code {exit_code}"
        stderr = _stderr_tail(stderr_ring)
        if stderr:
            detail += f": {stderr}"
        return _check(CHECK_NAME, False, detail)
    finally:
        _shutdown(proc)
