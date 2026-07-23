"""Regression tests for `repowise serve` port auto-fallback (issue #232).

When the user's project already occupies the default UI port (3000) or the
default API port (7337), `repowise serve` should pick the next available
port instead of crashing with EADDRINUSE.
"""

from __future__ import annotations

import os
import socket

import pytest

from repowise.cli.commands import serve_cmd


def _bind_listener(host: str = "127.0.0.1") -> tuple[socket.socket, int]:
    """Bind a listening socket on an OS-assigned port and return (sock, port)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def test_is_port_free_detects_free_port() -> None:
    sock, port = _bind_listener()
    sock.close()
    assert serve_cmd._is_port_free("127.0.0.1", port) is True


def test_is_port_free_detects_busy_port() -> None:
    sock, port = _bind_listener()
    try:
        assert serve_cmd._is_port_free("127.0.0.1", port) is False
    finally:
        sock.close()


def test_find_free_port_returns_preferred_when_free() -> None:
    sock, port = _bind_listener()
    sock.close()
    assert serve_cmd._find_free_port("127.0.0.1", port, "test") == port


def test_find_free_port_falls_back_to_next_available() -> None:
    """The exact scenario from issue #232: preferred port already taken."""
    sock, busy_port = _bind_listener()
    try:
        chosen = serve_cmd._find_free_port("127.0.0.1", busy_port, "web UI")
        assert chosen != busy_port
        assert chosen > busy_port
        # Chosen port must actually be bindable.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", chosen))
    finally:
        sock.close()


def test_find_free_port_handles_contiguous_busy_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the preferred port and the scan window are all busy, fall back
    to an OS-assigned ephemeral port instead of raising."""
    # Pretend every port in the scan range is busy; the function should still
    # produce a usable port via the wildcard bind path.
    monkeypatch.setattr(serve_cmd, "_is_port_free", lambda host, port: False)

    chosen = serve_cmd._find_free_port("127.0.0.1", 3000, "web UI", max_attempts=5)
    assert isinstance(chosen, int)
    assert 1024 <= chosen <= 65535


@pytest.mark.skipif(os.name == "nt", reason="SO_REUSEADDR probe fix is POSIX-only (issue #840)")
def test_is_port_free_ignores_time_wait() -> None:
    """Regression test for issue #840: TIME_WAIT sockets shouldn't block the probe."""
    host = "127.0.0.1"
    lis = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name != "nt":
        lis.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lis.bind((host, 0))
    port = lis.getsockname()[1]
    lis.listen(1)
    
    cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    cli.connect((host, port))
    conn, _ = lis.accept()
    
    # Active close from the server side pushes the listener's port into TIME_WAIT
    lis.close()
    conn.close()
    cli.close()
    
    # The probe should report it as free because it uses SO_REUSEADDR too
    assert serve_cmd._is_port_free(host, port) is True
