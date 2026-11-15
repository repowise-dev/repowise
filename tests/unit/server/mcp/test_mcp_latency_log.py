"""Every MCP call appends its latency to a bounded repo-local log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core import mcp_latency
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._savings import wrapper


@pytest.fixture(autouse=True)
def _fresh_process_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mcp_latency, "_seen_tools", set())
    monkeypatch.setattr(mcp_latency, "_line_counts", {})


def _rows(repo: Path) -> list[dict]:
    log = repo / ".repowise" / mcp_latency.LOG_FILENAME
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_first_call_of_a_tool_is_cold(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()
    for ms in (10, 20):
        mcp_latency.record_call(tmp_path, "search_codebase", ms)
    mcp_latency.record_call(tmp_path, "get_context", 5)
    assert _rows(tmp_path) == [
        {"tool": "search_codebase", "ms": 10, "cold": True},
        {"tool": "search_codebase", "ms": 20, "cold": False},
        {"tool": "get_context", "ms": 5, "cold": True},
    ]


def test_no_repowise_dir_writes_nothing(tmp_path: Path) -> None:
    assert mcp_latency.record_call(tmp_path, "t", 1) is False
    assert not (tmp_path / ".repowise").exists()


def test_log_is_trimmed_to_the_last_rows_past_the_bound(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()
    for i in range(mcp_latency.MAX_LINES + 1):
        mcp_latency.record_call(tmp_path, "t", i)
    rows = _rows(tmp_path)
    assert len(rows) == mcp_latency.KEEP_LINES
    assert rows[-1]["ms"] == mcp_latency.MAX_LINES


def test_write_failure_returns_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".repowise").mkdir()
    monkeypatch.setattr(Path, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    assert mcp_latency.record_call(tmp_path, "t", 1) is False


def test_summary_percentiles_and_absent_file(tmp_path: Path) -> None:
    assert mcp_latency.latency_summary(tmp_path) == {}
    (tmp_path / ".repowise").mkdir()
    for ms in range(1, 11):
        mcp_latency.record_call(tmp_path, "t", ms)
    assert mcp_latency.latency_summary(tmp_path) == {"t": {"calls": 10, "p50": 5, "p90": 9}}


@pytest.mark.asyncio
async def test_instrument_logs_and_a_failing_log_never_fails_the_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".repowise").mkdir()
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path), raising=False)
    monkeypatch.setattr(wrapper, "_observe_baseline", lambda *a: None)
    monkeypatch.setattr(wrapper, "_emit_telemetry", lambda *a: None)

    async def sample_tool() -> dict:
        return {"ok": True}

    wrapped = wrapper.instrument(sample_tool)
    assert await wrapped() == {"ok": True}
    assert _rows(tmp_path)[0]["tool"] == "sample_tool"

    def boom(*a):
        raise RuntimeError("disk")

    monkeypatch.setattr(wrapper, "record_call", boom)
    assert await wrapped() == {"ok": True}
