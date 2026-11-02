"""Rows to chart rows: one pass filters and groups, then sort, top and the
Other bucket work on the groups, and the figures a reply quotes are added up.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from repowise.server.chat_spec.kinds import _choose_kind
from repowise.server.chat_spec.parse import (
    _ADDITIVE,
    _AGGREGATE_LABEL,
    _DONUT_KEEP,
    ARROW,
    OTHER_LABEL,
    ChartSpec,
    SpecError,
    _number,
    _passes,
    _value_label,
    _x_value,
    label,
)
from repowise.server.schemas.views import VIEW_LIMITS

# Fields that name a point in a scatter or an unaggregated chart, in preference order.
_LABEL_FIELDS = ("file", "file_path", "path", "name", "title", "id")


#: Shares a receipt lists, so the model quotes them instead of dividing.
_SHARES_LISTED = 12


_SORT_KEY = "__sort"


#: Rows a receipt previews when it has no shares to list.
_ROWS_PREVIEWED = 15


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
    #: Why the asked kind was not drawn as asked, or a caution about it.
    kind_note: str | None = None
    #: Totals, shares and the overall average, computed here so nobody divides.
    figures: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    color: str | None = None


class _Acc:
    """Running count, sum, min, max and weighted sum of one measure in one group."""

    __slots__ = ("hi", "lo", "n", "rows", "total", "weighted", "weights")

    def __init__(self) -> None:
        self.rows = 0
        self.n = 0
        self.total: float = 0
        self.lo: float | None = None
        self.hi: float | None = None
        self.weighted: float = 0
        self.weights: float = 0

    def add(self, value: Any, weight: Any = None) -> None:
        self.rows += 1
        if _number(value):
            self.n += 1
            self.total += value
            self.lo = value if self.lo is None or value < self.lo else self.lo
            self.hi = value if self.hi is None or value > self.hi else self.hi
            if _number(weight) and weight > 0:
                self.weighted += value * weight
                self.weights += weight

    def merge(self, other: _Acc) -> None:
        self.rows += other.rows
        self.n += other.n
        self.total += other.total
        self.weighted += other.weighted
        self.weights += other.weights
        for value in (other.lo, other.hi):
            if value is not None:
                self.lo = value if self.lo is None or value < self.lo else self.lo
                self.hi = value if self.hi is None or value > self.hi else self.hi

    def value(self, aggregate: str, weighted: bool = False) -> float | None:
        if aggregate == "count":
            return self.rows
        if not self.n:
            return None
        if aggregate == "sum":
            return _round(self.total)
        if aggregate == "avg":
            if weighted:
                return round(self.weighted / self.weights, 2) if self.weights else None
            return round(self.total / self.n, 2)
        return self.lo if aggregate == "min" else self.hi


def _round(value: float) -> float:
    # Float sums carry noise (0.1 + 0.2); a whole number stays an int.
    return value if isinstance(value, int) else round(value, 6)


def _x_order(value: Any) -> tuple[bool, Any]:
    return (isinstance(value, str), value)


@dataclass
class _Pass:
    """What the single pass over the rows found."""

    groups: dict[Any, dict[Any, list[_Acc]]] = field(default_factory=dict)
    points: list[dict[str, Any]] = field(default_factory=list)
    series_values: dict[Any, None] = field(default_factory=dict)
    #: Per x group: the first value of each treemap group field, and the colour accumulator.
    places: dict[Any, dict[str, Any]] = field(default_factory=dict)
    colors: dict[Any, Any] = field(default_factory=dict)
    overall: list[_Acc] = field(default_factory=list)
    matched: int = 0
    missing_x: int = 0


def _collect(
    spec: ChartSpec,
    rows: Sequence[Mapping[str, Any]],
    keep_points: bool,
    label_key: str | None,
    unique_x: bool,
) -> _Pass:
    """Filter, then group (aggregate), pivot (series) or keep each row as a point."""
    found = _Pass(overall=[_Acc() for _ in spec.measures])
    seen_x: set[Any] | None = set() if unique_x else None
    numeric_color = spec.color is not None
    for row in rows:
        if not all(_passes(f, row.get(f.field)) for f in spec.filters):
            continue
        found.matched += 1
        weight = row.get(spec.weight) if spec.weight else None
        if spec.weight_floor is not None and spec.weight:
            weight = max(weight if _number(weight) else 0, spec.weight_floor)
        for acc, measure in zip(found.overall, spec.measures, strict=True):
            acc.add(None if measure == "count" else row.get(measure), weight)
        x_value = _x_value(spec, row)
        if x_value is None:
            found.missing_x += 1
            continue
        s_value = row.get(spec.series) if spec.series else None
        if spec.series:
            found.series_values.setdefault(s_value)
        if spec.groups and x_value not in found.places:
            found.places[x_value] = {g: row.get(g) for g in spec.groups}
        if spec.color:
            value = row.get(spec.color)
            numeric_color = numeric_color and (value is None or _number(value))
            found.colors.setdefault(x_value, []).append((value, weight))
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
            if spec.sort:
                point[_SORT_KEY] = row.get(spec.sort[0])
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
            acc.add(None if measure == "count" else row.get(measure), weight)

    if found.matched == 0:
        what = "match the filters" if spec.filters else "were selected"
        raise SpecError(f"no rows {what} ({len(rows)} rows before filtering)", code="no_data")
    if found.matched == found.missing_x:
        raise SpecError(f"no matching row has a value for x {spec.x!r}", code="no_data")
    found.colors = {
        x: _color_value(values, numeric_color, bool(spec.weight))
        for x, values in found.colors.items()
    }
    return found


def _color_value(values: list[tuple[Any, Any]], numeric: bool, weighted: bool) -> Any:
    """A group's colour: the average of a number (weighted when asked), else the commonest text."""
    present = [(v, w) for v, w in values if v is not None]
    if not present:
        return None
    if numeric:
        acc = _Acc()
        for v, w in present:
            acc.add(v, w)
        return acc.value("avg", weighted)
    return Counter(_value_label(v) for v, _ in present).most_common(1)[0][0]


def _fold_series(spec: ChartSpec, found: _Pass, notes: list[str]) -> None:
    """Keep the chart's series cap: the largest values stay, the rest fold into Other."""
    cap = VIEW_LIMITS["chart_series"]
    if len(found.series_values) <= cap:
        return
    if spec.aggregate not in _ADDITIVE:
        raise SpecError(
            f"series {spec.series!r} has {len(found.series_values)} values and a chart plots "
            f"at most {cap}; filter it with op in, or drop series"
        )
    totals: Counter[Any] = Counter()
    for cells in found.groups.values():
        for s_value, accs in cells.items():
            totals[s_value] += accs[0].value(spec.aggregate) or 0
    keep = {s for s, _ in totals.most_common(cap - 1)}
    folded = len(found.series_values) - len(keep)
    for cells in found.groups.values():
        rest = _Acc()
        for s_value in [s for s in cells if s not in keep]:
            rest.merge(cells.pop(s_value)[0])
        if rest.rows:
            cells[OTHER_LABEL] = [rest]
    found.series_values = {
        **{s: None for s in found.series_values if s in keep},
        OTHER_LABEL: None,
    }
    notes.append(
        f"series {spec.series} has {len(keep) + folded} values; the {len(keep)} largest are "
        f"drawn and {folded} more are added up as {OTHER_LABEL}"
    )


def _measure_columns(spec: ChartSpec, found: _Pass) -> tuple[list[str], list[str]]:
    """Keys and labels of the plotted columns: one per measure, or one per series value."""
    if spec.series:
        keys = [f"s{i + 1}" for i in range(len(found.series_values))]
        return keys, [_value_label(v) for v in found.series_values]
    keys = ["row_count" if m == "count" and spec.x == "count" else m for m in spec.measures]
    labels = [_measure_label(spec, m) for m in spec.measures]
    return keys, labels


def _measure_label(spec: ChartSpec, measure: str) -> str:
    if measure == "count":
        return _AGGREGATE_LABEL["count"]
    if spec.aggregate in ("avg", "min", "max"):
        return f"{_AGGREGATE_LABEL[spec.aggregate]} {label(measure).lower()}"
    return label(measure)


def _chart_rows(
    spec: ChartSpec, found: _Pass, keys: list[str], label_key: str | None
) -> list[dict[str, Any]]:
    by_value = dict(zip(found.series_values, keys, strict=True)) if spec.series else {}
    weighted = bool(spec.weight)
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
        if _SORT_KEY in p:
            row_out[_SORT_KEY] = p[_SORT_KEY]
        out.append(row_out)
    # Without aggregate each cell holds one row's value, read through "sum".
    aggregate = spec.aggregate or "sum"
    empty = 0 if spec.aggregate in _ADDITIVE else None
    for x_value, cells in found.groups.items():
        row_out = {spec.x: x_value}
        if spec.series:
            row_out.update(dict.fromkeys(keys, empty))
            for s_value, accs in cells.items():
                row_out[by_value[s_value]] = accs[0].value(aggregate, weighted)
        else:
            row_out.update(
                (k, acc.value(aggregate, weighted))
                for k, acc in zip(keys, cells[None], strict=True)
            )
        out.append(row_out)
    for row_out in out:
        place = found.places.get(row_out[spec.x], {})
        for g in spec.groups:
            row_out[g] = place.get(g)
        if _own_color_column(spec):
            row_out[_color_key(spec, keys)] = found.colors.get(row_out[spec.x])
    return out


def _own_color_column(spec: ChartSpec) -> bool:
    """A colour by a group level reads that level's column, so it adds none."""
    return spec.color is not None and spec.color not in spec.groups


def _color_key(spec: ChartSpec, keys: list[str]) -> str:
    key = spec.color or "color"
    return f"{key}_color" if key in keys or key == spec.x else key


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
    # Series columns sort by their row total; a measure by its own value; any
    # other field by the value each point carried from its row.
    if name in spec.measures:
        key = None if spec.series else keys[spec.measures.index(name)]
    else:
        key = None if name == "__total" else _SORT_KEY

    def value_of(r: Mapping[str, Any]) -> Any:
        return _total(r, keys) if key is None else r.get(key)

    # Nulls last either way; a stable sort keeps ties in source order.
    present = [r for r in out if value_of(r) is not None]
    present.sort(key=value_of, reverse=reverse)
    out[:] = present + [r for r in out if value_of(r) is None]
    # A chart claims an order only by its series total, which a null has no place in.
    sums = key != _SORT_KEY and (key is None or len(keys) == 1)
    claim = order if sums and all(r.get(k) is not None for r in out for k in keys) else None
    return sort, claim


def _is_scatter(spec: ChartSpec, x_type: str) -> bool:
    return spec.kind == "scatter" or (
        spec.kind == "auto" and spec.aggregate is None and x_type == "number"
    )


def transform(
    spec: ChartSpec, rows: Sequence[Mapping[str, Any]], types: Mapping[str, str]
) -> ChartRows:
    """Filter, group, sort and cut *rows* by *spec*, and pick the chart type.

    *spec* must already pass ``fit`` (or ``check_fields``) for these *types*.
    """
    x_type = "text" if spec.x_parts else types[spec.x]
    scatter = _is_scatter(spec, x_type)
    table = spec.kind == "table"
    label_key = None
    if spec.aggregate is None and not spec.series:
        label_key = next(
            (
                k
                for k in _LABEL_FIELDS
                if k in types
                and k not in (spec.x, spec.color)
                and k not in (*spec.y, *spec.x_parts, *spec.groups)
            ),
            None,
        )
    keep_points = spec.aggregate is None and (scatter or table or not spec.series)
    notes: list[str] = list(spec.notes)
    found = _collect(spec, rows, keep_points, label_key, unique_x=not (scatter or table))
    if spec.series:
        _fold_series(spec, found, notes)
    keys, labels = _measure_columns(spec, found)
    columns = [(spec.x, _x_label(spec)), *zip(keys, labels, strict=True)]
    if label_key:
        columns.insert(0, (label_key, label(label_key)))
    if spec.groups:
        columns[:0] = [(g, label(g)) for g in spec.groups]
    if spec.color and _own_color_column(spec):
        color_label = label(spec.color)
        if spec.aggregate and types.get(spec.color) == "number":
            color_label = f"Average {color_label.lower()}"
        columns.append((_color_key(spec, keys), color_label))
    out = _chart_rows(spec, found, keys, label_key)
    if spec.x_step == "week" and spec.aggregate:
        out = _fill_weeks(spec, out, keys)
    sort, claim = _sort(spec, out, keys, x_type, scatter)

    top, other = spec.top, spec.other
    additive_one = len(keys) == 1 and spec.aggregate in _ADDITIVE
    if (
        spec.kind == "donut"
        and additive_one
        and len(out) > VIEW_LIMITS["donut_max_parts"]
        and x_type == "text"
        and (top is None or top > _DONUT_KEEP)
    ):
        top, other = _DONUT_KEEP, True
        notes.append(
            f"a donut holds {VIEW_LIMITS['donut_max_parts']} parts, so the largest {_DONUT_KEEP} "
            f"are drawn and the other {len(out) - _DONUT_KEEP} are added up as {OTHER_LABEL}"
        )
        if sort is None or sort[0] == spec.x:
            out.sort(key=lambda r: -(r.get(keys[0]) or 0))

    # Top N, with the rest added up as Other; else the chart-point cap.
    total_rows = len(out)
    all_rows = out
    folded = False
    if top is not None and len(out) > top:
        rest, out = out[top:], out[:top]
        if other:
            bucket: dict[str, Any] = {spec.x: f"{OTHER_LABEL} ({len(rest)} more)"}
            for k in keys:
                values = [r[k] for r in rest if isinstance(r.get(k), (int, float))]
                bucket[k] = _round(sum(values)) if values else None
            for g in spec.groups:
                bucket[g] = OTHER_LABEL
            if _own_color_column(spec):
                bucket[_color_key(spec, keys)] = None
            if label_key:
                bucket[label_key] = None
            out.append(bucket)
            # The bucket sits last whatever its size, so no order is claimed.
            folded, claim, total_rows = True, None, len(out)
    cap = VIEW_LIMITS["rows"] if table else VIEW_LIMITS["chart_points"]
    if len(out) > cap:
        # A time axis keeps its newest points; anything else keeps its first.
        newest = x_type == "time" and (sort is None or sort[0] == spec.x)
        out = out[-cap:] if newest else out[:cap]

    for row in out:
        row.pop(_SORT_KEY, None)
    chart_type, reason, kind_note = _choose_kind(
        spec, x_type, keys, out, total_rows > len(out), scatter
    )
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
        sort=claim if chart_type not in ("scatter", "treemap", "table") else None,
        other=folded,
        kind_note=kind_note or spec.kind_note,
        figures=_figures(spec, found, keys, labels, all_rows, out, folded, x_type == "time"),
        notes=notes,
        groups=list(spec.groups),
        color=_color_key(spec, keys) if spec.color else None,
    )


def _fill_weeks(spec: ChartSpec, out: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    """Weeks between the first and last with no row, as rows of zero (or null for an average)."""
    from datetime import date, timedelta

    try:
        present = {date.fromisoformat(str(r[spec.x])[:10]): r for r in out}
    except ValueError:
        return out
    if len(present) < 2:
        return out
    empty = 0 if spec.aggregate in _ADDITIVE else None
    day, last = min(present), max(present)
    filled = []
    while day <= last:
        row = present.get(day)
        filled.append(row if row is not None else {spec.x: day.isoformat(), **dict.fromkeys(keys, empty)})
        day += timedelta(days=7)
    # Rows off the Monday grid (none from the changes dataset) stay as they were.
    on_grid = {id(r) for r in filled}
    return filled + [r for r in out if id(r) not in on_grid]


def _x_label(spec: ChartSpec) -> str:
    if spec.x_parts:
        return f" {ARROW} ".join(label(p) for p in spec.x_parts)
    return label(spec.x)


def _pct(part: float, whole: float) -> float:
    return round(100 * part / whole, 1)


def _figures(
    spec: ChartSpec,
    found: _Pass,
    keys: list[str],
    labels: list[str],
    all_rows: list[dict[str, Any]],
    shown: list[dict[str, Any]],
    folded: bool,
    time_axis: bool = False,
) -> dict[str, Any]:
    """The numbers a reply quotes: totals and shares of a whole, or the overall average."""
    figures: dict[str, Any] = {"rows_matched": found.matched, "groups": len(all_rows)}
    if spec.aggregate in _ADDITIVE:
        totals = {
            label_: _round(sum(r.get(k) or 0 for r in all_rows))
            for k, label_ in zip(keys, labels, strict=True)
        }
        whole = sum(totals.values())
        figures["total"] = _round(whole)
        if len(keys) > 1:
            figures["total_by_series"] = totals
        if whole > 0:
            figures["shares"] = [
                {
                    "x": _value_label(r[spec.x]),
                    "value": _round(_total(r, keys)),
                    "pct": _pct(_total(r, keys), whole),
                }
                for r in shown[:_SHARES_LISTED]
            ]
            if folded:
                figures["shares_note"] = f"pct is of the total {figures['total']}, Other included"
    elif spec.aggregate in ("avg", "min", "max") and not spec.series:
        weighted = bool(spec.weight)
        figures["overall"] = {
            label_: acc.value(spec.aggregate, weighted)
            for acc, label_ in zip(found.overall, labels, strict=True)
        }
        if weighted:
            figures["weighted_by"] = spec.weight
    if "shares" not in figures:
        # Over time the newest rows matter most, and the first one anchors a change.
        newest = time_axis and len(shown) > _ROWS_PREVIEWED
        preview = shown[-_ROWS_PREVIEWED:] if newest else shown[:_ROWS_PREVIEWED]
        figures["rows"] = [{k: _preview(v) for k, v in r.items() if v is not None} for r in preview]
        if newest:
            figures["first_row"] = {k: _preview(v) for k, v in shown[0].items() if v is not None}
        if len(shown) > _ROWS_PREVIEWED:
            which = "newest" if newest else "first"
            figures["rows_note"] = f"the {which} {_ROWS_PREVIEWED} of {len(shown)} drawn rows"
    return figures


def _preview(value: Any) -> Any:
    if isinstance(value, bool):
        return _value_label(value)
    return round(value, 2) if isinstance(value, float) else value
