"""The MCP instrument seam emits one anonymous ``mcp_tool_call`` per call.

Guards the two things that matter: the reported properties are coarse and
non-identifying (enums/booleans/buckets, never query text or paths), and the
emit is best-effort so it can never break or slow a tool response.
"""

from __future__ import annotations

import json

import pytest

from repowise.server.mcp_server._savings import wrapper


def _size(result) -> tuple[int, int]:
    """The size the wrapper should report, from the same formula the budget uses."""
    chars = len(json.dumps(result, separators=(",", ":"), default=str))
    return chars, chars // 4


class TestTelemetryProperties:
    @pytest.fixture(autouse=True)
    def _no_ambient_embedder(self, monkeypatch: pytest.MonkeyPatch):
        """Pin the install-level state so the exact-shape cases stay exact.

        `semantic_search` is read from server state rather than the response, so
        without this an unrelated test leaving an embedder resolved would add a
        key here and the equality assertions would fail for the wrong reason.
        """
        monkeypatch.setattr(wrapper, "_semantic_search_state", lambda: None)

    def test_answer_shape_extracts_enums_and_flags(self):
        result = {
            "answer": "…prose the agent reads…",  # must NOT be reported
            "confidence": "high",
            "retrieval_quality": "strong",
            "grounding": "exact_symbol",
            "_meta": {"index_behind": True, "embedder_degraded": False, "timing_ms": 12.3},
        }
        props = wrapper._telemetry_properties("get_answer", result, 42)
        chars, tokens = _size(result)
        assert props == {
            "tool": "get_answer",
            "status": "ok",
            "duration_ms": 42,
            "confidence": "high",
            "retrieval_quality": "strong",
            "grounding": "exact_symbol",
            "index_behind": True,
            "embedder_degraded": False,
            "response_chars": chars,
            "response_tokens": tokens,
        }

    def test_degraded_answer_reports_why_synthesis_was_missing(self):
        """"No provider" and "the provider failed" are different products.

        Both return the same payload shape, so without this dimension a keyless
        install working exactly as designed is indistinguishable from a broken
        one, and neither can be sized.
        """
        result = {
            "answer": "…assembled boilerplate…",
            "confidence": "medium",
            "retrieval_quality": "high",
            "degraded": "no-llm-provider",
            "_meta": {"embedder_degraded": False},
        }
        props = wrapper._telemetry_properties("get_answer", result, 7)
        assert props["degraded"] == "no-llm-provider"
        assert props["embedder_degraded"] is False
        # The reason is an enum; the prose it explains never travels.
        assert "answer" not in props

    def test_semantic_search_is_read_from_the_install_not_the_response(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The response shape is untouched; the dimension still gets all three states.

        Putting this on the wire would have cost every caller tokens on every
        call to say something `embedder_degraded` already implies. It is a fact
        about the install, so it is read from the install.
        """
        monkeypatch.setattr(wrapper, "_semantic_search_state", lambda: False)
        props = wrapper._telemetry_properties("get_answer", {"confidence": "low"}, 1)
        assert props["semantic_search"] is False

        monkeypatch.setattr(wrapper, "_semantic_search_state", lambda: True)
        assert wrapper._telemetry_properties("get_answer", {}, 1)["semantic_search"] is True

    def test_an_unevaluated_embedder_reports_no_semantic_search(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Absent stays distinct from False: "never checked" is not "off"."""
        monkeypatch.setattr(wrapper, "_semantic_search_state", lambda: None)
        assert "semantic_search" not in wrapper._telemetry_properties("get_answer", {}, 1)

    def test_error_result_is_status_error(self):
        props = wrapper._telemetry_properties("get_symbol", {"error": "boom"}, 3)
        assert props["status"] == "error"

    def test_search_result_reports_count_bucket_not_content(self):
        result = {"results": [{"path": "secret/a.py"}, {"path": "secret/b.py"}]}
        props = wrapper._telemetry_properties("search_codebase", result, 5)
        assert props["results_bucket"] == "1-3"
        # Only coarse keys — no results/paths reach the wire.
        assert set(props) == {
            "tool",
            "status",
            "duration_ms",
            "results_bucket",
            "response_chars",
            "response_tokens",
        }
        assert (props["response_chars"], props["response_tokens"]) == _size(result)

    def test_non_dict_result_is_safe(self):
        props = wrapper._telemetry_properties("get_overview", "unexpected", 1)
        assert props == {"tool": "get_overview", "status": "ok", "duration_ms": 1}

    def test_response_size_is_the_serialised_payload(self):
        """Size is a number about the payload, not any of its content."""
        result = {"answer": "x" * 100, "confidence": "high"}
        props = wrapper._telemetry_properties("get_answer", result, 1)
        chars, tokens = _size(result)
        assert props["response_chars"] == chars
        assert props["response_tokens"] == tokens == chars // 4

    def test_unserialisable_result_reports_no_size(self, monkeypatch: pytest.MonkeyPatch):
        """A dump that blows up drops the size keys instead of failing the emit."""

        class _Boom:
            @staticmethod
            def dumps(*a, **k):
                raise TypeError("nope")

        monkeypatch.setattr(wrapper, "json", _Boom)
        props = wrapper._telemetry_properties("get_answer", {"confidence": "high"}, 1)
        assert props == {
            "tool": "get_answer",
            "status": "ok",
            "duration_ms": 1,
            "confidence": "high",
        }


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


def test_response_size_prefers_the_budget_stamp(monkeypatch: pytest.MonkeyPatch):
    """The budget pass already measured the payload; a second dump is waste."""
    monkeypatch.setattr(wrapper, "_semantic_search_state", lambda: None)
    result = {"answer": "x" * 50, "_meta": {"response_budget": {"serialized_chars": 4000}}}
    props = wrapper._telemetry_properties("get_answer", result, 1)
    assert props["response_chars"] == 4000
    assert props["response_tokens"] == 1000
