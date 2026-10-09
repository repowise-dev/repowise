"""A session that ends must say which way it ended.

The four ways are not interchangeable: a client closing a transport it owns is
an ordinary end, a client process dying is a different one, stray stdout is a
protocol hazard, and only the fourth is this server's fault. Before these,
every one of them was either a traceback the host respawns on or no line at
all.
"""

from __future__ import annotations

import logging
import sys

import anyio
import pytest

from repowise.server.mcp_server import _server, _transport
from repowise.server.mcp_server._transport import (
    CLIENT_CLOSED,
    CLIENT_GONE,
    PROTOCOL_CORRUPTED,
    SERVER_FAULT,
    SERVER_STOPPED,
    classify_termination,
    guard_stdout,
    is_client_closure,
    log_outcome,
)


@pytest.fixture
def no_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "repowise.server.mcp_server._watchdog.start_parent_watchdog", lambda: None
    )


@pytest.mark.parametrize(
    "exc",
    [
        BrokenPipeError("the client hung up"),
        ConnectionResetError("reset by peer"),
        EOFError("stdin at EOF"),
        anyio.EndOfStream(),
        anyio.ClosedResourceError(),
        anyio.BrokenResourceError(),
    ],
)
def test_a_hang_up_is_not_a_fault(exc: BaseException) -> None:
    assert is_client_closure(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        OSError("address already in use"),
        PermissionError("wiki.db is locked"),
        RuntimeError("bad state"),
        ModuleNotFoundError("no mcp"),
    ],
)
def test_a_fault_is_not_a_hang_up(exc: BaseException) -> None:
    """OSError shares a base class with a broken pipe and must not match."""
    assert is_client_closure(exc) is False


def test_a_clean_stdio_return_is_the_client_closing_the_transport() -> None:
    assert classify_termination("stdio", None) == CLIENT_CLOSED


def test_a_clean_network_return_is_this_server_stopping() -> None:
    """Nobody hung up on an HTTP server that returned; it shut down."""
    assert classify_termination("streamable-http", None) == SERVER_STOPPED


def test_stray_stdout_names_itself() -> None:
    assert classify_termination("stdio", None, stray_writes=2) == PROTOCOL_CORRUPTED


def test_a_group_of_hang_ups_is_still_a_hang_up() -> None:
    group = ExceptionGroup("closed", [BrokenPipeError(), anyio.ClosedResourceError()])

    assert (
        classify_termination("stdio", group, leaves=_server.group_leaves)
        == CLIENT_CLOSED
    )


def test_one_real_fault_beside_a_hang_up_is_a_fault() -> None:
    """A crash that also broke the pipe is a crash, not a clean close."""
    group = ExceptionGroup("mixed", [BrokenPipeError(), PermissionError("locked")])

    assert (
        classify_termination("stdio", group, leaves=_server.group_leaves)
        == SERVER_FAULT
    )


def test_a_clean_outcome_reaches_stderr_with_no_logging_configured(capsys) -> None:
    """The regression a level-forcing test hid.

    ``repowise mcp`` configures no logging, so an INFO record fell through to
    logging's last-resort sink, which is WARNING-only: the fault line printed
    and the clean ones printed nothing at all. Asserting at the default level
    is the whole point — do not add ``caplog.at_level`` here.
    """
    _transport._log.handlers.clear()
    _transport._log.propagate = True
    try:
        log_outcome(CLIENT_CLOSED, "stdio")
    finally:
        _transport._log.handlers.clear()
        _transport._log.propagate = True

    assert "client_closed" in capsys.readouterr().err


def test_the_sink_is_attached_once(capsys) -> None:
    """One line per process; a handler per call would multiply it."""
    _transport._log.handlers.clear()
    _transport._log.propagate = True
    try:
        log_outcome(CLIENT_CLOSED, "stdio")
        log_outcome(SERVER_STOPPED, "sse")
        handlers = len(_transport._log.handlers)
    finally:
        _transport._log.handlers.clear()
        _transport._log.propagate = True

    assert handlers == 1
    assert capsys.readouterr().err.count("MCP session") == 2


def test_a_fault_is_logged_at_error_and_a_close_is_not() -> None:
    """A client hanging up is not a problem; only a fault is.

    Read off this module's own logger rather than caplog: the sink sets
    ``propagate = False``, so records never reach the root handler caplog
    attaches to. That is deliberate — see :func:`_ensure_outcome_sink`.
    """
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    _transport._log.handlers.clear()
    _transport._log.addHandler(_Capture())
    _transport._log.setLevel(logging.INFO)
    try:
        log_outcome(CLIENT_CLOSED, "stdio")
        log_outcome(SERVER_FAULT, "stdio", "PermissionError")
    finally:
        _transport._log.handlers.clear()
        _transport._log.propagate = True

    levels = {
        record.getMessage().split("ended: ")[1].split()[0]: record.levelno
        for record in records
    }
    assert levels["client_closed"] == logging.INFO
    assert levels["server_fault"] == logging.ERROR


# --- the stdout guard ------------------------------------------------------


def test_a_stray_print_goes_to_stderr_and_is_counted(capsys) -> None:
    """stdout carries JSON-RPC frames; a print would land inside one."""
    with guard_stdout() as guard:
        print("this would have mangled a frame")

    captured = capsys.readouterr()
    assert "mangled a frame" not in captured.out
    assert "mangled a frame" in captured.err
    assert guard.writes >= 1


def test_the_protocol_channel_itself_is_untouched() -> None:
    """The SDK writes frames through sys.stdout.buffer, which must be real."""
    real = sys.stdout
    with guard_stdout() as guard:
        assert sys.stdout is guard
        assert guard.buffer is real.buffer
    assert sys.stdout is real


def test_the_real_stream_comes_back_after_a_failure() -> None:
    real = sys.stdout
    with pytest.raises(RuntimeError), guard_stdout():
        raise RuntimeError("boom")

    assert sys.stdout is real


# --- run_mcp ---------------------------------------------------------------


def test_a_closed_transport_returns_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, no_watchdog: None
) -> None:
    """The regression: an ordinary hang-up escaped as a traceback."""
    def hang_up(**_kw):
        raise BrokenPipeError("the client hung up")

    monkeypatch.setattr(_server.mcp, "run", hang_up)

    assert _server.run_mcp(transport="stdio") == CLIENT_CLOSED


def test_a_grouped_closed_transport_returns_too(
    monkeypatch: pytest.MonkeyPatch, no_watchdog: None
) -> None:
    def hang_up(**_kw):
        raise ExceptionGroup("closed", [anyio.ClosedResourceError()])

    monkeypatch.setattr(_server.mcp, "run", hang_up)

    assert _server.run_mcp(transport="stdio") == CLIENT_CLOSED


def test_a_server_fault_still_raises(
    monkeypatch: pytest.MonkeyPatch, no_watchdog: None
) -> None:
    """Classification must not turn a real crash into a quiet exit."""
    def boom(**_kw):
        raise ExceptionGroup("task failed", [PermissionError("wiki.db is locked")])

    monkeypatch.setattr(_server.mcp, "run", boom)

    with pytest.raises(PermissionError):
        _server.run_mcp(transport="stdio")


def test_a_clean_stdio_session_names_its_ending(
    monkeypatch: pytest.MonkeyPatch, no_watchdog: None
) -> None:
    monkeypatch.setattr(_server.mcp, "run", lambda **_kw: None)

    assert _server.run_mcp(transport="stdio") == CLIENT_CLOSED


def test_a_session_that_printed_to_stdout_says_so(
    monkeypatch: pytest.MonkeyPatch, no_watchdog: None
) -> None:
    monkeypatch.setattr(_server.mcp, "run", lambda **_kw: print("stray"))

    assert _server.run_mcp(transport="stdio") == PROTOCOL_CORRUPTED


def test_a_network_transport_reports_its_own_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_server.mcp, "run", lambda **_kw: None)

    assert _server.run_mcp(transport="streamable-http") == SERVER_STOPPED


def test_a_network_transport_never_reports_a_closure() -> None:
    """An HTTP server holds no pipe a particular client owns."""
    assert classify_termination("streamable-http", BrokenPipeError()) == SERVER_FAULT


def test_a_startup_fault_is_never_read_as_a_clean_close(
    monkeypatch: pytest.MonkeyPatch, no_watchdog: None
) -> None:
    """A truncated index artifact raises EOFError, which is a hang-up class.

    Classifying it as one reported a broken index as an ordinary session and
    exited 0, which is the failure this whole module exists to prevent.
    """
    def boom():
        raise EOFError("compressed file ended before the end-of-stream marker")

    monkeypatch.setattr("repowise.server.mcp_server.ensure_full_surface", boom)

    with pytest.raises(EOFError):
        _server.run_mcp(transport="stdio")


def test_a_grouped_startup_fault_is_not_a_closure_either(
    monkeypatch: pytest.MonkeyPatch, no_watchdog: None
) -> None:
    def boom():
        raise ExceptionGroup("startup", [EOFError("truncated artifact")])

    monkeypatch.setattr("repowise.server.mcp_server.ensure_full_surface", boom)

    with pytest.raises(EOFError):
        _server.run_mcp(transport="stdio")


def test_an_interrupt_is_not_a_closure(
    monkeypatch: pytest.MonkeyPatch, no_watchdog: None
) -> None:
    """Ctrl-C ends a server; it is not the client hanging up."""
    monkeypatch.setattr(
        _server.mcp, "run", lambda **_kw: (_ for _ in ()).throw(KeyboardInterrupt())
    )

    with pytest.raises(KeyboardInterrupt):
        _server.run_mcp(transport="stdio")


def test_an_unopenable_store_is_not_a_closure(
    monkeypatch: pytest.MonkeyPatch, no_watchdog: None
) -> None:
    monkeypatch.setattr(
        _server.mcp,
        "run",
        lambda **_kw: (_ for _ in ()).throw(_server.StoreUnavailableError("locked")),
    )

    with pytest.raises(_server.StoreUnavailableError):
        _server.run_mcp(transport="stdio")


def test_the_watchdog_speaks_the_shared_vocabulary(capsys, monkeypatch) -> None:
    """A dead client and a dead server must be one grep apart."""
    from repowise.server.mcp_server import _watchdog

    _transport._log.handlers.clear()
    _transport._log.propagate = True
    try:
        _watchdog.log_outcome(CLIENT_GONE, "stdio", "ancestor code(123) is gone")
    finally:
        _transport._log.handlers.clear()
        _transport._log.propagate = True

    err = capsys.readouterr().err
    assert "client_gone" in err
    assert "code(123)" in err


def test_a_session_that_printed_and_then_hung_up_reports_the_hang_up(
    monkeypatch: pytest.MonkeyPatch, no_watchdog: None
) -> None:
    """How it ended outranks what happened during it; the guard still warns."""
    def print_then_hang_up(**_kw):
        print("stray")
        raise BrokenPipeError("hung up")

    monkeypatch.setattr(_server.mcp, "run", print_then_hang_up)

    assert _server.run_mcp(transport="stdio") == CLIENT_CLOSED


def test_the_guard_says_stdout_is_writable() -> None:
    """io.TextIOBase defaults writable() to False, which would read as a
    read-only stdout to anything that checks before writing."""
    with guard_stdout() as guard:
        assert guard.writable() is True
        assert sys.stdout.writable() is True
