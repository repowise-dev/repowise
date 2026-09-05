"""``repowise mcp`` turns an unopenable store into one line and exit 1."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.server.mcp_server._server import StoreUnavailableError


def test_store_unavailable_exits_cleanly_with_the_message(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()

    def _boom(**_kw):
        raise StoreUnavailableError("repowise MCP: cannot open the index store at X: denied")

    monkeypatch.setattr("repowise.server.mcp_server.run_mcp", _boom)
    result = CliRunner().invoke(cli, ["mcp", str(tmp_path)])

    assert result.exit_code == 1
    assert "cannot open the index store at X" in result.output
    assert "Traceback" not in result.output
