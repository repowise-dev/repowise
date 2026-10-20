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
    part.pop("receipt", None)
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


def test_health_bands_reads_the_overview_band_counts_too():
    bands = HEALTH["distribution"]["bands"]
    overview = {"code_health": {"files_by_band": {b: v["files"] for b, v in bands.items()}}}
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


def _overview_changes(coverage: float = 0.5, /, **provenance: Any) -> dict:
    return {
        "changes": {
            "window": {"days": 90, "since": "2025-12-15T00:00:00+00:00", "until": "2026-03-15"},
            "total": 4,
            "by_category": {"feature": 3, "fix": 1},
            "by_week": [{"week": "2026-03-02", "changes": 3}, {"week": "2026-03-09", "changes": 1}],
            "provenance": provenance
            or {
                "agent_changes": 1,
                "human_attested_changes": 1,
                "unknown_changes": 2,
                "coverage": coverage,
                "agent_share": 0.5,
            },
        }
    }


def test_changes_by_category_and_week_plot_the_source_counts():
    data = _overview_changes()
    part = _build(data, "overview", "get_overview", "changes.by_category")
    assert part["data"]["rows"] == [
        {"category": "feature", "changes": 3},
        {"category": "fix", "changes": 1},
    ]
    assert "90 days" in part["summary"]
    week = _build(data, "overview", "get_overview", "changes.by_week")
    assert week["chart_type"] == "line"
    assert _series(week, "changes") == [3, 1]
    # A shorter indexed history names where it starts instead of the full window.
    data["changes"]["window"]["history_from"] = "2026-03-02T09:00:00+00:00"
    week = _build(data, "overview", "get_overview", "changes.by_week")
    assert "since 2026-03-02" in week["summary"] and "90 days" not in week["summary"]


def test_changes_provenance_keeps_unknown_as_its_own_band():
    part = _build(_overview_changes(), "overview", "get_overview", "changes.provenance")
    assert part["chart_type"] == "donut"
    assert part["data"]["rows"] == [
        {"source": "agent", "changes": 1},
        {"source": "human attested", "changes": 1},
        {"source": "unknown", "changes": 2},
    ]
    assert part["coverage"] == {"metric": "attribution", "ratio": 0.5}


@pytest.mark.parametrize(
    ("data", "code", "coverage"),
    [
        (_overview_changes(0.2), "insufficient_coverage", 0.2),
        (
            _overview_changes(unknown_changes=4, coverage=0.2, agent_share=None),
            "insufficient_coverage",
            0.2,
        ),
        (_overview_changes(unavailable="attribution_off"), "attribution_off", None),
        ({"changes": {"total": 0}}, "view_unavailable", None),
    ],
)
def test_changes_provenance_is_refused_without_enough_coverage(data, code, coverage):
    turn = TurnViews()
    stamped = turn.record("get_overview", {}, data, artifact_type="overview")
    result = turn.show({"artifact_id": stamped[ARTIFACT_ID_FIELD], "view": "changes.provenance"})
    assert result["error_code"] == code
    assert result.get("coverage") == coverage
    assert turn.parts == []


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


# ---------------------------------------------------------------------------
# References and empty charts
# ---------------------------------------------------------------------------


def test_a_chart_whose_series_are_all_zero_is_no_data():
    empty = {"tiers": {tier: {"count": 0, "findings": []} for tier in ("high", "medium", "low")}}
    with pytest.raises(ViewError) as err:
        _build(empty, "dead_code", "get_dead_code", "dead_code.by_tier")
    assert err.value.code == "no_data"


def test_the_stamp_is_the_first_key_and_survives_truncation():
    from repowise.server.chat_context import bound_tool_messages
    from repowise.server.chat_prompt import tool_result_message

    stamped = TurnViews().record("get_risk", {}, {"rows": ["x" * 200_000]}, artifact_type="risk")
    assert next(iter(stamped)) == ARTIFACT_ID_FIELD
    [bounded] = bound_tool_messages([tool_result_message("t1", "get_risk", stamped)])
    assert stamped[ARTIFACT_ID_FIELD] in json.loads(bounded["content"])["excerpt"][:100]


def test_latest_skips_a_failed_result_and_an_unknown_tool_resolves_to_nothing():
    turn = TurnViews()
    good = turn.record("get_risk", {}, RISK, artifact_type="risk")
    turn.record("get_risk", {}, {"error": "boom"}, artifact_type="risk")
    assert turn.artifacts.resolve("latest:get_risk").artifact_id == good[ARTIFACT_ID_FIELD]
    assert turn.artifacts.resolve("latest:get_health") is None
    assert [ref["artifact_id"] for ref in turn.artifacts.available()] == [good[ARTIFACT_ID_FIELD]]


def test_a_legacy_row_is_indexed_under_the_id_its_replay_shows():
    from repowise.server.chat_prompt import stored_messages_to_llm_format
    from repowise.server.chat_references import ArtifactIndex
    from repowise.server.chat_seams import StoredMessage

    legacy = StoredMessage(
        id="m2",
        role="assistant",
        content={"text": "", "tool_calls": [{"id": "t1", "name": "get_risk", "result": RISK}]},
    )
    history = [StoredMessage(id="m1", role="user", content={"text": "q"}), legacy]
    index = ArtifactIndex.from_history(history)
    replayed = json.loads(stored_messages_to_llm_format(history)[-1]["content"])

    artifact = index.resolve(replayed[ARTIFACT_ID_FIELD])
    assert artifact is not None and artifact.artifact_type == "risk"
    assert index.available()[0]["age"] == "1 turn ago"
    assert build_view(artifact, "risk.hotspots", {}, part_id="v1")["kind"] == "chart"


def test_the_prompt_explains_views_only_when_show_view_is_offered():
    from repowise.server.chat_prompt import _build_system_prompt

    tools = get_tool_catalog(None)
    assert "latest:<tool_name>" in _build_system_prompt("demo", "/tmp", tools, shows_views=True)
    assert "show_view" not in _build_system_prompt("demo", "/tmp", tools)


def test_a_latest_reference_with_only_a_failed_result_lists_nothing_to_cite():
    turn = TurnViews()
    turn.record("get_risk", {}, {"error": "boom"}, artifact_type="risk")
    result = turn.show({"artifact_id": "latest:get_risk", "view": "risk.hotspots"})
    assert result["error_code"] == "unknown_artifact"
    assert result["available"] == []


def test_a_latest_receipt_names_the_result_it_picked():
    turn = TurnViews()
    stamped = turn.record("get_risk", {}, RISK, artifact_type="risk")
    receipt = turn.show({"artifact_id": "latest:get_risk", "view": "risk.hotspots"})
    assert receipt["resolved_artifact_id"] == stamped[ARTIFACT_ID_FIELD]
    assert receipt["source_tool"] == "get_risk"
    plain = turn.show({"artifact_id": stamped[ARTIFACT_ID_FIELD], "view": "risk.hotspots"})
    assert "resolved_artifact_id" not in plain


def test_latest_across_turns_picks_a_later_legacy_row():
    from repowise.server.chat_references import ArtifactIndex
    from repowise.server.chat_seams import StoredMessage

    first = TurnViews()
    stamped = first.record("get_risk", {}, RISK, artifact_type="risk")
    artifact_id, data = first.unstamp(stamped)
    envelope = create_artifact_envelope(
        tool_name="get_risk", artifact_type="risk", presentation="risk", data=data, artifact_id=artifact_id
    )
    history = [
        StoredMessage(id="m1", role="user", content={"text": "q"}),
        StoredMessage(
            id="m2",
            role="assistant",
            content={"tool_calls": [{"id": "t1", "name": "get_risk", "artifact": envelope}]},
        ),
        StoredMessage(id="m3", role="user", content={"text": "again"}),
        StoredMessage(
            id="m4",
            role="assistant",
            content={"tool_calls": [{"id": "t2", "name": "get_risk", "result": RISK}]},
        ),
    ]
    index = ArtifactIndex.from_history(history)
    latest = index.resolve("latest:get_risk")
    assert latest is not None and latest.artifact_id != artifact_id
    assert [ref["age"] for ref in index.available()] == ["1 turn ago", "2 turns ago"]
    assert index.resolve(artifact_id).artifact_type == "risk"


def test_only_a_chart_with_rows_and_string_series_can_be_all_zero():
    from repowise.server.chat_views import _all_zero

    assert _all_zero({"series": ["n"], "data": {"rows": [{"n": 0}, {"n": None}]}})
    assert not _all_zero({"series": ["n"], "data": {"rows": []}})
    assert not _all_zero({"series": [{"key": "n"}], "data": {"rows": [{"n": 0}]}})
    assert not _all_zero({"series": "n", "data": {"rows": [{"n": 0}]}})
    assert not _all_zero({"series": ["n"], "data": {"rows": [{"n": 0}, {"n": 2}]}})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("GET https://g.example/v1?alt=sse&key=AIzaQ&x=1", "GET https://g.example/v1?alt=sse&key=[redacted]&x=1"),
        ("Authorization: Bearer abcdefgh12345678", "Authorization: Bearer [redacted]"),
        ("no secret at /repo/a.py:12", "no secret at /repo/a.py:12"),
    ],
)
def test_query_keys_and_bearer_tokens_are_redacted(text, expected):
    from repowise.core.redaction import redact_secrets

    assert redact_secrets(text) == expected


# ---------------------------------------------------------------------------
# chart: a spec over any list
# ---------------------------------------------------------------------------


def _show_chart(data: dict, **options: Any) -> tuple[TurnViews, dict]:
    turn = TurnViews()
    stamped = turn.record("get_health", {}, data, artifact_type="health")
    return turn, turn.show(
        {"artifact_id": stamped[ARTIFACT_ID_FIELD], "view": "chart", "options": options}
    )


def test_chart_draws_a_spec_and_its_receipt_names_the_kind_and_why():
    turn, receipt = _show_chart(DEAD_CODE, rows="tiers.medium.findings", x="file_path", y="lines")
    assert receipt["chart_type"] == "bar" and receipt["reason"]
    part = turn.parts[0]
    assert "receipt" not in part
    ViewPartList.model_validate(turn.parts)
    assert part["data"]["rows"] == [{"file_path": {"type": "file", "id": "x.py"}, "lines": 307}]
    assert part["title"] == "Lines by file path"
    assert part["as_of"] == "4c1e31fe3a9b"


def test_chart_summary_says_when_the_tool_returned_only_part_of_a_list():
    data = {"files": [{"kind": "py", "n": 1}, {"kind": "ts", "n": 2}], "files_total": 9}
    _, receipt = _show_chart(data, x="kind", subtitle="Files by language.")
    assert receipt["chart_type"] == "donut"
    assert receipt["summary"].startswith("Files by language. 2 rows of files")
    assert "returned 2 of 9 rows" in receipt["summary"]


def test_chart_errors_list_the_fields_without_people():
    _, result = _show_chart(HEALTH, rows="high_leverage_files", x="max_ccnx_value")
    assert result["error_code"] == "bad_spec"
    assert "'max_ccn'" in result["error"]
    assert result["fields"] == {"file_path": "text", "score": "number", "max_ccn": "number"}
    # No near miss and no example: guessing a field would chart another measure.
    assert "suggested_spec" not in result


def test_a_suggestion_says_what_it_gave_up():
    _, result = _show_chart(
        HEALTH,
        rows="high_leverage_files",
        x="file_path",
        y="score",
        filter=[{"field": "max_ccn", "op": "gt", "value": 1000}],
    )
    assert result["error_code"] == "no_data"
    assert result["suggested_spec"] == {"rows": "high_leverage_files", "x": "file_path", "y": "score"}
    assert result["adjustments"] == ["filter dropped, so every row is charted"]


_PACKAGES = {
    "files": [
        {"package": "core", "directory": "core/a", "file_path": "x.py", "nloc": 10, "score": 9.0},
        {"package": "core", "directory": "core/b", "file_path": "y.py", "nloc": 30, "score": 5.0},
        {"package": "web", "directory": "web/src", "file_path": "z.ts", "nloc": 5, "score": 7.0},
    ]
}


@pytest.mark.parametrize(
    "options",
    [
        {"kind": "treemap", "x": "directory", "y": "nloc", "aggregate": "sum",
         "groups": ["package", "directory"], "color": "package"},
        {"kind": "treemap", "x": "file_path", "y": "nloc", "groups": ["package", "directory"],
         "color": "directory"},
        {"kind": "treemap", "x": "directory", "y": "nloc", "aggregate": "sum", "groups": ["nloc"]},
        {"kind": "bar", "x": "directory", "y": "nloc", "aggregate": "sum", "series": "directory"},
        {"kind": "scatter", "x": "nloc", "y": "score", "series": "nloc"},
        {"kind": "heatmap", "x": "directory", "y": "nloc", "aggregate": "sum",
         "series": "package", "color": "package"},
        {"kind": "heatmap", "x": "package", "y": "nloc", "aggregate": "sum", "series": "directory",
         "groups": ["package"]},
        {"kind": "treemap", "x": "directory", "y": "nloc", "aggregate": "sum", "color": "nloc"},
        {"kind": "treemap", "x": "directory", "y": "nloc", "aggregate": "sum", "color": "directory"},
        {"kind": "treemap", "x": "directory", "y": "score", "aggregate": "avg", "weight": "nloc",
         "color": "nloc"},
        {"kind": "treemap", "x": "directory", "y": "nloc", "aggregate": "sum", "groups": ["package"],
         "color": "package", "top": 1, "other": True},
    ],
)
def test_one_field_in_two_roles_draws_one_column(options):
    turn, receipt = _show_chart(_PACKAGES, rows="files", **options)
    assert "error" not in receipt
    ViewPartList.model_validate(turn.parts)
    keys = [c["key"] for c in turn.parts[0]["data"]["columns"]]
    assert len(keys) == len(set(keys))


def test_a_repeated_person_field_is_refused_not_dropped():
    data = {"files": [{**row, "owner": "ana"} for row in _PACKAGES["files"]]}
    _, result = _show_chart(
        data, rows="files", kind="treemap", x="directory", y="nloc", aggregate="sum",
        groups=["owner", "directory"],
    )
    assert result["error_code"] == "person_grouping"


def test_a_risk_score_is_refused_before_any_suggestion():
    _, result = _show_chart(RISK, rows="global_hotspots", x="file_path", y="hotspot_scroe")
    assert result["error_code"] == "risk_score_series"
    assert "suggested_spec" not in result


def test_a_near_miss_field_name_is_read_as_the_field_and_said():
    _, result = _show_chart(HEALTH, rows="high_leverage_files", x="max_cnn", y="score")
    assert "error" not in result
    assert "field 'max_cnn' read as 'max_ccn'" in result["notes"]


@pytest.mark.parametrize(
    ("options", "code"),
    [
        ({"rows": "high_leverage_files", "x": "primary_owner"}, "person_grouping"),
        (
            {"rows": "high_leverage_files", "x": "file_path", "filter": [{"field": "primary_owner", "value": "S"}]},
            "person_grouping",
        ),
        ({"rows": "global_hotspots", "x": "file_path", "y": "hotspot_score"}, "risk_score_series"),
        ({"rows": "global_hotspots", "x": "hotspot_score", "y": "fix_count"}, "risk_score_series"),
        ({"rows": "changes", "x": "category", "series": "agent_label"}, "attribution_figure"),
        ({"rows": "nope", "x": "a"}, "bad_path"),
        ({"x": "a", "colour": "red"}, "view_unavailable"),
    ],
)
def test_chart_keeps_every_view_guard(options, code):
    data = {
        **HEALTH,
        **RISK,
        "changes": [{"category": "fix", "agent_label": "agent"}],
    }
    turn, result = _show_chart(data, **options)
    assert result["error_code"] == code
    assert turn.parts == []


def test_chart_bool_x_values_are_text_cells():
    turn, _ = _show_chart({"rows": [{"ok": True}, {"ok": False}, {"ok": True}]}, x="ok")
    assert [row["ok"] for row in turn.parts[0]["data"]["rows"]] == ["true", "false"]


def test_chart_never_splits_series_by_a_risk_score():
    data = {"rows": [{"file_path": "a", "risk_level": "high", "lines": 1}]}
    _, result = _show_chart(data, x="file_path", series="risk_level")
    assert result["error_code"] == "risk_score_series"


def test_chart_series_split_is_still_risk_guarded_by_its_measure():
    data = {"rows": [{"file_path": "a", "tier": "x", "risk_score": 1}]}
    _, result = _show_chart(data, x="file_path", y="risk_score", series="tier", aggregate="sum")
    assert result["error_code"] == "risk_score_series"


# ---------------------------------------------------------------------------
# Named views take a kind; maps chart; errors carry a fix
# ---------------------------------------------------------------------------


def _show(data: dict, artifact_type: str, tool: str, view: str, **options: Any) -> tuple[TurnViews, dict]:
    turn = TurnViews()
    stamped = turn.record(tool, {}, data, artifact_type=artifact_type)
    return turn, turn.show({"artifact_id": stamped[ARTIFACT_ID_FIELD], "view": view, "options": options})


@pytest.mark.parametrize(
    ("data", "artifact_type", "tool", "view"),
    [
        (HEALTH, "health", "get_health", "health.bands"),
        (_overview_changes(), "overview", "get_overview", "changes.by_category"),
    ],
)
def test_a_named_view_draws_as_a_donut_and_its_receipt_has_the_shares(data, artifact_type, tool, view):
    turn, receipt = _show(data, artifact_type, tool, view, kind="donut")
    assert receipt["chart_type"] == "donut" and turn.parts[0]["chart_type"] == "donut"
    shares = receipt["figures"]["shares"]
    assert sum(s["value"] for s in shares) == receipt["figures"]["total"]
    ViewPartList.model_validate(turn.parts)


def test_a_named_view_keeps_its_kind_when_the_asked_one_does_not_fit_and_says_so():
    turn, receipt = _show(HEALTH, "health", "get_health", "health.bands", kind="scatter")
    assert turn.parts[0]["chart_type"] == "bar"
    assert "not scatter" in receipt["kind_note"]
    _, table = _show(HEALTH, "health", "get_health", "health.trend", kind="table")
    assert table["kind"] == "table"


def test_a_map_of_numbers_charts_as_key_and_value_rows():
    turn, receipt = _show_chart(
        _overview_changes(), rows="changes.by_category", x="key", y="value", kind="donut"
    )
    assert receipt["chart_type"] == "donut"
    assert turn.parts[0]["data"]["rows"] == [{"key": "feature", "value": 3}, {"key": "fix", "value": 1}]


def test_table_columns_it_cannot_show_come_back_with_options_that_work():
    _, result = _show(HEALTH, "health", "get_health", "table", path="high_leverage_files", columns=["file_path", "fix_count"])
    assert result["error_code"] == "bad_arguments"
    assert result["suggested_options"]["columns"] == ["file_path"]


def test_a_view_of_this_conversation_redraws_as_another_kind():
    turn, _ = _show(HEALTH, "health", "get_health", "health.bands")
    redrawn = turn.show({"artifact_id": "v1", "view": "chart", "options": {"kind": "donut"}})
    assert redrawn["chart_type"] == "donut" and redrawn["redrawn_from"] == "v1"
    refused = turn.show({"artifact_id": "v1", "view": "chart", "options": {"kind": "scatter"}})
    assert refused["error_code"] == "invalid_view"
    assert "call chart" in refused["error"]


def test_a_tool_with_no_declared_basis_names_itself_not_unresolved():
    turn, _ = _show_chart({"rows": [{"k": "a"}, {"k": "b"}]}, x="k")
    assert turn.parts[0]["basis"]["source"] == "get_health"
