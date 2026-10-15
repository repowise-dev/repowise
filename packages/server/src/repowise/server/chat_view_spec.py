"""The ``chart`` view's spec: parse and check it, then turn rows into chart rows.

Pure functions over a list of row dicts; the ``chart`` builder in
:mod:`repowise.server.chat_views` picks the rows, applies the view guards and
makes the part. One pass over the rows filters and groups; sort, top and the
Other bucket then work on the groups. Every refusal is a ``SpecError`` whose
message says what to change, and names the fields the rows hold, so the model
can fix its spec in one retry.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from repowise.server.schemas.views import VIEW_LIMITS

AGGREGATES = ("count", "sum", "avg", "min", "max")
OPS = ("eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "prefix")
KINDS = ("auto", "bar", "stacked_bar", "line", "donut", "scatter")
OPTIONS = frozenset(
    {"rows", "x", "y", "series", "filter", "group_by", "aggregate", "sort", "top", "other"}
    | {"kind", "title", "subtitle"}
)
OTHER_LABEL = "Other"

_ADDITIVE = frozenset({"count", "sum"})
_AGGREGATE_LABEL = {
    "count": "Count",
    "sum": "Total",
    "avg": "Average",
    "min": "Lowest",
    "max": "Highest",
}
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TIME = re.compile(
    r"^\d{4}-\d{2}(-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?)?$"
)
# Fields that name a point in a scatter or an unaggregated chart, in preference order.
_LABEL_FIELDS = ("file_path", "path", "name", "title", "id")
_MAX_FILTERS = 10
_MAX_TEXT = 120
_FIELDS_SHOWN = 30


class SpecError(ValueError):
    """A spec the rows cannot satisfy; the message says what to change."""

    def __init__(
        self, message: str, code: str = "bad_spec", fields: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.fields = dict(fields) if fields else None


@dataclass(frozen=True)
class Filter:
    field: str
    op: str
    value: Any


@dataclass(frozen=True)
class ChartSpec:
    x: str
    y: tuple[str, ...]
    series: str | None = None
    filters: tuple[Filter, ...] = ()
    aggregate: str | None = None
    sort: tuple[str, str] | None = None
    top: int | None = None
    other: bool = False
    kind: str = "auto"
    title: str | None = None
    subtitle: str | None = None

    @property
    def measures(self) -> tuple[str, ...]:
        """Keys of the plotted measures before any series split."""
        return ("count",) if self.aggregate == "count" else self.y

    def fields(self) -> list[str]:
        """Every row field the spec reads."""
        names = [self.x, *self.y, *(f.field for f in self.filters)]
        if self.series:
            names.append(self.series)
        return list(dict.fromkeys(names))


@dataclass
class ChartRows:
    """Chart rows and what was done to get them."""

    x: str
    x_type: str
    columns: list[tuple[str, str]]
    series: list[str]
    rows: list[dict[str, Any]]
    chart_type: str
    reason: str
    total_rows: int
    matched: int
    source_rows: int
    missing_x: int = 0
    sort: str | None = None
    other: bool = False


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def _name(options: Mapping[str, Any], key: str) -> str | None:
    value = options.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SpecError(f"{key} must be a field name")
    return value


def _text(options: Mapping[str, Any], key: str) -> str | None:
    value = options.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise SpecError(f"{key} must be text of 1 to {_MAX_TEXT} characters")
    return value.strip()


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _filter(raw: Any, i: int) -> Filter:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("field"), str):
        raise SpecError(f"filter[{i}] must be {{field, op, value}}")
    op = raw.get("op", "eq")
    if op not in OPS:
        raise SpecError(f"filter[{i}].op {op!r} is not one of {list(OPS)}")
    value = raw.get("value")
    if op == "in":
        if not isinstance(value, list) or not value or not all(_scalar(v) for v in value):
            raise SpecError(f"filter[{i}]: op in takes a non-empty list of values")
        value = tuple(value)
    elif op in ("contains", "prefix"):
        if not isinstance(value, str):
            raise SpecError(f"filter[{i}]: op {op} takes a text value")
    elif op in ("lt", "lte", "gt", "gte"):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise SpecError(f"filter[{i}]: op {op} takes a number or text value")
    elif not _scalar(value):
        raise SpecError(f"filter[{i}]: op {op} takes one value")
    return Filter(raw["field"], op, value)


def parse_spec(options: Mapping[str, Any]) -> ChartSpec:
    """A spec from ``show_view`` options, checked for shape only (not against rows)."""
    x, group_by = _name(options, "x"), _name(options, "group_by")
    if x and group_by and x != group_by:
        raise SpecError("x and group_by name the same axis; pass one of them")
    x = x or group_by
    if x is None:
        raise SpecError("pass x: the field the chart runs along (a category or a date)")

    raw_y = options.get("y")
    if raw_y is None:
        y: tuple[str, ...] = ()
    elif isinstance(raw_y, str):
        y = (raw_y,)
    elif isinstance(raw_y, list) and raw_y and all(isinstance(v, str) and v for v in raw_y):
        y = tuple(dict.fromkeys(raw_y))
    else:
        raise SpecError("y must be a field name or a list of field names")
    if len(y) > VIEW_LIMITS["chart_series"]:
        raise SpecError(f"y takes at most {VIEW_LIMITS['chart_series']} fields")

    aggregate = options.get("aggregate")
    if aggregate is not None and aggregate not in AGGREGATES:
        raise SpecError(f"aggregate {aggregate!r} is not one of {list(AGGREGATES)}")
    if aggregate is None and not y:
        aggregate = "count"
    if aggregate is None and group_by:
        raise SpecError(f"group_by needs aggregate, one of {list(AGGREGATES)}")
    if aggregate == "count" and y:
        raise SpecError("aggregate count counts rows; drop y, or use sum, avg, min or max")
    if aggregate not in (None, "count") and not y:
        raise SpecError(f"aggregate {aggregate} needs y, the numeric field to {aggregate}")

    series = _name(options, "series")
    if series and len(y) > 1:
        raise SpecError("with series, y takes one field, which series splits by value")
    if series and (series == x or series in y):
        raise SpecError("series must be a field other than x and y")
    if x in y:
        raise SpecError("x cannot also be a y field")

    raw_filters = options.get("filter") or []
    if isinstance(raw_filters, Mapping):
        raw_filters = [raw_filters]
    if not isinstance(raw_filters, list) or len(raw_filters) > _MAX_FILTERS:
        raise SpecError(f"filter must be a list of at most {_MAX_FILTERS} {{field, op, value}}")
    filters = tuple(_filter(raw, i) for i, raw in enumerate(raw_filters))

    sort = options.get("sort")
    if sort is not None:
        if isinstance(sort, str):
            sort = {"field": sort}
        if not isinstance(sort, Mapping) or not isinstance(sort.get("field"), str):
            raise SpecError('sort must be {"field": <x or a y field>, "order": "asc" | "desc"}')
        order = sort.get("order", "desc")
        if order not in ("asc", "desc"):
            raise SpecError('sort order must be "asc" or "desc"')
        sort = (sort["field"], order)

    other = options.get("other", False)
    if not isinstance(other, bool):
        raise SpecError("other must be true or false")
    top = options.get("top")
    most = VIEW_LIMITS["chart_points"] - (1 if other else 0)
    whole = isinstance(top, int) and not isinstance(top, bool)
    if top is not None and not (whole and 1 <= top <= most):
        raise SpecError(f"top must be a whole number from 1 to {most}")
    if other and top is None:
        raise SpecError("other needs top: the rows past top go into Other")
    if other and aggregate not in _ADDITIVE:
        raise SpecError("other adds up the rows past top, so it needs aggregate count or sum")

    kind = options.get("kind", "auto")
    if kind not in KINDS:
        raise SpecError(f"kind {kind!r} is not one of {list(KINDS)}")

    return ChartSpec(
        x=x,
        y=y,
        series=series,
        filters=filters,
        aggregate=aggregate,
        sort=sort,
        top=top,
        other=other,
        kind=kind,
        title=_text(options, "title"),
        subtitle=_text(options, "subtitle"),
    )


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "text"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "time" if _TIME.match(value) else "text"
    return "nested"


def field_types(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Chartable fields of *rows* and their type: number, time or text.

    A field mixing numbers and text is text; one holding objects or lists is
    left out, as is a key that cannot be a column key.
    """
    seen: dict[str, str | None] = {}
    for row in rows:
        for key, value in row.items():
            if value is None:
                seen.setdefault(key, None)
                continue
            kind = _value_type(value)
            before = seen.get(key)
            if before is None or before == kind:
                seen[key] = kind
            elif "nested" in (before, kind):
                seen[key] = "nested"
            else:
                seen[key] = "text"
    return {k: t or "text" for k, t in seen.items() if t != "nested" and _KEY.match(k)}


def _shown(types: Mapping[str, str]) -> dict[str, str]:
    return dict(list(types.items())[:_FIELDS_SHOWN])


def _unknown(name: str, role: str, types: Mapping[str, str]) -> SpecError:
    close = difflib.get_close_matches(name, list(types), n=3, cutoff=0.6)
    hint = f" Did you mean {', '.join(repr(c) for c in close)}?" if close else ""
    return SpecError(
        f"{role} {name!r} is not a field of these rows.{hint} Fields are listed in `fields`.",
        fields=_shown(types),
    )


def check_fields(spec: ChartSpec, types: Mapping[str, str]) -> None:
    """Every field the spec reads is in *types*, and measures are numeric."""
    roles = [
        ("x", spec.x),
        *(("y", y) for y in spec.y),
        *(("filter field", f.field) for f in spec.filters),
    ]
    if spec.series:
        roles.append(("series", spec.series))
    for role, name in roles:
        if name not in types:
            raise _unknown(name, role, types)
    numeric = [k for k, t in types.items() if t == "number"]
    for y in spec.y:
        if types[y] != "number":
            raise SpecError(
                f"y {y!r} holds {types[y]}; y fields must be numeric: {numeric[:_FIELDS_SHOWN]}",
                fields=_shown(types),
            )
    if spec.kind == "scatter" and types[spec.x] != "number":
        raise SpecError(
            f"a scatter needs a numeric x and {spec.x!r} holds {types[spec.x]}; "
            f"numeric fields: {numeric[:_FIELDS_SHOWN]}",
            fields=_shown(types),
        )
    if spec.other and types[spec.x] != "text":
        raise SpecError(
            f"other needs a category x and {spec.x!r} holds {types[spec.x]}; "
            "drop other, or use top alone"
        )
    if spec.sort and spec.sort[0] not in (spec.x, *spec.measures):
        choices = [spec.x, *spec.measures]
        raise SpecError(f"sort field {spec.sort[0]!r} must be one of {choices}")


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------


def _passes(f: Filter, value: Any) -> bool:
    op, want = f.op, f.value
    if op == "eq":
        return value == want
    if op == "ne":
        return value != want
    if op == "in":
        return value in want
    if op == "contains":
        return isinstance(value, str) and want in value
    if op == "prefix":
        return isinstance(value, str) and value.startswith(want)
    # Ordering compares like with like; a null or a type mismatch never passes.
    if value is None or isinstance(value, bool) or isinstance(value, str) != isinstance(want, str):
        return False
    if op == "lt":
        return value < want
    if op == "lte":
        return value <= want
    if op == "gt":
        return value > want
    return value >= want


class _Acc:
    """Running count, sum, min and max of one measure in one group."""

    __slots__ = ("hi", "lo", "n", "rows", "total")

    def __init__(self) -> None:
        self.rows = 0
        self.n = 0
        self.total: float = 0
        self.lo: float | None = None
        self.hi: float | None = None

    def add(self, value: Any) -> None:
        self.rows += 1
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self.n += 1
            self.total += value
            self.lo = value if self.lo is None or value < self.lo else self.lo
            self.hi = value if self.hi is None or value > self.hi else self.hi

    def value(self, aggregate: str) -> float | None:
        if aggregate == "count":
            return self.rows
        if not self.n:
            return None
        if aggregate == "sum":
            return _round(self.total)
        if aggregate == "avg":
            return round(self.total / self.n, 4)
        return self.lo if aggregate == "min" else self.hi


def _round(value: float) -> float:
    # Float sums carry noise (0.1 + 0.2); a whole number stays an int.
    return value if isinstance(value, int) else round(value, 6)


def _x_order(value: Any) -> tuple[bool, Any]:
    return (isinstance(value, str), value)


def label(key: str) -> str:
    """A field name as a column label: ``line_count`` is "Line count"."""
    return key.replace("_", " ").capitalize()


@dataclass
class _Pass:
    """What the single pass over the rows found."""

    groups: dict[Any, dict[Any, list[_Acc]]] = field(default_factory=dict)
    points: list[dict[str, Any]] = field(default_factory=list)
    series_values: dict[Any, None] = field(default_factory=dict)
    matched: int = 0
    missing_x: int = 0


def _collect(
    spec: ChartSpec,
    rows: Sequence[Mapping[str, Any]],
    keep_points: bool,
    label_key: str | None,
    scatter: bool,
) -> _Pass:
    """Filter, then group (aggregate), pivot (series) or keep each row as a point."""
    found = _Pass()
    # Outside a scatter, a kept point is a category or a date, which must not repeat.
    seen_x: set[Any] | None = None if scatter else set()
    for row in rows:
        if not all(_passes(f, row.get(f.field)) for f in spec.filters):
            continue
        found.matched += 1
        x_value = row.get(spec.x)
        if x_value is None:
            found.missing_x += 1
            continue
        s_value = row.get(spec.series) if spec.series else None
        if spec.series:
            found.series_values.setdefault(s_value)
        if keep_points:
            if seen_x is not None:
                if x_value in seen_x:
                    raise SpecError(
                        f"x {spec.x!r} repeats ({x_value!r}); add aggregate (count or sum) "
                        "to combine the rows that share it"
                    )
                seen_x.add(x_value)
            point = {spec.x: x_value, **{y: row.get(y) for y in spec.y}, "__series": s_value}
            if label_key:
                point[label_key] = row.get(label_key)
            found.points.append(point)
            continue
        cells = found.groups.setdefault(x_value, {})
        accs = cells.get(s_value)
        if accs is None:
            accs = cells[s_value] = [_Acc() for _ in spec.measures]
        elif spec.aggregate is None:
            raise SpecError(
                f"several rows share {spec.x}={x_value!r} and {spec.series}={s_value!r}; "
                "add aggregate (sum, avg, min or max) to combine them"
            )
        for acc, measure in zip(accs, spec.measures, strict=True):
            acc.add(None if measure == "count" else row.get(measure))

    if found.matched == 0:
        what = "match the filters" if spec.filters else "were selected"
        raise SpecError(f"no rows {what} ({len(rows)} rows before filtering)", code="no_data")
    if found.matched == found.missing_x:
        raise SpecError(f"no matching row has a value for x {spec.x!r}", code="no_data")
    if len(found.series_values) > VIEW_LIMITS["chart_series"]:
        raise SpecError(
            f"series {spec.series!r} has {len(found.series_values)} values and a chart plots "
            f"at most {VIEW_LIMITS['chart_series']}; filter it with op in, or drop series"
        )
    return found


def _value_label(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _measure_columns(spec: ChartSpec, found: _Pass) -> tuple[list[str], list[str]]:
    """Keys and labels of the plotted columns: one per measure, or one per series value."""
    if spec.series:
        keys = [f"s{i + 1}" for i in range(len(found.series_values))]
        return keys, [_value_label(v) for v in found.series_values]
    keys = ["row_count" if m == "count" and spec.x == "count" else m for m in spec.measures]
    labels = [_AGGREGATE_LABEL["count"] if m == "count" else label(m) for m in spec.measures]
    return keys, labels


def _chart_rows(
    spec: ChartSpec, found: _Pass, keys: list[str], label_key: str | None
) -> list[dict[str, Any]]:
    by_value = dict(zip(found.series_values, keys, strict=True)) if spec.series else {}
    out: list[dict[str, Any]] = []
    for p in found.points:
        if spec.series:
            # One series per point: the other series columns stay null.
            row_out = {spec.x: p[spec.x], **dict.fromkeys(keys)}
            row_out[by_value[p["__series"]]] = p.get(spec.y[0])
        else:
            values = zip(keys, spec.measures, strict=True)
            row_out = {spec.x: p[spec.x], **{k: p.get(m) for k, m in values}}
        if label_key:
            row_out[label_key] = p[label_key]
        out.append(row_out)
    # Without aggregate each cell holds one row's value, read through "sum".
    aggregate = spec.aggregate or "sum"
    empty = 0 if spec.aggregate in _ADDITIVE else None
    for x_value, cells in found.groups.items():
        row_out = {spec.x: x_value}
        if spec.series:
            row_out.update(dict.fromkeys(keys, empty))
            for s_value, accs in cells.items():
                row_out[by_value[s_value]] = accs[0].value(aggregate)
        else:
            row_out.update(
                (k, acc.value(aggregate)) for k, acc in zip(keys, cells[None], strict=True)
            )
        out.append(row_out)
    return out


def _total(row: Mapping[str, Any], keys: list[str]) -> float:
    return sum(v for k in keys if isinstance(v := row.get(k), (int, float)))


def _sort(
    spec: ChartSpec, out: list[dict[str, Any]], keys: list[str], x_type: str, scatter: bool
) -> tuple[tuple[str, str] | None, str | None]:
    """Sort *out* in place; returns the sort used and the order a chart may claim.

    As asked; else grouped categories largest first and dates oldest first.
    """
    sort = spec.sort
    if sort is None and x_type == "time":
        sort = (spec.x, "asc")
    elif sort is None and spec.aggregate and not scatter:
        sort = ("__total", "desc")
    if sort is None:
        return None, None
    name, order = sort
    reverse = order == "desc"
    if name == spec.x:
        out.sort(key=lambda r: _x_order(r[spec.x]), reverse=reverse)
        return sort, None
    # Series columns sort by their row total; a measure by its own value.
    key = None if name == "__total" or spec.series else keys[spec.measures.index(name)]

    def by_value(r: Mapping[str, Any]) -> tuple[bool, float]:
        value = _total(r, keys) if key is None else r.get(key)
        if value is None:
            return (True, 0)  # nulls last either way
        return (False, -value if reverse else value)

    out.sort(key=by_value)
    # A chart claims an order only by its series total, which a null has no place in.
    sums = key is None or len(keys) == 1
    claim = order if sums and all(r.get(k) is not None for r in out for k in keys) else None
    return sort, claim


def transform(
    spec: ChartSpec, rows: Sequence[Mapping[str, Any]], types: Mapping[str, str]
) -> ChartRows:
    """Filter, group, sort and cut *rows* by *spec*, and pick the chart type.

    *types* comes from ``field_types`` and must already pass ``check_fields``.
    """
    x_type = types[spec.x]
    scatter = spec.kind == "scatter" or (
        spec.kind == "auto" and spec.aggregate is None and x_type == "number"
    )
    label_key = None
    if spec.aggregate is None and not spec.series:
        label_key = next(
            (k for k in _LABEL_FIELDS if k in types and k != spec.x and k not in spec.y), None
        )
    keep_points = spec.aggregate is None and (scatter or not spec.series)
    found = _collect(spec, rows, keep_points, label_key, scatter)
    keys, labels = _measure_columns(spec, found)
    columns = [(spec.x, label(spec.x)), *zip(keys, labels, strict=True)]
    if label_key:
        columns.insert(0, (label_key, label(label_key)))
    out = _chart_rows(spec, found, keys, label_key)
    sort, claim = _sort(spec, out, keys, x_type, scatter)

    # Top N, with the rest added up as Other; else the chart-point cap.
    total_rows = len(out)
    other = False
    if spec.top is not None and len(out) > spec.top:
        rest, out = out[spec.top :], out[: spec.top]
        if spec.other:
            bucket: dict[str, Any] = {spec.x: f"{OTHER_LABEL} ({len(rest)} more)"}
            for k in keys:
                values = [r[k] for r in rest if isinstance(r.get(k), (int, float))]
                bucket[k] = _round(sum(values)) if values else None
            out.append(bucket)
            # The bucket sits last whatever its size, so no order is claimed.
            other, claim, total_rows = True, None, len(out)
    cap = VIEW_LIMITS["chart_points"]
    if len(out) > cap:
        # A time axis keeps its newest points; anything else keeps its first.
        newest = x_type == "time" and (sort is None or sort[0] == spec.x)
        out = out[-cap:] if newest else out[:cap]

    chart_type, reason = _choose_kind(spec, x_type, keys, out, total_rows > len(out), scatter)
    return ChartRows(
        x=spec.x,
        x_type=x_type,
        columns=columns,
        series=keys,
        rows=out,
        chart_type=chart_type,
        reason=reason,
        total_rows=total_rows,
        matched=found.matched,
        source_rows=len(rows),
        missing_x=found.missing_x,
        sort=claim if chart_type != "scatter" else None,
        other=other,
    )


# ---------------------------------------------------------------------------
# Chart type
# ---------------------------------------------------------------------------


def _whole(keys: list[str], rows: list[dict[str, Any]]) -> bool:
    values = [r.get(k) for r in rows for k in keys]
    return all(isinstance(v, (int, float)) and v >= 0 for v in values) and sum(values) > 0


def _choose_kind(
    spec: ChartSpec,
    x_type: str,
    keys: list[str],
    rows: list[dict[str, Any]],
    truncated: bool,
    scatter: bool,
) -> tuple[str, str]:
    """The chart type and why: the spec's ``kind``, checked, or one picked from the data's shape."""
    additive = spec.aggregate in _ADDITIVE or spec.aggregate is None
    low, high = VIEW_LIMITS["donut_min_parts"], VIEW_LIMITS["donut_max_parts"]
    donut_ok = len(keys) == 1 and low <= len(rows) <= high and not truncated and _whole(keys, rows)
    kind = spec.kind
    if kind == "donut" and not donut_ok:
        raise SpecError(
            f"a donut shows one measure as {low} to {high} non-negative parts of a whole, and this "
            f"chart has {len(rows)} rows and {len(keys)} series"
            f"{' with some cut' if truncated else ''}; "
            f"use top: {high - 1} with other: true, or kind bar"
        )
    if kind == "stacked_bar" and (len(keys) < 2 or not additive or not _whole(keys, rows)):
        raise SpecError(
            "a stacked_bar needs two or more series of non-negative counts or sums; "
            "pass series (a field to split by) or several y fields, or use kind bar"
        )
    if kind != "auto":
        return kind, f"kind {kind} was asked for"
    if scatter:
        return "scatter", f"x {spec.x!r} and the plotted values are both numeric"
    if x_type == "time":
        return "line", f"x {spec.x!r} holds dates"
    if spec.series and len(keys) > 1:
        if additive and _whole(keys, rows):
            return "stacked_bar", f"series {spec.series!r} splits each bar into parts"
        why = f"{spec.aggregate} does not add up"
        return "bar", f"series {spec.series!r} values sit side by side ({why})"
    if len(keys) > 1:
        return "bar", "several measures sit side by side"
    if spec.aggregate in _ADDITIVE and donut_ok:
        return "donut", f"{len(rows)} parts that add up to one whole"
    return "bar", "one measure across categories"


def default_title(spec: ChartSpec) -> str:
    if spec.aggregate == "count":
        measure = "Count"
    else:
        names = ", ".join(label(y).lower() for y in spec.y)
        measure = (
            f"{_AGGREGATE_LABEL[spec.aggregate]} {names}" if spec.aggregate else names.capitalize()
        )
    by = label(spec.x).lower() + (f" and {label(spec.series).lower()}" if spec.series else "")
    return f"{measure} by {by}"
