"""MCP transport CLI and server dispatch tests."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from repowise.cli.main import cli


def test_mcp_help_lists_streamable_http_transport() -> None:
    result = CliRunner().invoke(cli, ["mcp", "--help"])

    assert result.exit_code == 0
    assert "streamable-http" in result.output
    assert "HTTP/SSE" in result.output


def test_mcp_cli_accepts_streamable_http_transport(
    monkeypatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".repowise").mkdir()
    captured: dict[str, object] = {}

    def fake_run_mcp(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("repowise.server.mcp_server.run_mcp", fake_run_mcp)

    result = CliRunner().invoke(
        cli,
        [
            "mcp",
            str(tmp_path),
            "--transport",
            "streamable-http",
            "--port",
            "7339",
        ],
    )

    assert result.exit_code == 0
    assert "streamable HTTP" in result.output
    assert captured == {
        "transport": "streamable-http",
        "repo_path": str(tmp_path.resolve()),
        "port": 7339,
    }


def test_run_mcp_dispatches_streamable_http(monkeypatch) -> None:
    from repowise.server.mcp_server import _server

    calls: list[dict[str, str]] = []
    watchdog_started = False

    def fake_run(**kwargs):
        calls.append(kwargs)

    def fake_watchdog():
        nonlocal watchdog_started
        watchdog_started = True

    monkeypatch.setattr(_server.mcp, "run", fake_run)
    monkeypatch.setattr(
        "repowise.server.mcp_server._watchdog.start_parent_watchdog",
        fake_watchdog,
    )

    _server.run_mcp(transport="streamable-http", repo_path="/tmp/repo", port=7340)

    assert _server.mcp.settings.port == 7340
    assert calls == [{"transport": "streamable-http"}]
    assert watchdog_started is False


def test_run_mcp_keeps_existing_stdio_and_sse_dispatch(monkeypatch) -> None:
    from repowise.server.mcp_server import _server

    calls: list[dict[str, str]] = []
    watchdog_calls = 0

    def fake_run(**kwargs):
        calls.append(kwargs)

    def fake_watchdog():
        nonlocal watchdog_calls
        watchdog_calls += 1

    monkeypatch.setattr(_server.mcp, "run", fake_run)
    monkeypatch.setattr(
        "repowise.server.mcp_server._watchdog.start_parent_watchdog",
        fake_watchdog,
    )

    _server.run_mcp(transport="sse", port=7338)
    _server.run_mcp(transport="stdio", port=9999)

    assert calls == [{"transport": "sse"}, {"transport": "stdio"}]
    assert watchdog_calls == 1
