"""The MCP instrument seam emits one anonymous ``mcp_tool_call`` per call.

Guards the two things that matter: the reported properties are coarse and
non-identifying (enums/booleans/buckets, never query text or paths), and the
emit is best-effort so it can never break or slow a tool response.
"""

from __future__ import annotations

import pytest

from repowise.server.mcp_server._savings import wrapper


class TestTelemetryProperties:
    def test_answer_shape_extracts_enums_and_flags(self):
        result = {
            "answer": "…prose the agent reads…",  # must NOT be reported
            "confidence": "high",
            "retrieval_quality": "strong",
            "grounding": "exact_symbol",
            "_meta": {"index_behind": True, "embedder_degraded": False, "timing_ms": 12.3},
        }
        props = wrapper._telemetry_properties("get_answer", result, 42)
        assert props == {
            "tool": "get_answer",
            "status": "ok",
            "duration_ms": 42,
            "confidence": "high",
            "retrieval_quality": "strong",
            "grounding": "exact_symbol",
            "index_behind": True,
            "embedder_degraded": False,
        }

    def test_error_result_is_status_error(self):
        props = wrapper._telemetry_properties("get_symbol", {"error": "boom"}, 3)
        assert props["status"] == "error"

    def test_search_result_reports_count_bucket_not_content(self):
        result = {"results": [{"path": "secret/a.py"}, {"path": "secret/b.py"}]}
        props = wrapper._telemetry_properties("search_codebase", result, 5)
        assert props["results_bucket"] == "1-3"
        # Only coarse keys — no results/paths reach the wire.
        assert set(props) == {"tool", "status", "duration_ms", "results_bucket"}

    def test_non_dict_result_is_safe(self):
        props = wrapper._telemetry_properties("get_overview", "unexpected", 1)
        assert props == {"tool": "get_overview", "status": "ok", "duration_ms": 1}


@pytest.mark.asyncio
async def test_instrument_emits_one_event(monkeypatch: pytest.MonkeyPatch):
    from repowise.core.platform import telemetry

    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(telemetry, "record_event", lambda event, props: calls.append((event, props)))

    async def get_answer(question: str) -> dict:
        return {"answer": "x", "confidence": "medium", "_meta": {}}

    out = await wrapper.instrument(get_answer)("how does X work")
    assert out["confidence"] == "medium"  # response unchanged
    assert len(calls) == 1
    event, props = calls[0]
    assert event == "mcp_tool_call"
    assert props["tool"] == "get_answer"
    assert props["confidence"] == "medium"
    assert "duration_ms" in props


@pytest.mark.asyncio
async def test_telemetry_failure_never_breaks_the_tool(monkeypatch: pytest.MonkeyPatch):
    from repowise.core.platform import telemetry

    def boom(*a, **k):
        raise RuntimeError("telemetry backend down")

    monkeypatch.setattr(telemetry, "record_event", boom)

    async def get_overview() -> dict:
        return {"ok": True, "_meta": {}}

    # The tool result must survive a telemetry emit that raises.
    out = await wrapper.instrument(get_overview)()
    assert out == {"ok": True, "_meta": {}}
