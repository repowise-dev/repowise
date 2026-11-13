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
        # Likewise the host label, which reads the process environment.
        monkeypatch.setattr(wrapper, "_client_label", lambda: "claude_code")
        # And the served index's mode, which reads the state file of whatever
        # repo an earlier test left the server pointed at.
        monkeypatch.setattr(wrapper, "_index_mode", lambda: None)

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
            "client": "claude_code",
            "confidence": "high",
            "retrieval_quality": "strong",
            "grounding": "exact_symbol",
            "index_behind": True,
            "embedder_degraded": False,
            "response_chars": chars,
            "response_tokens": tokens,
            "empty_result": False,
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
        # No site noted a reason, so it says so rather than guessing.
        assert props["error_reason"] == "other"

    def test_error_reason_comes_from_the_note_not_the_message(self):
        from repowise.server.mcp_server._savings import interaction

        with interaction.begin("get_symbol"):
            interaction.note_error_reason("not_found")
            interaction.note_error_reason("internal")  # the first note wins
            props = wrapper._telemetry_properties("get_symbol", {"error": "Symbol x"}, 3)
        assert props["error_reason"] == "not_found"

    def test_an_unknown_reason_is_never_recorded(self):
        from repowise.server.mcp_server._savings import interaction

        with interaction.begin("get_symbol"):
            interaction.note_error_reason("Symbol not found: 'secret'")
            props = wrapper._telemetry_properties("get_symbol", {"error": "x"}, 3)
        assert props["error_reason"] == "other"

    def test_ok_result_has_no_error_reason(self):
        assert "error_reason" not in wrapper._telemetry_properties("get_answer", {}, 1)

    def test_search_result_reports_count_bucket_not_content(self):
        result = {"results": [{"path": "secret/a.py"}, {"path": "secret/b.py"}]}
        props = wrapper._telemetry_properties("search_codebase", result, 5)
        assert props["results_bucket"] == "1-3"
        # Only coarse keys — no results/paths reach the wire.
        assert set(props) == {
            "tool",
            "status",
            "duration_ms",
            "client",
            "results_bucket",
            "response_chars",
            "response_tokens",
            "empty_result",
        }
        assert props["empty_result"] is False
        assert (props["response_chars"], props["response_tokens"]) == _size(result)

    def test_non_dict_result_is_safe(self):
        props = wrapper._telemetry_properties("get_overview", "unexpected", 1)
        assert props == {
            "tool": "get_overview",
            "status": "ok",
            "duration_ms": 1,
            "client": "claude_code",
        }

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
            "client": "claude_code",
            "confidence": "high",
            # Nothing cited and no prose: the answer found nothing.
            "empty_result": True,
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


class TestClientLabel:
    def test_announced_client_wins(self, monkeypatch: pytest.MonkeyPatch):
        from repowise.server.mcp_server._savings import interaction

        monkeypatch.setattr(interaction, "_attribution", lambda: ("codex", {}, None))
        monkeypatch.setenv("CLAUDECODE", "1")
        with interaction.begin("get_answer"):
            assert wrapper._client_label() == "codex"

    def test_falls_back_to_the_environment(self, monkeypatch: pytest.MonkeyPatch):
        from repowise.core.platform import telemetry

        monkeypatch.setattr(telemetry, "detect_client", lambda: "cursor")
        assert wrapper._client_label() == "cursor"

    def test_a_server_is_never_a_terminal(self, monkeypatch: pytest.MonkeyPatch):
        from repowise.core.platform import telemetry

        monkeypatch.setattr(telemetry, "detect_client", lambda: "terminal")
        assert wrapper._client_label() == "unknown"


class TestCallFacts:
    """The fields that split a call's latency and outcome without a join.

    Cold or warm, the served index's mode, the provider kind, per-stage time,
    an honest empty result and where an error came from. Each one is a fixed
    label, a boolean or a whole number of milliseconds.
    """

    @pytest.fixture(autouse=True)
    def _pinned(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(wrapper, "_semantic_search_state", lambda: None)
        monkeypatch.setattr(wrapper, "_client_label", lambda: "claude_code")

    @pytest.mark.asyncio
    async def test_only_the_first_call_of_the_process_is_cold(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from repowise.core.platform import telemetry

        calls: list[dict] = []
        monkeypatch.setattr(telemetry, "record_event", lambda event, props: calls.append(props))
        monkeypatch.setattr(wrapper, "_calls_started", 0)

        async def get_overview() -> dict:
            return {"_meta": {}}

        tool = wrapper.instrument(get_overview)
        await tool()
        await tool()
        assert [c["cold_start"] for c in calls] == [True, False]

    def test_index_mode_is_who_wrote_the_served_pages(self, tmp_path):
        from repowise.server.mcp_server._savings import interaction

        (tmp_path / ".repowise").mkdir()
        (tmp_path / ".repowise" / "state.json").write_text(
            json.dumps({"docs_mode": "deterministic"}), encoding="utf-8"
        )
        with interaction.begin("get_overview") as live:
            live.repo_root = str(tmp_path)
            props = wrapper._telemetry_properties("get_overview", {}, 1)
        assert props["index_mode"] == "template"
        # The repo's location is how the mode was found, never what is sent.
        assert str(tmp_path) not in json.dumps(props)

    def test_no_served_repo_reports_no_index_mode(self, monkeypatch: pytest.MonkeyPatch):
        from repowise.server.mcp_server import _state

        monkeypatch.setattr(_state, "_repo_path", None)
        assert "index_mode" not in wrapper._telemetry_properties("get_overview", {}, 1)

    def test_provider_kind_is_noted_where_synthesis_runs(self):
        from types import SimpleNamespace

        from repowise.server.mcp_server._savings import interaction

        with interaction.begin("get_answer"):
            interaction.note_provider_kind(SimpleNamespace(provider_name="claude_cli"))
            props = wrapper._telemetry_properties("get_answer", {"answer": "x"}, 1)
        assert props["provider_kind"] == "subscription_cli"

    def test_an_answer_with_no_provider_reports_none(self):
        result = {"answer": "x", "degraded": "no-llm-provider"}
        assert wrapper._telemetry_properties("get_answer", result, 1)["provider_kind"] == "none"

    def test_every_provider_maps_to_a_fixed_kind(self):
        from repowise.core.providers.llm.specs import (
            PROVIDER_KINDS,
            PROVIDER_SPECS,
            provider_kind,
        )

        kinds = {name: provider_kind(name) for name in PROVIDER_SPECS}
        assert set(kinds.values()) <= PROVIDER_KINDS
        assert kinds["anthropic"] == "api_key"
        assert kinds["codex_cli"] == "subscription_cli"
        assert kinds["ollama"] == "local"
        assert provider_kind(None) == "none"
        assert provider_kind("not-a-provider") == "other"

    @pytest.mark.asyncio
    async def test_stage_timings_are_flat_whole_milliseconds(self):
        from repowise.server.mcp_server._savings import interaction

        @interaction.timed_stage("retrieve")
        async def retrieve() -> str:
            return "hits"

        with interaction.begin("get_answer"):
            assert await retrieve() == "hits"
            interaction.note_stage_ms("synthesize", 1234.6)
            interaction.note_stage_ms("a stage nobody listed", 5)
            props = wrapper._telemetry_properties("get_answer", {"answer": "x"}, 1)
        assert isinstance(props["retrieve_ms"], int)
        assert props["synthesize_ms"] == 1235
        # Flat keys only: the ingest keeps scalars and drops nested objects.
        assert not any(isinstance(v, dict) for v in props.values())
        assert not any("nobody" in key for key in props)

    def test_change_risk_blocks_are_stages(self):
        from repowise.server.mcp_server._savings import interaction
        from repowise.server.mcp_server.tool_change_risk.tool import _timed

        timings: dict[str, int] = {}
        with interaction.begin("get_change_risk"):
            with _timed(timings, "impacted_tests"):
                pass
            props = wrapper._telemetry_properties("get_change_risk", {}, 1)
        assert props["impacted_tests_ms"] == timings["impacted_tests"]

    def test_error_source_names_the_tool_or_the_shield(self):
        from repowise.server.mcp_server._failure_shield import _shape_exception
        from repowise.server.mcp_server._savings import interaction

        with interaction.begin("get_symbol"):
            reply = interaction.error_reply("not_found", {"error": "Symbol x not found"})
            props = wrapper._telemetry_properties("get_symbol", reply, 1)
        assert (props["error_reason"], props["error_source"]) == ("not_found", "tool")

        with interaction.begin("get_symbol"):
            reply = _shape_exception("get_symbol", RuntimeError("boom"))
            props = wrapper._telemetry_properties("get_symbol", reply, 1)
        assert (props["error_reason"], props["error_source"]) == ("internal", "exception")

    def test_an_unnoted_error_has_no_source(self):
        props = wrapper._telemetry_properties("get_symbol", {"error": "x"}, 1)
        assert "error_source" not in props

    @pytest.mark.parametrize(
        ("tool", "result", "empty"),
        [
            ("search_codebase", {"results": [], "lines": []}, True),
            ("search_codebase", {"results": [], "lines": [{"path": "a.py"}]}, False),
            ("get_context", {"targets": {"x": {"error": "Target not found: 'x'"}}}, True),
            ("get_context", {"targets": {"x": {"error": "nope"}, "y": {"summary": "s"}}}, False),
            ("get_answer", {"confidence": "low"}, True),
            ("get_answer", {"confidence": "low", "fallback_targets": ["a.py"]}, False),
            ("get_why", {"mode": "path"}, True),
            ("get_why", {"mode": "path", "answer_basis": "decision"}, False),
        ],
    )
    def test_empty_result_follows_each_tools_own_rule(self, tool, result, empty):
        assert wrapper._telemetry_properties(tool, result, 1)["empty_result"] is empty

    def test_a_tool_without_a_rule_and_an_error_report_no_emptiness(self):
        assert "empty_result" not in wrapper._telemetry_properties("get_overview", {}, 1)
        assert "empty_result" not in wrapper._telemetry_properties(
            "search_codebase", {"error": "x", "results": []}, 1
        )


class TestFieldAllowList:
    """No property outside the allow-list ever reaches the wire."""

    def test_an_unlisted_property_is_dropped(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            wrapper, "_call_facts", lambda tool, result: {"query": "how does secret work"}
        )
        props = wrapper._telemetry_properties("get_answer", {"answer": "x"}, 1)
        assert "query" not in props

    def test_a_full_call_carries_only_listed_coarse_values(self, tmp_path):
        from types import SimpleNamespace

        from repowise.server.mcp_server._savings import interaction

        (tmp_path / ".repowise").mkdir()
        (tmp_path / ".repowise" / "state.json").write_text(
            json.dumps({"docs_mode": "llm"}), encoding="utf-8"
        )
        result = {
            "answer": "secret prose about secret_module",
            "citations": ["secret/path.py"],
            "confidence": "high",
            "degraded": "synthesis-failed",
            "_meta": {"index_behind": False},
        }
        with interaction.begin("get_answer") as live:
            live.repo_root = str(tmp_path)
            interaction.note_stage_ms("retrieve", 10)
            interaction.note_provider_kind(SimpleNamespace(provider_name="openai"))
            props = wrapper._telemetry_properties("get_answer", result, 5, cold_start=True)
        assert set(props) <= wrapper.MCP_TOOL_CALL_FIELDS
        assert all(isinstance(v, (bool, int, str)) for v in props.values())
        wire = json.dumps(props)
        assert "secret" not in wire
        assert str(tmp_path) not in wire


def test_the_event_dump_shows_the_new_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """``REPOWISE_TELEMETRY_DEBUG`` prints exactly what would be sent, and sends nothing."""
    import asyncio

    from repowise.server.mcp_server._savings import interaction

    for var in ("DO_NOT_TRACK", "REPOWISE_TELEMETRY_DISABLED", "REPOWISE_OFFLINE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("REPOWISE_TELEMETRY_DEBUG", "1")
    monkeypatch.setattr(wrapper, "_calls_started", 0)

    async def search_codebase(query: str) -> dict:
        interaction.note_stage_ms("retrieve", 3)
        return {"results": [], "_meta": {}}

    async def call() -> None:
        with interaction.begin("search_codebase"):
            await wrapper.instrument(search_codebase)("where is the secret")

    asyncio.run(call())
    dumped = capsys.readouterr().err
    envelope = json.loads(dumped.split("would send:\n", 1)[1])
    props = envelope["properties"]
    assert envelope["event"] == "mcp_tool_call"
    assert props["cold_start"] is True
    assert props["empty_result"] is True
    assert props["retrieve_ms"] == 3
    assert "secret" not in dumped
