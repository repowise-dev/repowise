"""`repowise serve --host` must reach the server's view of its own bind (#1391).

The auth layer reads ``REPOWISE_HOST`` to describe the bind. Nothing set it
from ``--host``, so ``serve --host 0.0.0.0`` exposed the API to the network
while the server still believed it was loopback-only.
"""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from repowise.cli.commands import serve_cmd


@pytest.fixture
def stub_serve(monkeypatch) -> dict:
    """Run `serve` up to the uvicorn call and capture what it would bind."""
    captured: dict = {}

    def fake_run(_app: str, **kwargs) -> None:
        captured["host"] = kwargs.get("host")
        captured["repowise_host_env"] = os.environ.get("REPOWISE_HOST")

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr(serve_cmd, "_load_local_provider_config", lambda: None)
    monkeypatch.setattr(serve_cmd, "_setup_embedder", lambda: None)
    monkeypatch.setattr(serve_cmd, "_serve_lock_path", lambda: None)
    # No real sockets: the port probe is not what these tests are about.
    monkeypatch.setattr(serve_cmd, "_find_free_port", lambda _host, port, _label: port)
    monkeypatch.delenv("REPOWISE_API_KEY", raising=False)
    # The command auto-detects `./.repowise/wiki.db` and assigns
    # REPOWISE_DB_URL straight onto os.environ. Under CliRunner the cwd is the
    # developer's own checkout, and a raw assignment outlives the test, so
    # every later test that indexes a tmp_path repo wrote into it. Setting the
    # variable here skips that branch and hands the cleanup to monkeypatch,
    # exactly as REPOWISE_HOST is handled below.
    monkeypatch.setenv("REPOWISE_DB_URL", "sqlite+aiosqlite:///:memory:")
    # setenv, not delenv: the command assigns REPOWISE_HOST, and only a
    # monkeypatch that recorded the variable will unset it again afterwards.
    monkeypatch.setenv("REPOWISE_HOST", "127.0.0.1")
    return captured


def test_host_flag_propagates_to_repowise_host(stub_serve: dict) -> None:
    result = CliRunner().invoke(serve_cmd.serve_command, ["--no-ui", "--host", "0.0.0.0"])
    assert result.exit_code == 0, result.output
    assert stub_serve["host"] == "0.0.0.0"
    assert stub_serve["repowise_host_env"] == "0.0.0.0"


def test_exposed_bind_without_key_warns(stub_serve: dict) -> None:
    result = CliRunner().invoke(serve_cmd.serve_command, ["--no-ui", "--host", "0.0.0.0"])
    assert "REPOWISE_API_KEY" in result.output


def test_loopback_bind_is_quiet(stub_serve: dict) -> None:
    result = CliRunner().invoke(serve_cmd.serve_command, ["--no-ui", "--host", "127.0.0.1"])
    assert result.exit_code == 0, result.output
    assert stub_serve["repowise_host_env"] == "127.0.0.1"
    assert "REPOWISE_API_KEY" not in result.output


def test_web_ui_takes_the_api_bind(monkeypatch, tmp_path) -> None:
    """The UI proxies `/api/*` to the API, so a wider bind widens the API.

    With the UI on the wildcard and the API on loopback, every proxied request
    reached the API from 127.0.0.1 and passed the local-caller check, which
    handed the whole API to the network on the UI's port.
    """
    captured: dict = {}

    def fake_popen(_cmd, **kwargs):
        captured["hostname"] = kwargs["env"]["HOSTNAME"]
        return None

    (tmp_path / "server.js").write_text("")
    monkeypatch.setattr(serve_cmd.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(serve_cmd, "_WEB_CACHE_DIR", tmp_path)
    serve_cmd._start_frontend("node", 7337, 3000, local_web=None, host="127.0.0.1")
    assert captured["hostname"] == "127.0.0.1"
