"""The ``chart`` view spec: parsing, field checks, filters, grouping, top + Other and the kind choice."""

from __future__ import annotations

from typing import Any

import pytest

from repowise.server.chat_view_spec import (
    OTHER_LABEL,
    SpecError,
    check_fields,
    default_title,
    field_types,
    parse_spec,
    transform,
)

FINDINGS = [
    {"file_path": "a.py", "kind": "unused_export", "lines": 10, "confidence": 0.9, "seen": "2026-03-02"},
    {"file_path": "b.py", "kind": "unused_export", "lines": 4, "confidence": 0.5, "seen": "2026-03-09"},
    {"file_path": "c.py", "kind": "unreachable_file", "lines": 120, "confidence": 0.7, "seen": "2026-03-02"},
    {"file_path": "d.ts", "kind": "zombie_package", "lines": 0, "confidence": 0.4, "seen": "2026-03-16"},
    {"file_path": "e.ts", "kind": "unused_export", "lines": 7, "confidence": None, "seen": "2026-03-16"},
]


SIX = [*FINDINGS, {"file_path": "f.ts", "kind": "unused_export", "lines": 1, "confidence": 0.1, "seen": "2026-03-16"}]


def _run(rows: list[dict[str, Any]] = FINDINGS, **options: Any):
    spec = parse_spec(options)
    types = field_types(rows)
    check_fields(spec, types)
    return transform(spec, rows, types)


def _column(result, key: str) -> list:
    return [row[key] for row in result.rows]


# ---------------------------------------------------------------------------
# Fields and validation
# ---------------------------------------------------------------------------


def test_field_types_infer_number_time_and_text_and_skip_nested():
    rows = [
        {"n": 1, "when": "2026-03-02T09:00:00Z", "name": "x", "flag": True, "deep": {"a": 1}, "Bad": 1},
        {"n": 2.5, "when": "2026-03", "name": 3, "flag": None, "deep": None, "Bad": 2},
    ]
    assert field_types(rows) == {"n": "number", "when": "time", "name": "text", "flag": "text"}


def test_an_unknown_field_suggests_close_matches_and_lists_the_fields():
    with pytest.raises(SpecError) as exc:
        _run(x="knid")
    assert "'kind'" in str(exc.value)
    assert exc.value.fields == {
        "file_path": "text",
        "kind": "text",
        "lines": "number",
        "confidence": "number",
        "seen": "time",
    }


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({}, "pass x"),
        ({"x": "kind", "group_by": "seen"}, "same axis"),
        ({"x": "kind", "y": "kind"}, "x cannot also be a y field"),
        ({"x": "kind", "y": "lines", "aggregate": "median"}, "not one of"),
        ({"x": "kind", "y": "lines", "aggregate": "count"}, "drop y"),
        ({"x": "kind", "aggregate": "sum"}, "needs y"),
        ({"x": "kind", "y": "lines", "group_by": "kind"}, "group_by needs aggregate"),
        ({"x": "kind", "y": ["lines", "confidence"], "series": "seen"}, "y takes one field"),
        ({"x": "kind", "filter": [{"field": "lines", "op": "between", "value": 1}]}, "op 'between'"),
        ({"x": "kind", "filter": [{"field": "kind", "op": "in", "value": "a"}]}, "non-empty list"),
        ({"x": "kind", "filter": [{"field": "lines", "op": "gt", "value": True}]}, "number or text"),
        ({"x": "kind", "top": 0}, "top must be"),
        ({"x": "kind", "top": 100, "other": True}, "from 1 to 99"),
        ({"x": "kind", "other": True}, "needs top"),
        ({"x": "kind", "y": "lines", "aggregate": "avg", "top": 2, "other": True}, "count or sum"),
        ({"x": "kind", "kind": "pie"}, "kind 'pie'"),
        ({"x": "kind", "sort": {"field": "kind", "order": "up"}}, "asc"),
        ({"x": "kind", "title": ""}, "title must be text"),
    ],
)
def test_a_malformed_spec_says_what_to_change(options, message):
    with pytest.raises(SpecError) as exc:
        parse_spec(options)
    assert message in str(exc.value)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"x": "kind", "y": "file_path", "aggregate": "sum"}, "y fields must be numeric"),
        ({"x": "kind", "y": "lines", "kind": "scatter"}, "scatter needs a numeric x"),
        ({"x": "kind", "sort": {"field": "lines"}}, "must be one of ['kind', 'count']"),
        ({"x": "kind", "filter": [{"field": "owner", "value": 1}]}, "filter field 'owner'"),
    ],
)
def test_a_spec_that_does_not_fit_the_rows_is_refused(options, message):
    with pytest.raises(SpecError) as exc:
        _run(**options)
    assert message in str(exc.value)


# ---------------------------------------------------------------------------
# Filters, grouping and aggregates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flt", "kept"),
    [
        ({"field": "kind", "value": "unused_export"}, 3),
        ({"field": "kind", "op": "ne", "value": "unused_export"}, 2),
        ({"field": "lines", "op": "lt", "value": 7}, 2),
        ({"field": "lines", "op": "lte", "value": 7}, 3),
        ({"field": "lines", "op": "gt", "value": 7}, 2),
        ({"field": "lines", "op": "gte", "value": 120}, 1),
        ({"field": "kind", "op": "in", "value": ["zombie_package", "unreachable_file"]}, 2),
        ({"field": "file_path", "op": "contains", "value": ".ts"}, 2),
        ({"field": "file_path", "op": "prefix", "value": "a"}, 1),
        ({"field": "seen", "op": "gte", "value": "2026-03-09"}, 3),
        # A null and a type mismatch never pass an ordering filter.
        ({"field": "confidence", "op": "lt", "value": 1}, 4),
        ({"field": "lines", "op": "gt", "value": "3"}, 0),
    ],
)
def test_each_filter_op(flt, kept):
    if kept == 0:
        with pytest.raises(SpecError) as exc:
            _run(x="kind", filter=[flt])
        assert exc.value.code == "no_data"
        return
    assert _run(x="kind", filter=[flt]).matched == kept


def test_count_by_category_is_largest_first():
    result = _run(x="kind")
    assert _column(result, "kind") == ["unused_export", "unreachable_file", "zombie_package"]
    assert _column(result, "count") == [3, 1, 1]
    assert result.sort == "desc"
    assert result.columns == [("kind", "Kind"), ("count", "Count")]


@pytest.mark.parametrize(
    ("aggregate", "values"),
    [("sum", [21, 120, 0]), ("avg", [7.0, 120.0, 0.0]), ("min", [4, 120, 0]), ("max", [10, 120, 0])],
)
def test_aggregates_by_group(aggregate, values):
    result = _run(group_by="kind", y="lines", aggregate=aggregate, sort={"field": "kind", "order": "desc"})
    assert _column(result, "kind") == ["zombie_package", "unused_export", "unreachable_file"]
    assert _column(result, "lines") == [values[2], values[0], values[1]]


def test_avg_skips_nulls_and_float_sums_lose_their_noise():
    rows = [{"k": "a", "v": 0.1}, {"k": "a", "v": 0.2}, {"k": "a", "v": None}]
    assert _column(_run(rows, x="k", y="v", aggregate="sum"), "v") == [0.3]
    assert _column(_run(rows, x="k", y="v", aggregate="avg"), "v") == [0.15]


def test_series_splits_a_count_into_one_column_per_value():
    result = _run(x="seen", series="kind")
    assert result.series == ["s1", "s2", "s3"]
    assert [label for _, label in result.columns[1:]] == [
        "unused_export",
        "unreachable_file",
        "zombie_package",
    ]
    # Dates run oldest first; a missing combination counts zero.
    assert result.rows[0] == {"seen": "2026-03-02", "s1": 1, "s2": 1, "s3": 0}
    assert _column(result, "seen") == ["2026-03-02", "2026-03-09", "2026-03-16"]


def test_too_many_series_values_is_refused():
    rows = [{"x": "a", "s": f"v{i}"} for i in range(9)]
    with pytest.raises(SpecError, match="at most 8"):
        _run(rows, x="x", series="s")


def test_unaggregated_series_with_repeats_asks_for_an_aggregate():
    rows = [{"x": "a", "s": "p", "v": 1}, {"x": "a", "s": "p", "v": 2}]
    with pytest.raises(SpecError, match="add aggregate"):
        _run(rows, x="x", y="v", series="s")


def test_a_repeated_x_without_aggregate_asks_for_one_outside_a_scatter():
    with pytest.raises(SpecError, match="x 'kind' repeats"):
        _run(x="kind", y="lines")
    # Points of a scatter may share an x.
    rows = [{"a": 1, "b": 2}, {"a": 1, "b": 3}]
    assert len(_run(rows, x="a", y="b").rows) == 2


def test_an_unaggregated_pivot_leaves_missing_combinations_null():
    rows = [{"x": "a", "s": "p", "v": 1}, {"x": "b", "s": "q", "v": 2}]
    result = _run(rows, x="x", y="v", series="s")
    assert result.rows == [{"x": "a", "s1": 1, "s2": None}, {"x": "b", "s1": None, "s2": 2}]


def test_bool_fields_group_and_split_as_text():
    rows = [{"ok": True, "k": "a"}, {"ok": False, "k": "a"}, {"ok": True, "k": "b"}]
    by_flag = _run(rows, x="ok")
    assert _column(by_flag, "ok") == [True, False] and _column(by_flag, "count") == [2, 1]
    split = _run(rows, x="k", series="ok")
    assert [label for _, label in split.columns[1:]] == ["true", "false"]


def test_rows_without_x_are_counted_and_left_out():
    rows = [{"k": "a"}, {"k": None}, {"k": "a"}]
    result = _run(rows, x="k")
    assert result.missing_x == 1 and _column(result, "count") == [2]


# ---------------------------------------------------------------------------
# Top, Other and caps
# ---------------------------------------------------------------------------


def test_top_with_other_adds_up_the_rest():
    result = _run(x="file_path", y="lines", aggregate="sum", top=2, other=True)
    assert _column(result, "file_path") == ["c.py", "a.py", f"{OTHER_LABEL} (3 more)"]
    assert _column(result, "lines") == [120, 10, 11]
    # The Other row breaks the order, so no sort is claimed, and nothing is cut.
    assert result.sort is None and result.other
    assert result.total_rows == len(result.rows)


def test_the_other_bucket_never_merges_with_a_real_other_category():
    rows = [{"k": "a"}, {"k": "a"}, {"k": "Other"}, {"k": "b"}, {"k": "c"}]
    result = _run(rows, x="k", top=2, other=True)
    assert _column(result, "k") == ["a", "Other", f"{OTHER_LABEL} (2 more)"]
    assert _column(result, "count") == [2, 1, 2]


@pytest.mark.parametrize("x", ["seen", "lines"])
def test_other_needs_a_category_x(x):
    with pytest.raises(SpecError, match="other needs a category x"):
        _run(x=x, top=1, other=True)


def test_top_without_other_is_truncated():
    result = _run(x="file_path", y="lines", aggregate="sum", top=2)
    assert len(result.rows) == 2 and result.total_rows == 5


def test_a_chart_keeps_at_most_100_points_and_a_time_axis_its_newest():
    rows = [{"day": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "n": i} for i in range(150)]
    result = _run(rows, x="day", y="n")
    assert len(result.rows) == 100 and result.total_rows == 150
    assert result.rows[-1]["n"] == 149
    bars = _run([{"k": f"k{i}"} for i in range(150)], x="k")
    assert len(bars.rows) == 100 and bars.total_rows == 150


def test_the_transform_is_one_pass_over_large_inputs():
    rows = [{"k": f"k{i % 7}", "v": i} for i in range(50_000)]
    result = _run(rows, x="k", y="v", aggregate="sum")
    assert sum(_column(result, "v")) == sum(range(50_000))


# ---------------------------------------------------------------------------
# Kind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rows", "options", "kind"),
    [
        (FINDINGS, {"x": "seen"}, "line"),
        (FINDINGS, {"x": "kind"}, "donut"),
        (FINDINGS, {"x": "kind", "y": "lines", "aggregate": "sum"}, "donut"),
        (FINDINGS, {"x": "kind", "y": "lines", "aggregate": "avg"}, "bar"),
        (SIX, {"x": "file_path", "y": "lines", "aggregate": "sum"}, "bar"),
        (FINDINGS, {"x": "kind", "series": "seen"}, "stacked_bar"),
        (FINDINGS, {"x": "kind", "series": "seen", "y": "lines", "aggregate": "avg"}, "bar"),
        (FINDINGS, {"x": "file_path", "y": ["lines", "confidence"]}, "bar"),
        (FINDINGS, {"x": "lines", "y": "confidence"}, "scatter"),
        (FINDINGS, {"x": "kind", "kind": "line"}, "line"),
        ([{"k": f"k{i}"} for i in range(6)], {"x": "k"}, "bar"),
    ],
)
def test_auto_kind_follows_the_data_shape(rows, options, kind):
    result = _run(rows, **options)
    assert result.chart_type == kind
    assert result.reason


def test_a_scatter_keeps_one_row_per_point_with_its_label():
    result = _run(x="lines", y="confidence")
    assert result.columns[0] == ("file_path", "File path")
    assert result.rows[0] == {"file_path": "a.py", "lines": 10, "confidence": 0.9}
    split = _run(x="lines", y="confidence", series="kind")
    assert len(split.rows) == 5
    assert split.rows[0] == {"lines": 10, "s1": 0.9, "s2": None, "s3": None}


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"x": "file_path", "kind": "donut"}, "top: 4 with other: true"),
        ({"x": "kind", "kind": "stacked_bar"}, "pass series"),
    ],
)
def test_an_asked_kind_the_data_cannot_take_is_refused_with_a_fix(options, message):
    with pytest.raises(SpecError, match=message):
        _run(SIX, **options)


def test_default_titles_name_the_measure_and_axes():
    assert default_title(parse_spec({"x": "kind"})) == "Count by kind"
    assert (
        default_title(parse_spec({"x": "kind", "y": "lines", "aggregate": "sum", "series": "seen"}))
        == "Total lines by kind and seen"
    )
    assert default_title(parse_spec({"x": "lines", "y": "confidence"})) == "Confidence by lines"
