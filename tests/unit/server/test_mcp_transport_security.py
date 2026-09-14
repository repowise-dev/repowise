"""Tests for the MCP HTTP/SSE transport_security host allowlist.

``repowise mcp --transport streamable-http --host <non-loopback>`` failed
every request with ``421 Misdirected Request`` / ``Invalid Host header``,
regardless of the ``--host`` value given. ``FastMCP`` builds
``mcp.settings.transport_security`` at import time, before any CLI ``--host``
is known, so it bakes in a DNS-rebinding ``Host`` allowlist scoped to
loopback only; ``run_mcp`` later rebinds the socket via ``mcp.settings.host``
but never updated the allowlist to match.

``_configure_transport_security`` rebuilds the allowlist around the actual
bind host (or disables the now-unsatisfiable check for a wildcard bind).
"""

from __future__ import annotations

import pytest
from mcp.server.transport_security import TransportSecurityMiddleware

from repowise.server.mcp_server import _server


@pytest.fixture(autouse=True)
def _restore_transport_security():
    """Isolate mcp.settings.transport_security across tests."""
    original = _server.mcp.settings.transport_security
    yield
    _server.mcp.settings.transport_security = original


def test_loopback_host_is_left_alone() -> None:
    """127.0.0.1/localhost/::1 already work under FastMCP's default — no-op."""
    before = _server.mcp.settings.transport_security

    for host in ("127.0.0.1", "localhost", "::1"):
        _server.mcp.settings.transport_security = before
        _server._configure_transport_security(host)
        assert _server.mcp.settings.transport_security is before


def test_wildcard_bind_disables_the_host_check() -> None:
    """0.0.0.0/:: can't be matched by any single Host value, so it's disabled."""
    for host in ("0.0.0.0", "::"):
        _server._configure_transport_security(host)
        settings = _server.mcp.settings.transport_security
        assert settings.enable_dns_rebinding_protection is False


def test_lan_host_gets_widened_to_that_host_plus_loopback() -> None:
    _server._configure_transport_security("172.21.12.48")
    settings = _server.mcp.settings.transport_security

    assert settings.enable_dns_rebinding_protection is True
    assert "172.21.12.48:*" in settings.allowed_hosts
    assert "127.0.0.1:*" in settings.allowed_hosts
    assert "localhost:*" in settings.allowed_hosts
    assert "[::1]:*" in settings.allowed_hosts
    assert "http://172.21.12.48:*" in settings.allowed_origins


def test_hostname_bind_gets_widened_too() -> None:
    _server._configure_transport_security("my-machine.lan")
    settings = _server.mcp.settings.transport_security

    assert "my-machine.lan:*" in settings.allowed_hosts
    assert "http://my-machine.lan:*" in settings.allowed_origins


def test_ipv6_host_gets_bracketed_in_the_allowlist() -> None:
    """A bare IPv6 --host must be bracketed to match the Host header shape a
    client actually sends (``[2001:db8::1]:7338``, not ``2001:db8::1:7338``).
    """
    _server._configure_transport_security("2001:db8::1")
    settings = _server.mcp.settings.transport_security

    assert settings.enable_dns_rebinding_protection is True
    assert "[2001:db8::1]:*" in settings.allowed_hosts
    assert "http://[2001:db8::1]:*" in settings.allowed_origins


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("172.21.12.48", "172.21.12.48"),
        ("my-machine.lan", "my-machine.lan"),
        ("2001:db8::1", "[2001:db8::1]"),
        ("[2001:db8::1]", "[2001:db8::1]"),  # already bracketed — left alone
        ("::1", "[::1]"),
    ],
)
def test_bracket_if_ipv6(host: str, expected: str) -> None:
    assert _server._bracket_if_ipv6(host) == expected


@pytest.mark.parametrize(
    ("header_host", "expected"),
    [
        ("172.21.12.48:7338", True),
        ("localhost:7338", True),
        ("127.0.0.1:7338", True),
        ("172.21.12.48", False),  # bare host, no port — not in the allowlist
        ("evil.com:7338", False),
    ],
)
def test_real_middleware_validates_reported_host_header(header_host: str, expected: bool) -> None:
    """Exercise the actual SDK middleware, not just our settings object."""
    _server._configure_transport_security("172.21.12.48")
    middleware = TransportSecurityMiddleware(_server.mcp.settings.transport_security)

    assert middleware._validate_host(header_host) is expected


@pytest.mark.parametrize(
    ("header_host", "expected"),
    [
        ("[2001:db8::1]:7338", True),  # bracketed, as a real client sends it
        ("2001:db8::1:7338", False),  # unbracketed — not the wire shape, must not match
        ("[::1]:7338", True),  # loopback pattern still allowed alongside it
        ("[2001:db8::2]:7338", False),  # a different IPv6 host
    ],
)
def test_real_middleware_validates_ipv6_host_header(header_host: str, expected: bool) -> None:
    """The gap a prior review caught: an unbracketed allowlist entry never
    matches the bracketed Host header a client sends for an IPv6 literal.
    """
    _server._configure_transport_security("2001:db8::1")
    middleware = TransportSecurityMiddleware(_server.mcp.settings.transport_security)

    assert middleware._validate_host(header_host) is expected


def test_run_mcp_applies_transport_security_for_streamable_http(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(_server.mcp, "run", lambda **kw: calls.append(kw))
    monkeypatch.delenv("REPOWISE_API_KEY", raising=False)

    _server.run_mcp(transport="streamable-http", host="172.21.12.48", port=7338)

    settings = _server.mcp.settings.transport_security
    assert settings.enable_dns_rebinding_protection is True
    assert "172.21.12.48:*" in settings.allowed_hosts


def test_run_mcp_applies_transport_security_for_sse(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(_server.mcp, "run", lambda **kw: calls.append(kw))
    monkeypatch.delenv("REPOWISE_API_KEY", raising=False)

    _server.run_mcp(transport="sse", host="172.21.12.48", port=7338)

    settings = _server.mcp.settings.transport_security
    assert settings.enable_dns_rebinding_protection is True
    assert "172.21.12.48:*" in settings.allowed_hosts


def test_run_mcp_stdio_does_not_touch_transport_security(monkeypatch) -> None:
    """stdio never binds a socket — there's nothing for the allowlist to gate."""
    before = _server.mcp.settings.transport_security
    monkeypatch.setattr(_server.mcp, "run", lambda **kw: None)
    monkeypatch.setattr("repowise.server.mcp_server._watchdog.start_parent_watchdog", lambda: None)

    _server.run_mcp(transport="stdio")

    assert _server.mcp.settings.transport_security is before
