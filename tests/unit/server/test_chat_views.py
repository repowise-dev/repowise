"""Chat view builders: exact source numbers, guards, ``show_view`` errors and the SSE event."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from repowise.core.providers.llm.base import ChatStreamEvent, ChatToolCall
from repowise.server.chat_artifacts import ARTIFACT_ID_FIELD, create_artifact_envelope
from repowise.server.chat_tools import get_tool_catalog
from repowise.server.chat_views import (
    SHOW_VIEW_TOOL,
    TurnViews,
    ViewArtifact,
    ViewError,
    args_hash,
    build_view,
    register_view_builder,
    show_view_schema,
    view_builders,
)
from repowise.server.mcp_server._tool_selection import get_registered_tool
from repowise.server.schemas.views import ViewPartList
from tests.unit.server.test_chat_router import _PROVIDER, _ScriptedProvider, _stored_messages
from tests.unit.server.test_chat_stream_terminal_event import _data_events, _make_app, _post

_FIXTURE = (
    Path(__file__).resolve().parents[3] / "packages" / "types" / "fixtures" / "views.conformance.json"
)
FIELDS: dict[str, list[str]] = json.loads(_FIXTURE.read_text(encoding="utf-8"))["fields"]

_META = {"indexed_commit": "4c1e31fe3a9b", "stale_warning": "Index is behind live HEAD."}

HEALTH = {
    "distribution": {
        "total_files": 5034,
        "bands": {
            "at_risk": {"files": 168, "nloc": 101306, "pct": 11.3},
            "needs_work": {"files": 128, "nloc": 53535, "pct": 6},
            "fair": {"files": 268, "nloc": 87572, "pct": 9.8},
            "good": {"files": 504, "nloc": 124420, "pct": 13.9},
            "excellent": {"files": 3966, "nloc": 527275, "pct": 59},
        },
    },
    "trend": {
        "recent": [
            {"taken_at": "2026-10-11T17:11:16", "hotspot_health": 6.47, "average_health": 7.88},
            {"taken_at": "2026-10-11T04:34:21", "hotspot_health": 6.16, "average_health": 7.74},
            {"taken_at": "2026-10-10T18:36:12", "hotspot_health": 6.71, "average_health": 8.02},
        ],
        "recent_total": 10,
    },
    "coverage": {
        "summary": {"line_coverage_pct": 88.84},
        "files": [
            {"file_path": "a.py", "line_coverage_pct": 100, "covered_line_count": 9},
            {"file_path": "b.py", "line_coverage_pct": 96.36, "covered_line_count": 53},
        ],
        "files_total": 1382,
    },
    "high_leverage_files": [
        {"file_path": "cmd.py", "score": 1, "max_ccn": 279, "primary_owner": "Someone"},
        {"file_path": "init.py", "score": 1.01, "max_ccn": 160, "primary_owner": "Someone"},
    ],
    "high_leverage_files_total": 770,
    "_meta": _META,
}

DEAD_CODE = {
    "summary": {"total_findings": 15, "by_kind": {"unreachable_file": 4, "unused_export": 11}},
    "tiers": {
        "high": {"count": 0, "lines": 0, "findings": []},
        "medium": {
            "count": 15,
            "lines": 545,
            "findings": [
                {"file_path": "x.py", "confidence": 0.4, "primary_owner": "Someone", "lines": 307}
            ],
        },
        "low": {"count": 0, "lines": 0, "findings": []},
    },
    "_meta": {"indexed_commit": "4c1e31fe3a9b"},
}

RISK = {
    "targets": {
        "src/a.py": {"dependents_count": 12, "hotspot_score": 0.9458, "primary_owner": "Someone"},
        "src/b.py": {"dependents_count": 6, "hotspot_score": 0.6087, "primary_owner": "Someone"},
    },
    "global_hotspots": [
        {"file_path": "analyzer.py", "hotspot_score": 0.9998, "fix_count": 56, "primary_owner": "S"},
        {"file_path": "parser.py", "hotspot_score": 0.9992, "fix_count": 48, "primary_owner": "S"},
    ],
    "global_hotspots_total": 1037,
    "_meta": _META,
}


def _artifact(data: dict, artifact_type: str, tool: str, basis: str = "inferred") -> ViewArtifact:
    return ViewArtifact(
        artifact_id="a1",
        tool_name=tool,
        artifact_type=artifact_type,
        evidence_basis=basis,
        arguments={"limit": 2},
        data=data,
    )


def _build(data: dict, artifact_type: str, tool: str, view: str, **options: Any) -> dict:
    part = build_view(_artifact(data, artifact_type, tool), view, options, part_id="v1")
    ViewPartList.model_validate([part])
    kind = "TableView" if part["kind"] == "table" else "ChartView"
    assert set(part) <= set(FIELDS[kind])
    return part


def _series(part: dict, key: str) -> list:
    return [row[key] for row in part["data"]["rows"]]


# ---------------------------------------------------------------------------
# Builders: every number is the source number
# ---------------------------------------------------------------------------


def test_health_bands_plots_each_band_file_count():
    part = _build(HEALTH, "health", "get_health", "health.bands")
    bands = HEALTH["distribution"]["bands"]
    assert _series(part, "files") == [bands[b]["files"] for b in bands]
    assert part["source"] == {
        "artifact_id": "a1",
        "tool_name": "get_health",
        "args_hash": args_hash({"limit": 2}),
    }
    assert part["stale"] is True
    assert part["as_of"] == "4c1e31fe3a9b"


def test_health_bands_reads_the_overview_distribution_too():
    overview = {"code_health": {"distribution": HEALTH["distribution"]}}
    part = _build(overview, "overview", "get_overview", "health.bands")
    assert sum(_series(part, "files")) == 5034
    assert part["as_of"] is None
    assert part["stale"] is False


def test_health_trend_is_oldest_first_with_source_values():
    part = _build(HEALTH, "health", "get_health", "health.trend")
    recent = sorted(HEALTH["trend"]["recent"], key=lambda s: s["taken_at"])
    assert part["chart_type"] == "line"
    assert _series(part, "average_health") == [s["average_health"] for s in recent]
    assert _series(part, "hotspot_health") == [s["hotspot_health"] for s in recent]
    assert part["data"]["total_rows"] == 10
    assert part["data"]["truncated"] is True


def test_test_coverage_carries_the_report_reach_as_coverage():
    part = _build(HEALTH, "health", "get_health", "health.test_coverage")
    assert _series(part, "line_coverage") == [100, 96.36]
    assert part["coverage"] == {"metric": "test_reach", "ratio": 0.8884}
    assert part["basis"]["kind"] == "measured"
    assert part["data"]["total_rows"] == 1382


def test_a_limit_cut_without_a_reported_total_is_still_truncated():
    data = {"global_hotspots": [{"file_path": f"f{i}.py", "fix_count": i} for i in range(5)]}
    part = _build(data, "risk", "get_risk", "risk.hotspots", limit=2)
    assert part["data"]["total_rows"] == 5
    assert part["data"]["truncated"] is True


def test_an_empty_chart_source_is_refused():
    with pytest.raises(ViewError) as err:
        _build({"summary": {"by_kind": {"x": "n/a"}}}, "dead_code", "get_dead_code", "dead_code.by_kind")
    assert err.value.code == "no_data"


def test_dead_code_by_tier_and_by_kind():
    tiers = _build(DEAD_CODE, "dead_code", "get_dead_code", "dead_code.by_tier")
    assert _series(tiers, "findings") == [0, 15, 0]
    kinds = _build(DEAD_CODE, "dead_code", "get_dead_code", "dead_code.by_kind")
    assert dict(zip(_series(kinds, "kind"), _series(kinds, "findings"), strict=True)) == {
        "unreachable_file": 4,
        "unused_export": 11,
    }


def test_risk_hotspots_plot_fix_counts_not_scores():
    part = _build(RISK, "risk", "get_risk", "risk.hotspots", limit=1)
    assert part["series"] == ["fix_count"]
    assert _series(part, "fix_count") == [56]
    assert part["data"]["total_rows"] == 1037
    assert part["data"]["truncated"] is True


def test_generic_table_over_a_list_keeps_values_and_the_total():
    part = _build(HEALTH, "health", "get_health", "table", path="high_leverage_files")
    assert [c["key"] for c in part["columns"]] == ["file_path", "score", "max_ccn"]
    assert part["rows"][1] == {
        "file_path": {"type": "file", "id": "init.py"},
        "score": 1.01,
        "max_ccn": 160,
    }
    assert part["total_rows"] == 770
    assert part["truncated"] is True


def test_generic_table_over_a_map_keys_rows_by_id():
    part = _build(RISK, "risk", "get_risk", "table", path="targets")
    assert part["rows"] == [
        {"id": "src/a.py", "dependents_count": 12, "hotspot_score": 0.9458},
        {"id": "src/b.py", "dependents_count": 6, "hotspot_score": 0.6087},
    ]


def test_generic_table_picks_the_only_table_and_honours_columns_and_limit():
    data = {"rows": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}], "_meta": _META}
    part = _build(data, "generic", "get_x", "table", columns=["b"], limit=1)
    assert part["rows"] == [{"b": "x"}]
    assert part["total_rows"] == 2
    assert part["basis"] == {"kind": "inferred", "source": "get_x"}


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_no_builder_output_has_a_person_column():
    cases = [
        (HEALTH, "health", "get_health"),
        (DEAD_CODE, "dead_code", "get_dead_code"),
        (RISK, "risk", "get_risk"),
    ]
    for data, artifact_type, tool in cases:
        for view in view_builders.names:
            try:
                part = build_view(_artifact(data, artifact_type, tool), view, {}, part_id="v1")
            except ViewError:
                continue
            columns = part.get("columns") or part["data"]["columns"]
            assert not any("owner" in c["key"] or "author" in c["key"] for c in columns), view
    part = _build(DEAD_CODE, "dead_code", "get_dead_code", "table", path="tiers.medium.findings")
    assert "primary_owner" not in part["rows"][0]


def test_a_person_path_or_column_is_refused():
    data = {"by_owner": [{"owner": "someone", "count": 3}]}
    with pytest.raises(ViewError) as exc:
        build_view(_artifact(data, "dead_code", "get_dead_code"), "table", {"path": "by_owner"}, part_id="v1")
    assert exc.value.code == "person_grouping"

    @register_view_builder("test.by_author", description="never allowed")
    def _by_author(artifact, options):
        return {
            "kind": "table",
            "title": "By author",
            "basis": {"kind": "measured", "source": "git"},
            "columns": [{"key": "author", "label": "Author"}],
            "rows": [{"author": "x"}],
            "total_rows": 1,
            "truncated": False,
        }

    try:
        with pytest.raises(ViewError) as exc:
            build_view(_artifact({}, "risk", "get_risk"), "test.by_author", {}, part_id="v1")
        assert exc.value.code == "person_grouping"
    finally:
        view_builders._impls.pop("test.by_author")


def _attribution_builder(ratio: float | None):
    def build(artifact, options):
        part = {
            "kind": "chart",
            "chart_type": "bar",
            "title": "Agent share",
            "summary": "Agent-authored share by component.",
            "basis": {"kind": "measured", "source": "git markers"},
            "x": "component",
            "series": ["share"],
            "data": {
                "columns": [
                    {"key": "component", "label": "Component"},
                    {"key": "share", "label": "Share", "unit": "%"},
                ],
                "rows": [{"component": "payments", "share": 40}],
                "total_rows": 1,
                "truncated": False,
            },
        }
        if ratio is not None:
            part["coverage"] = {"metric": "attribution", "ratio": ratio}
        return part

    return build


@pytest.mark.parametrize(("ratio", "refused"), [(0.2, True), (None, True), (0.3, False), (0.9, False)])
def test_attribution_below_the_floor_is_refused_with_its_coverage(ratio, refused):
    register_view_builder(
        "test.agent_share", description="test", options=frozenset(), coverage_metric="attribution"
    )(_attribution_builder(ratio))
    try:
        turn = TurnViews()
        stamped = turn.record("get_x", {}, {"ok": 1})
        result = turn.show({"artifact_id": stamped[ARTIFACT_ID_FIELD], "view": "test.agent_share"})
    finally:
        view_builders._impls.pop("test.agent_share")
    if refused:
        assert result["error_code"] == "insufficient_coverage"
        assert result["coverage"] == ratio
        assert turn.parts == []
    else:
        assert result["view_id"] == "v1"
        assert turn.parts[0]["coverage"]["ratio"] == ratio


def test_a_risk_score_is_never_a_chart_series():
    @register_view_builder("test.risk_chart", description="never allowed", options=frozenset())
    def _risk_chart(artifact, options):
        return {
            "kind": "chart",
            "chart_type": "bar",
            "title": "Risk",
            "summary": "Risk.",
            "basis": {"kind": "inferred", "source": "x"},
            "x": "file",
            "series": ["hotspot_score"],
            "data": {
                "columns": [{"key": "file", "label": "File"}, {"key": "hotspot_score", "label": "S"}],
                "rows": [{"file": "a", "hotspot_score": 0.9}],
                "total_rows": 1,
                "truncated": False,
            },
        }

    try:
        with pytest.raises(ViewError) as exc:
            build_view(_artifact(RISK, "risk", "get_risk"), "test.risk_chart", {}, part_id="v1")
        assert exc.value.code == "risk_score_series"
    finally:
        view_builders._impls.pop("test.risk_chart")


# ---------------------------------------------------------------------------
# Registry and show_view
# ---------------------------------------------------------------------------


def test_registering_one_function_adds_a_view():
    @register_view_builder("test.count", description="Rows counted", artifact_types={"risk"})
    def _count(artifact, options):
        return {
            "kind": "table",
            "title": "Targets",
            "basis": {"kind": "measured", "source": "test"},
            "columns": [{"key": "targets", "label": "Targets"}],
            "rows": [{"targets": len(artifact.data["targets"])}],
            "total_rows": 1,
            "truncated": False,
        }

    try:
        turn = TurnViews()
        stamped = turn.record("get_risk", {}, RISK, artifact_type="risk")
        receipt = turn.show({"artifact_id": stamped[ARTIFACT_ID_FIELD], "view": "test.count"})
        assert receipt == {"view_id": "v1", "kind": "table", "title": "Targets"}
        assert turn.parts[0]["rows"] == [{"targets": 2}]
        assert "test.count" in show_view_schema(["risk"])["function"]["parameters"]["properties"][
            "view"
        ]["enum"]
        assert "test.count" not in show_view_schema(["health"])["function"]["description"]
    finally:
        view_builders._impls.pop("test.count")


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({"artifact_id": "nope", "view": "table"}, "unknown_artifact"),
        ({"view": "table"}, "unknown_artifact"),
        ({"artifact_id": "@", "view": "pie.chart"}, "unknown_view"),
        ({"artifact_id": "@", "view": "dead_code.by_tier"}, "view_not_applicable"),
        ({"artifact_id": "@", "view": "health.bands", "options": {"color": "red"}}, "view_unavailable"),
        ({"artifact_id": "@", "view": "table", "options": {"path": "missing"}}, "bad_path"),
        ({"artifact_id": "@", "view": "table", "options": {"limit": 0}}, "bad_arguments"),
        ({"artifact_id": "@", "view": "table", "options": "x"}, "bad_arguments"),
        ({"artifact_id": "@", "view": "health.trend"}, "view_unavailable"),
    ],
)
def test_show_view_errors_reach_the_model(arguments, code):
    turn = TurnViews()
    stamped = turn.record("get_health", {}, {"distribution": HEALTH["distribution"]}, artifact_type="health")
    if arguments.get("artifact_id") == "@":
        arguments = {**arguments, "artifact_id": stamped[ARTIFACT_ID_FIELD]}
    result = turn.show(arguments)
    assert result["error_code"] == code
    assert result["error"]
    assert turn.parts == []


def test_a_builder_that_raises_is_an_error_the_model_sees():
    register_view_builder("test.broken", description="raises", options=frozenset())(
        lambda artifact, options: {}["missing"]
    )
    try:
        turn = TurnViews()
        stamped = turn.record("get_x", {}, {"ok": 1})
        result = turn.show({"artifact_id": stamped[ARTIFACT_ID_FIELD], "view": "test.broken"})
    finally:
        view_builders._impls.pop("test.broken")
    assert result["error_code"] == "builder_failed"


def test_a_failed_tool_result_is_not_viewable():
    turn = TurnViews()
    stamped = turn.record("get_health", {}, {"error": "boom"}, artifact_type="health")
    result = turn.show({"artifact_id": stamped[ARTIFACT_ID_FIELD], "view": "table"})
    assert result["error_code"] == "artifact_failed"


def test_show_view_is_chat_only():
    assert get_registered_tool(SHOW_VIEW_TOOL) is None
    assert SHOW_VIEW_TOOL not in {tool.entry.name for tool in get_tool_catalog(None)}


def test_the_envelope_takes_the_stamped_id_and_keeps_data_unchanged():
    turn = TurnViews()
    stamped = turn.record("get_risk", {}, RISK, artifact_type="risk")
    artifact_id, data = turn.unstamp(stamped)
    envelope = create_artifact_envelope(
        tool_name="get_risk",
        artifact_type="risk",
        presentation="risk",
        data=data,
        artifact_id=artifact_id,
    )
    assert envelope["id"] == stamped[ARTIFACT_ID_FIELD]
    assert envelope["data"] == RISK


def test_a_tools_own_artifact_id_field_is_data_not_an_id():
    tool_data = {"artifact_id": "a" * 32, "score": 1}
    turn = TurnViews()
    # Never recorded: the field is not a stamp.
    assert turn.unstamp(tool_data) == (None, tool_data)
    envelope = create_artifact_envelope(
        tool_name="get_risk", artifact_type="risk", presentation="risk", data=tool_data
    )
    assert envelope["id"] != tool_data["artifact_id"]
    assert envelope["data"] == {"artifact_id": "a" * 32, "score": 1}

    # Recorded: the stamp wins on the model's copy, the stored data keeps the tool's value.
    stamped = turn.record("get_risk", {}, tool_data, artifact_type="risk")
    artifact_id, data = turn.unstamp(stamped)
    assert artifact_id == stamped[ARTIFACT_ID_FIELD] != tool_data["artifact_id"]
    assert data == {"artifact_id": "a" * 32, "score": 1}
    # An equal copy that was not the dict handed out is not a stamp either.
    assert turn.unstamp(dict(stamped)) == (None, dict(stamped))


@pytest.mark.parametrize("bad", ["", "x" * 32, "A" * 32, 7, "a" * 31])
def test_the_envelope_refuses_a_malformed_id(bad):
    with pytest.raises(ValueError):
        create_artifact_envelope(
            tool_name="t", artifact_type="generic", presentation="generic", data={}, artifact_id=bad
        )


# ---------------------------------------------------------------------------
# The chat stream
# ---------------------------------------------------------------------------


def _call(tool_id: str, name: str, arguments: dict) -> ChatStreamEvent:
    return ChatStreamEvent(
        type="tool_start", tool_call=ChatToolCall(id=tool_id, name=name, arguments=arguments)
    )


@pytest.mark.asyncio
async def test_show_view_streams_a_view_event_and_stores_the_view():
    app = await _make_app()

    def turns(index: int) -> list[ChatStreamEvent]:
        if index == 0:
            return [_call("t1", "get_dead_code", {})]
        if index == 1:
            tool_message = provider.calls[1]["messages"][-1]
            artifact_id = json.loads(tool_message["content"])[ARTIFACT_ID_FIELD]
            return [_call("t2", SHOW_VIEW_TOOL, {"artifact_id": artifact_id, "view": "dead_code.by_tier"})]
        return [ChatStreamEvent(type="text_delta", text="Most findings are medium.")]

    provider = _ScriptedProvider(turns)
    execute = AsyncMock(return_value=DEAD_CODE)

    with patch(_PROVIDER, return_value=provider), patch(
        "repowise.server.chat_tools.execute_tool", execute
    ):
        events = _data_events(await _post(app, {"message": "Show dead code by tier"}))

    types = [event["type"] for event in events]
    assert types[:5] == ["tool_start", "tool_result", "tool_start", "tool_result", "view"]
    assert types[-1] == "done"
    execute.assert_awaited_once()
    view = events[4]
    assert view["tool_id"] == "t2"
    artifact = events[1]["artifact"]
    assert view["view"]["source"]["artifact_id"] == artifact["id"]
    assert ARTIFACT_ID_FIELD not in artifact["data"]
    assert [row["findings"] for row in view["view"]["data"]["rows"]] == [0, 15, 0]

    stored = await _stored_messages(app, events[-1]["conversation_id"])
    assert stored[-1]["content"]["views"] == [view["view"]]


@pytest.mark.asyncio
async def test_a_turn_without_show_view_stores_no_views():
    app = await _make_app()
    provider = _ScriptedProvider([[_call("t1", "get_risk", {})], [ChatStreamEvent(type="text_delta", text="ok")]])
    with patch(_PROVIDER, return_value=provider), patch(
        "repowise.server.chat_tools.execute_tool", AsyncMock(return_value=RISK)
    ):
        events = _data_events(await _post(app, {"message": "risk?"}))
    assert "view" not in [event["type"] for event in events]
    stored = await _stored_messages(app, events[-1]["conversation_id"])
    assert "views" not in stored[-1]["content"]
