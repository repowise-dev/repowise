"""The chart spec: parsing, field checks, filters, grouping, top + Other and the kind choice."""

from __future__ import annotations

from typing import Any

import pytest

from repowise.server.chat_spec import (
    OTHER_LABEL,
    SpecError,
    default_title,
    field_types,
    fit,
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
    return transform(fit(spec, rows, types), rows, types)


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
        ({"x": "kind", "aggregate": "sum"}, "needs y"),
        ({"x": "kind", "y": ["lines", "confidence"], "series": "seen"}, "y takes one field"),
        ({"x": "kind", "filter": [{"field": "lines", "op": "between", "value": 1}]}, "op 'between'"),
        ({"x": "kind", "filter": [{"field": "kind", "op": "in", "value": "a"}]}, "non-empty list"),
        ({"x": "kind", "filter": [{"field": "lines", "op": "gt", "value": True}]}, "number or text"),
        ({"x": "kind", "top": 0}, "top must be"),
        ({"x": "kind", "kind": "radar"}, "kind 'radar'"),
        ({"x": "kind", "other": "maybe"}, "other must be"),
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
        ({"x": "kind", "y": "file_path", "aggregate": "sum"}, "it must be numeric"),
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


def test_too_many_series_values_fold_the_smallest_into_other():
    rows = [{"x": "a", "s": f"v{i}"} for i in range(9)] + [{"x": "a", "s": "v0"}]
    result = _run(rows, x="x", series="s")
    assert len(result.series) == 8
    assert result.columns[-1][1] == OTHER_LABEL
    assert result.rows[0]["s8"] == 2 and result.rows[0]["s1"] == 2
    assert any("added up as Other" in n for n in result.notes)
    # An average cannot fold, so it is still refused.
    many = [{"x": "a", "s": f"v{i}", "v": i} for i in range(9)]
    with pytest.raises(SpecError, match="at most 8"):
        _run(many, x="x", y="v", series="s", aggregate="avg")


def test_unaggregated_series_with_repeats_is_summed_and_said():
    rows = [{"x": "a", "s": "p", "v": 1}, {"x": "a", "s": "p", "v": 2}]
    result = _run(rows, x="x", y="v", series="s")
    assert result.rows == [{"x": "a", "s1": 3}]
    assert any("combined by sum" in n for n in result.notes)


def test_a_repeated_x_without_aggregate_is_combined_outside_a_scatter():
    result = _run(x="kind", y="lines")
    assert _column(result, "lines") == [120, 21, 0]
    assert any("combined by sum" in n for n in result.notes)
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
    ("options", "drawn", "note"),
    [
        ({"x": "kind", "kind": "stacked_bar"}, "bar", "pass series"),
        ({"x": "kind", "y": "lines", "aggregate": "avg", "kind": "donut"}, "bar", "does not add up"),
        ({"x": "kind", "y": "lines", "kind": "scatter"}, "bar", "numeric x"),
        ({"x": "kind", "kind": "line"}, "line", "no order"),
    ],
)
def test_an_asked_kind_the_data_cannot_take_draws_the_nearest_and_says_why(options, drawn, note):
    result = _run(SIX, **options)
    assert result.chart_type == drawn
    assert note in result.kind_note


@pytest.mark.parametrize(
    "kind",
    ["bar", "horizontal_bar", "line", "area", "donut", "scatter", "treemap", "heatmap", "table"],
)
def test_every_asked_kind_is_drawn_when_the_data_fits(kind):
    rows = [{"k": f"k{i}", "n": i + 1, "m": 2 * i, "t": f"2026-03-{i + 1:02d}"} for i in range(4)]
    options = {
        "scatter": {"x": "n", "y": "m"},
        "line": {"x": "t", "y": "n"},
        "area": {"x": "t", "y": "n"},
        "heatmap": {"x": "k", "series": "t", "aggregate": "count"},
    }.get(kind, {"x": "k", "y": "n", "aggregate": "sum"})
    result = _run(rows, kind=kind, **options)
    assert result.chart_type == kind and result.kind_note is None


def test_stacked_bar_draws_when_series_splits_a_count():
    assert _run(SIX, x="kind", series="seen", kind="stacked_bar").chart_type == "stacked_bar"


def test_a_donut_folds_past_eight_parts_into_other():
    rows = [{"k": f"k{i}"} for i in range(12) for _ in range(i + 1)]
    result = _run(rows, x="k", kind="donut")
    assert result.chart_type == "donut" and len(result.rows) == 8
    assert result.rows[-1]["k"] == f"{OTHER_LABEL} (5 more)"
    assert result.figures["total"] == sum(range(1, 13))
    assert any("added up as Other" in n for n in result.notes)


# ---------------------------------------------------------------------------
# Repairs, composite x, weights, treemaps, figures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("options", "check"),
    [
        ({"x": "kind", "kind": "pie"}, lambda r: r.chart_type == "donut"),
        ({"x": "kind", "y": "lines", "aggregate": "count"}, lambda r: r.series == ["count"]),
        ({"x": "kind", "top": 2, "other": "Other"}, lambda r: r.other),
        ({"x": "kind", "other": True}, lambda r: r.other is False and len(r.rows) == 3),
        ({"x": "file_path", "y": "lines", "top": 2, "other": True}, lambda r: r.other),
        (
            {"x": "kind", "y": "lines", "aggregate": "avg", "top": 2, "other": True},
            lambda r: not r.other,
        ),
        ({"x": "kind", "aggregate": "mean", "y": "lines"}, lambda r: r.rows[0]["lines"] == 120.0),
        ({"x": "kind", "top": "2"}, lambda r: len(r.rows) == 2),
        ({"x": "kind", "top": 500}, lambda r: len(r.rows) == 3),
        ({"x": "kind", "sort": "asc"}, lambda r: _column(r, "count") == [1, 1, 3]),
        ({"x": "Kind"}, lambda r: r.x == "kind"),
        ({"x": "kind", "y": "count"}, lambda r: r.series == ["count"]),
        (
            {"x": "kind", "filter": {"field": "lines", "op": "gt", "value": "7"}},
            lambda r: r.matched == 2,
        ),
    ],
)
def test_obvious_slips_are_repaired(options, check):
    assert check(_run(**options))


def test_a_repair_is_reported():
    assert _run(x="kind", kind="pie").notes == ["kind 'pie' read as 'donut'"]


def test_a_bool_filter_value_written_as_text_matches_the_bool():
    rows = [{"ok": True, "k": "a"}, {"ok": False, "k": "b"}]
    assert _run(rows, x="k", filter=[{"field": "ok", "value": "true"}]).matched == 1


def test_composite_x_joins_fields_with_an_arrow():
    rows = [{"a": "core", "b": "web", "w": 3}, {"a": "cli", "b": "core", "w": 5}]
    result = _run(rows, x=["a", "b"], y="w", sort={"field": "a_b", "order": "asc"})
    assert _column(result, "a_b") == ["cli → core", "core → web"]
    assert result.columns[0] == ("a_b", "A → B")


class _Meta:
    def __init__(self, aggregates, weight=None):
        self.aggregates, self.weight = aggregates, weight


def test_declared_weights_and_aggregates_shape_an_average():
    rows = [{"pkg": "p", "cov": 100.0, "lines": 1}, {"pkg": "p", "cov": 0.0, "lines": 3}]
    meta = {"cov": _Meta(("avg", "min", "max"), weight="lines"), "lines": _Meta(("sum",))}
    types = field_types(rows)
    spec = fit(parse_spec({"x": "pkg", "y": "cov", "aggregate": "sum"}), rows, types, meta)
    result = transform(spec, rows, types)
    # A sum of percentages is no measure: it is averaged, weighted by lines, and said.
    assert result.rows == [{"pkg": "p", "cov": 25.0}]
    assert result.figures["overall"] == {"Average cov": 25.0}
    assert any("weighted by lines" in n for n in result.notes)


def test_a_treemap_of_an_average_is_sized_by_its_weight_and_coloured_by_it():
    rows = [{"d": "a", "s": 9.0, "n": 10}, {"d": "a", "s": 5.0, "n": 30}, {"d": "b", "s": 7.0, "n": 5}]
    result = _run(rows, x="d", y="s", aggregate="avg", weight="n", kind="treemap")
    assert result.chart_type == "treemap" and result.series == ["n"]
    assert result.rows[0] == {"d": "a", "n": 40, "s": 6.0}
    assert any("sized by n" in note for note in result.notes)


def test_points_sort_by_a_field_they_do_not_plot():
    result = _run(x="lines", y="confidence", sort={"field": "seen", "order": "desc"}, top=2)
    assert _column(result, "file_path") == ["d.ts", "e.ts"]
    assert all("__sort" not in row for row in result.rows)


def test_a_treemap_nests_by_the_hierarchy_and_colours_by_a_field():
    rows = [
        {"package": "core", "directory": "core/a", "file": "x.py", "nloc": 10, "score": 9.0},
        {"package": "core", "directory": "core/a", "file": "y.py", "nloc": 30, "score": 5.0},
        {"package": "web", "directory": "web/src", "file": "z.ts", "nloc": 5, "score": 7.0},
    ]
    types = field_types(rows)
    options = {"x": "directory", "y": "nloc", "aggregate": "sum", "kind": "treemap", "color": "score"}
    spec = fit(parse_spec(options), rows, types, hierarchy=("package", "directory", "file"))
    result = transform(spec, rows, types)
    assert result.chart_type == "treemap" and result.groups == ["package"]
    assert result.rows[0] == {"directory": "core/a", "nloc": 40, "package": "core", "score": 7.0}
    assert ("score", "Average score") in result.columns


def test_a_treemap_coloured_by_a_group_level_reads_that_levels_column():
    rows = [
        {"package": "core", "directory": "core/a", "file": "x.py", "nloc": 10},
        {"package": "core", "directory": "core/b", "file": "y.py", "nloc": 30},
        {"package": "web", "directory": "web/src", "file": "z.ts", "nloc": 5},
    ]
    options = {"x": "directory", "y": "nloc", "aggregate": "sum", "kind": "treemap"}
    result = _run(rows, **options, groups=["package", "directory"], color="package")
    keys = [k for k, _ in result.columns]
    assert keys == ["package", "directory", "nloc"]
    assert result.groups == ["package"] and result.color == "package"
    assert result.rows[0] == {"package": "core", "directory": "core/b", "nloc": 30}
    assert result.notes[0] == (
        "groups ['directory'] are already the x or a plotted field, so they were left out; "
        "the treemap nests by package"
    )


_TREE = [
    {"package": "core", "directory": "core/a", "file": "x.py", "nloc": 10, "score": 9.0},
    {"package": "core", "directory": "core/b", "file": "y.py", "nloc": 30, "score": 5.0},
    {"package": "web", "directory": "web/src", "file": "z.ts", "nloc": 5, "score": 7.0},
]


def _tree(**options: Any):
    types = field_types(_TREE)
    spec = fit(parse_spec(options), _TREE, types, hierarchy=("package", "directory", "file"))
    return transform(spec, _TREE, types)


def test_a_treemap_whose_only_group_repeats_x_still_nests_by_the_hierarchy():
    result = _tree(x="directory", y="nloc", aggregate="sum", kind="treemap", groups=["directory"])
    assert result.groups == ["package"]
    assert result.notes[-1] == "the treemap nests by package"


def test_a_group_that_is_part_of_a_composite_x_is_left_out():
    result = _tree(x=["package", "directory"], y="nloc", aggregate="sum", kind="treemap", groups=["package"])
    assert result.groups == []
    keys = [k for k, _ in result.columns]
    assert len(keys) == len(set(keys))
    assert "groups ['package'] are already the x" in result.notes[0]


@pytest.mark.parametrize(
    ("options", "color_key"),
    [
        ({"x": "directory", "y": "nloc", "aggregate": "sum", "color": "nloc"}, "nloc_color"),
        ({"x": "directory", "y": "nloc", "aggregate": "sum", "color": "directory"}, "directory_color"),
        ({"x": "directory", "y": "score", "aggregate": "avg", "weight": "nloc", "color": "nloc"}, "nloc_color"),
    ],
)
def test_a_treemap_colour_that_is_another_role_gets_its_own_column(options, color_key):
    result = _tree(kind="treemap", **options)
    keys = [k for k, _ in result.columns]
    assert result.color == color_key and keys.count(color_key) == 1
    assert len(keys) == len(set(keys))


def test_the_other_bucket_of_a_treemap_coloured_by_a_group_keeps_one_column():
    result = _tree(
        x="directory", y="nloc", aggregate="sum", kind="treemap", color="package", top=1, other=True
    )
    assert result.color == "package" and result.other
    assert result.rows[-1]["package"] == OTHER_LABEL
    assert all(set(row) == {k for k, _ in result.columns} for row in result.rows)


def test_a_repaired_series_name_that_repeats_x_is_dropped():
    result = _run(x="kind", series="Kind")
    assert len(result.series) == 1
    assert any("series 'kind' repeats x or y" in n for n in result.notes)


def test_figures_carry_totals_and_shares_so_nobody_divides():
    result = _run(x="kind")
    assert result.figures["total"] == 5
    assert result.figures["shares"][0] == {"x": "unused_export", "value": 3, "pct": 60.0}


def test_default_titles_name_the_measure_and_axes():
    assert default_title(parse_spec({"x": "kind"})) == "Count by kind"
    assert (
        default_title(parse_spec({"x": "kind", "y": "lines", "aggregate": "sum", "series": "seen"}))
        == "Total lines by kind and seen"
    )
    assert default_title(parse_spec({"x": "lines", "y": "confidence"})) == "Confidence by lines"


def test_a_time_series_receipt_previews_its_newest_rows_and_its_first():
    rows = [{"day": f"2026-03-{i + 1:02d}", "v": i} for i in range(20)]
    result = _run(rows, x="day", y="v")
    assert result.figures["rows"][-1] == {"day": "2026-03-20", "v": 19}
    assert result.figures["first_row"] == {"day": "2026-03-01", "v": 0}
    assert result.figures["rows_note"] == "the newest 15 of 20 drawn rows"


def test_a_donut_of_one_part_says_so():
    result = _run([{"k": "a"}, {"k": "a"}], x="k", kind="donut")
    assert result.chart_type == "bar"
    assert "there is only 1 part" in result.kind_note
