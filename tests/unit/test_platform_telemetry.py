"""Tests for the core-layer anonymous telemetry emitter.

This is the self-contained substrate the MCP server uses (it may not import the
CLI). It must honour the same opt-out consent, produce the same anonymous
envelope shape, and never touch disk or the network on the caller's hot path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.platform import telemetry


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point platform.json at a temp file and reset the process caches."""
    state_path = tmp_path / "platform.json"
    monkeypatch.setattr(telemetry, "_platform_json_path", lambda: state_path)
    monkeypatch.setattr(telemetry, "_state_cache", None, raising=False)
    for var in ("DO_NOT_TRACK", "REPOWISE_TELEMETRY_DISABLED", "REPOWISE_TELEMETRY_DEBUG"):
        monkeypatch.delenv(var, raising=False)
    telemetry._pending.clear()
    yield telemetry, state_path
    telemetry._pending.clear()


def _write_state(path: Path, **fields) -> None:
    path.write_text(json.dumps(fields), encoding="utf-8")


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Patch the sender + force synchronous delivery; return captured envelopes."""
    sent: list[dict] = []
    monkeypatch.setattr(telemetry, "_post", lambda envelope: sent.append(envelope))
    monkeypatch.setattr(telemetry, "_state_cache", None, raising=False)
    monkeypatch.setattr(
        telemetry.threading,
        "Thread",
        lambda target, args, daemon: type(
            "T", (), {"start": lambda s: target(*args), "is_alive": lambda s: False}
        )(),
    )
    return sent


class TestConsent:
    def test_enabled_by_default(self, isolated):
        _, path = isolated
        _write_state(path, anon_id="abc")
        assert telemetry.is_enabled() is True

    def test_do_not_track_blocks_send(self, isolated, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DO_NOT_TRACK", "1")
        sent = _capture(monkeypatch)
        telemetry.record_event("mcp_tool_call", {"tool": "get_answer"})
        assert sent == []

    def test_stored_disable_blocks_send(self, isolated, monkeypatch: pytest.MonkeyPatch):
        _, path = isolated
        _write_state(path, anon_id="abc", telemetry_enabled=False)
        sent = _capture(monkeypatch)
        telemetry.record_event("mcp_tool_call", {"tool": "get_answer"})
        assert sent == []


class TestEnvelope:
    def test_shape_and_anon_id(self, isolated, monkeypatch: pytest.MonkeyPatch):
        _, path = isolated
        _write_state(path, anon_id="install-xyz")
        sent = _capture(monkeypatch)

        telemetry.record_event("mcp_tool_call", {"tool": "get_answer", "status": "ok"})

        assert len(sent) == 1
        env = sent[0]
        assert env["event"] == "mcp_tool_call"
        assert env["anon_id"] == "install-xyz"  # shared with the CLI's install id
        assert {"session_id", "cli_version", "os", "arch", "python_version", "is_ci"} <= set(env)
        assert env["properties"]["tool"] == "get_answer"

    def test_no_patch_version_leak(self, isolated, monkeypatch: pytest.MonkeyPatch):
        _, path = isolated
        _write_state(path, anon_id="abc")
        sent = _capture(monkeypatch)
        telemetry.record_event("mcp_tool_call", {"tool": "x"})
        assert sent[0]["python_version"].count(".") == 1  # major.minor only

    def test_missing_platform_json_sends_null_anon(self, isolated, monkeypatch: pytest.MonkeyPatch):
        # No CLI has run — no platform.json. We still send, with anon_id=None,
        # rather than minting a competing id.
        sent = _capture(monkeypatch)
        telemetry.record_event("mcp_tool_call", {"tool": "x"})
        assert sent and sent[0]["anon_id"] is None


class TestDebug:
    def test_debug_prints_does_not_send(
        self, isolated, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ):
        _, path = isolated
        _write_state(path, anon_id="abc")
        sent = _capture(monkeypatch)
        monkeypatch.setenv("REPOWISE_TELEMETRY_DEBUG", "1")
        telemetry.record_event("mcp_tool_call", {"tool": "get_answer"})
        assert "would send" in capsys.readouterr().err
        assert sent == []
