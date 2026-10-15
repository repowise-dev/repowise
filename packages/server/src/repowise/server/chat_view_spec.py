"""The chart spec: parse and repair it, then turn rows into chart rows.

Pure functions over a list of row dicts. ``parse_spec`` checks a spec's shape
and repairs the obvious slips (``other: "Other"``, ``kind: "pie"``, a count
with a y); ``fit`` checks it against the rows and repairs what the rows decide
(a repeated x without an aggregate, a near-miss field name, a weight a
dataset declares). Every repair is a note the receipt reports, so a change to
what is drawn is never silent. One pass over the rows filters and groups;
sort, top and the Other bucket then work on the groups. Every refusal is a
``SpecError`` whose message says what to change and names the fields.

Callers: the ``chart`` view of ``show_view`` and the ``chart`` tool, both in
:mod:`repowise.server.chat_views`.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from repowise.server.schemas.views import VIEW_LIMITS

AGGREGATES = ("count", "sum", "avg", "min", "max")
OPS = ("eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "prefix")
KINDS = (
    "auto",
    "bar",
    "horizontal_bar",
    "stacked_bar",
    "line",
    "area",
    "donut",
    "scatter",
    "treemap",
    "heatmap",
    "table",
)
OPTIONS = frozenset(
    {"rows", "x", "y", "series", "filter", "group_by", "aggregate", "sort", "top", "other"}
    | {"kind", "title", "subtitle", "weight", "groups", "color"}
)
OTHER_LABEL = "Other"
#: Joins the fields of a composite x, as in "packages/server → packages/core".
ARROW = "→"

# Names a model reaches for, read as the kind or aggregate they mean.
_KIND_ALIASES = {
    "pie": "donut",
    "doughnut": "donut",
    "ring": "donut",
    "column": "bar",
    "columns": "bar",
    "vertical_bar": "bar",
    "histogram": "bar",
    "hbar": "horizontal_bar",
    "barh": "horizontal_bar",
    "horizontal": "horizontal_bar",
    "stacked": "stacked_bar",
    "stacked_column": "stacked_bar",
    "timeseries": "line",
    "time_series": "line",
    "points": "scatter",
    "heat_map": "heatmap",
    "grid": "heatmap",
    "tree_map": "treemap",
    "list": "table",
}
_AGGREGATE_ALIASES = {"mean": "avg", "average": "avg", "total": "sum", "n": "count"}
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
_LABEL_FIELDS = ("file", "file_path", "path", "name", "title", "id")
_MAX_FILTERS = 10
_MAX_TEXT = 120
_FIELDS_SHOWN = 30
_MAX_X_PARTS = 3
#: Parts a donut keeps when it folds the rest into Other.
_DONUT_KEEP = VIEW_LIMITS["donut_max_parts"] - 1
_DEFAULT_TOP = 10
#: Shares a receipt lists, so the model quotes them instead of dividing.
_SHARES_LISTED = 12
_LINE_KINDS = frozenset({"line", "area"})
_SORT_KEY = "__sort"
#: Rows a receipt previews when it has no shares to list.
_ROWS_PREVIEWED = 15


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
    #: The fields a composite x joins, in order; ``x`` is then their joined key.
    x_parts: tuple[str, ...] = ()
    #: A numeric field an ``avg`` is weighted by; ``unweighted`` turns off a declared one.
    weight: str | None = None
    unweighted: bool = False
    #: A treemap's nesting fields (outermost first) and the field colouring its cells.
    groups: tuple[str, ...] = ()
    color: str | None = None
    #: Repairs made to the spec as sent, for the receipt.
    notes: tuple[str, ...] = ()
    #: Set when ``fit`` had to draw another kind than the one asked for.
    kind_note: str | None = None

    @property
    def measures(self) -> tuple[str, ...]:
        """Keys of the plotted measures before any series split."""
        return ("count",) if self.aggregate == "count" else self.y

    def fields(self) -> list[str]:
        """Every row field the spec reads."""
        names = [
            *(self.x_parts or (self.x,)),
            *self.y,
            *(f.field for f in self.filters),
            *self.groups,
        ]
        for extra in (self.series, self.weight, self.color):
            if extra:
                names.append(extra)
        return list(dict.fromkeys(names))

    def noted(self, *notes: str, **changes: Any) -> ChartSpec:
        return replace(self, notes=(*self.notes, *notes), **changes)


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


def _names(options: Mapping[str, Any], key: str, most: int) -> tuple[str, ...]:
    value = options.get(key)
    if value is None:
        return ()
    if isinstance(value, str) and value:
        return (value,)
    if isinstance(value, list) and value and all(isinstance(v, str) and v for v in value):
        if len(value) > most:
            raise SpecError(f"{key} takes at most {most} fields")
        return tuple(dict.fromkeys(value))
    raise SpecError(f"{key} must be a field name or a list of field names")


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


def _flag(value: Any) -> bool | None:
    """``True``/``False`` from a bool or an obvious spelling of one; None otherwise."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in ("true", "yes", "on", "other", "1"):
            return True
        if word in ("false", "no", "off", "none", "0", ""):
            return False
    return None


def _whole_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _composite_key(parts: tuple[str, ...]) -> str:
    key = "_".join(parts)[:64]
    return key if _KEY.match(key) else "x"


def _parse_kind(options: Mapping[str, Any], notes: list[str]) -> str:
    kind = options.get("kind", "auto")
    if kind is None:
        return "auto"
    if isinstance(kind, str):
        word = kind.strip().lower().replace("-", "_").replace(" ", "_")
        alias = _KIND_ALIASES.get(word, word)
        if alias in KINDS:
            if alias != kind:
                notes.append(f"kind {kind!r} read as {alias!r}")
            return alias
    raise SpecError(f"kind {kind!r} is not one of {list(KINDS)}")


def read_kind(value: Any) -> tuple[str, str | None]:
    """A kind as asked (``"pie"``, ``"Donut"``) and the note when it was read as another name."""
    notes: list[str] = []
    return _parse_kind({"kind": value}, notes), (notes[0] if notes else None)


def _parse_aggregate(options: Mapping[str, Any], notes: list[str]) -> str | None:
    aggregate = options.get("aggregate")
    if aggregate is None:
        return None
    word = aggregate.strip().lower() if isinstance(aggregate, str) else aggregate
    word = _AGGREGATE_ALIASES.get(word, word) if isinstance(word, str) else word
    if word not in AGGREGATES:
        raise SpecError(f"aggregate {aggregate!r} is not one of {list(AGGREGATES)}")
    if word != aggregate:
        notes.append(f"aggregate {aggregate!r} read as {word!r}")
    return word


def _parse_sort(options: Mapping[str, Any]) -> tuple[str, str] | None:
    sort = options.get("sort")
    if sort is None:
        return None
    if isinstance(sort, str):
        # A bare order sorts by the plotted value; a bare name sorts by that field.
        sort = {"order": sort} if sort.strip().lower() in ("asc", "desc") else {"field": sort}
    if not isinstance(sort, Mapping):
        raise SpecError('sort must be {"field": <x or a y field>, "order": "asc" | "desc"}')
    name = sort.get("field", "__value")
    if not isinstance(name, str) or not name:
        raise SpecError('sort must be {"field": <x or a y field>, "order": "asc" | "desc"}')
    order = sort.get("order", "desc")
    order = order.strip().lower() if isinstance(order, str) else order
    if order not in ("asc", "desc"):
        raise SpecError('sort order must be "asc" or "desc"')
    return name, order


def parse_spec(options: Mapping[str, Any]) -> ChartSpec:
    """A spec from chart options, checked for shape and repaired where the intent is plain."""
    notes: list[str] = []
    raw_x = options.get("x")
    group_by = _name(options, "group_by")
    x_parts: tuple[str, ...] = ()
    if isinstance(raw_x, list):
        x_parts = _names(options, "x", _MAX_X_PARTS)
        x = _composite_key(x_parts) if len(x_parts) > 1 else x_parts[0]
        if len(x_parts) == 1:
            x_parts = ()
    else:
        x = _name(options, "x")
    if x and group_by and x != group_by:
        raise SpecError("x and group_by name the same axis; pass one of them")
    x = x or group_by
    if x is None:
        raise SpecError("pass x: the field the chart runs along (a category or a date)")

    y = _names(options, "y", VIEW_LIMITS["chart_series"])
    aggregate = _parse_aggregate(options, notes)
    if aggregate == "count" and y:
        notes.append(f"aggregate count counts rows, so y {list(y)} was dropped")
        y = ()
    if aggregate is None and not y:
        aggregate = "count"
    if aggregate is None and group_by:
        aggregate = "sum"
        notes.append("group_by combines rows, so y is summed")
    if aggregate not in (None, "count") and not y:
        raise SpecError(f"aggregate {aggregate} needs y, the numeric field to {aggregate}")

    series = _name(options, "series")
    if series and len(y) > 1:
        raise SpecError("with series, y takes one field, which series splits by value")
    if series and (series == x or series in y):
        notes.append(f"series {series!r} repeats x or y, so it was dropped")
        series = None
    if x in y:
        raise SpecError("x cannot also be a y field")

    raw_filters = options.get("filter") or []
    if isinstance(raw_filters, Mapping):
        raw_filters = [raw_filters]
    if not isinstance(raw_filters, list) or len(raw_filters) > _MAX_FILTERS:
        raise SpecError(f"filter must be a list of at most {_MAX_FILTERS} {{field, op, value}}")
    filters = tuple(_filter(raw, i) for i, raw in enumerate(raw_filters))

    kind = _parse_kind(options, notes)
    other = options.get("other", False)
    flag = _flag(other)
    if flag is None:
        raise SpecError("other must be true or false")
    if flag is not other:
        notes.append(f"other {other!r} read as {str(flag).lower()}")
    other = flag

    top = options.get("top")
    if top is not None:
        whole = _whole_number(top)
        if whole is None or whole < 1:
            raise SpecError(f"top must be a whole number from 1 to {VIEW_LIMITS['chart_points']}")
        if whole != top:
            notes.append(f"top {top!r} read as {whole}")
        top = whole
    most = VIEW_LIMITS["chart_points"] - (1 if other else 0)
    if top is not None and top > most and kind != "table":
        notes.append(f"top {top} is past the {most} points a chart holds, so it shows {most}")
        top = most
    if other and top is None:
        top = _DONUT_KEEP if kind == "donut" else _DEFAULT_TOP
        notes.append(f"other needs top, so the largest {top} are kept and the rest go to Other")
    if other and aggregate not in _ADDITIVE:
        if aggregate is None:
            aggregate = "sum"
            notes.append("other adds up the rows past top, so y is summed")
        else:
            other = False
            notes.append(f"other adds up the rest, and an {aggregate} does not add up, so no Other")

    unweighted = isinstance(options.get("weight"), str) and options["weight"].lower() == "none"
    unweighted = unweighted or options.get("weight") is False
    return ChartSpec(
        x=x,
        y=y,
        series=series,
        filters=filters,
        aggregate=aggregate,
        sort=_parse_sort(options),
        top=top,
        other=other,
        kind=kind,
        title=_text(options, "title"),
        subtitle=_text(options, "subtitle"),
        x_parts=x_parts,
        weight=None if unweighted else _name(options, "weight"),
        unweighted=unweighted,
        groups=_names(options, "groups", VIEW_LIMITS["treemap_groups"]),
        color=_name(options, "color"),
        notes=tuple(notes),
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
        *(("x", x) for x in (spec.x_parts or (spec.x,))),
        *(("y", y) for y in spec.y),
        *(("filter field", f.field) for f in spec.filters),
        *(("group", g) for g in spec.groups),
    ]
    for role, extra in (("series", spec.series), ("weight", spec.weight), ("color", spec.color)):
        if extra:
            roles.append((role, extra))
    for role, name in roles:
        if name not in types:
            raise _unknown(name, role, types)
    numeric = [k for k, t in types.items() if t == "number"]
    for y in (*spec.y, *([spec.weight] if spec.weight else [])):
        if types[y] != "number":
            raise SpecError(
                f"{'weight' if y == spec.weight else 'y'} {y!r} holds {types[y]}; "
                f"it must be numeric: {numeric[:_FIELDS_SHOWN]}",
                fields=_shown(types),
            )
    x_type = "text" if spec.x_parts else types[spec.x]
    if spec.other and x_type != "text":
        raise SpecError(
            f"other needs a category x and {spec.x!r} holds {x_type}; "
            "drop other, or use top alone"
        )
    # Unaggregated rows may sort by any number or date they hold, before top cuts them.
    if (
        spec.sort
        and spec.sort[0] not in (spec.x, *spec.measures)
        and (spec.aggregate is not None or types.get(spec.sort[0]) not in ("number", "time"))
    ):
        choices = [spec.x, *spec.measures]
        raise SpecError(
            f"sort field {spec.sort[0]!r} must be one of {choices}"
            + ("" if spec.aggregate is None else "; with aggregate, sort by x or the measure")
        )


def _repair_name(name: str, types: Mapping[str, str]) -> str | None:
    """*name* as a field of *types*: itself, another case, or one near-identical spelling."""
    if name in types:
        return name
    lowered = {k.lower(): k for k in types}
    if name.lower() in lowered:
        return lowered[name.lower()]
    close = difflib.get_close_matches(name, list(types), n=2, cutoff=0.85)
    return close[0] if len(close) == 1 else None


def _repair_names(spec: ChartSpec, types: Mapping[str, str]) -> ChartSpec:
    notes: list[str] = []

    def fix(name: str | None) -> str | None:
        if name is None:
            return None
        fixed = _repair_name(name, types)
        if fixed is None or fixed == name:
            return name
        notes.append(f"field {name!r} read as {fixed!r}")
        return fixed

    x_parts = tuple(fix(p) or p for p in spec.x_parts)
    x = _composite_key(x_parts) if x_parts else (fix(spec.x) or spec.x)
    y = tuple(fix(v) or v for v in spec.y)
    aggregate = spec.aggregate
    # "y": "count" over rows with no count field means: count the rows.
    if y == ("count",) and "count" not in types:
        y, aggregate = (), "count"
        notes.append("y 'count' read as aggregate count")
    sort = spec.sort
    if sort and sort[0] not in ("__value", x, *y) and (fixed := _repair_name(sort[0], types)):
        sort = (fixed, sort[1])
    return replace(
        spec,
        x=x,
        x_parts=x_parts,
        y=y,
        aggregate=aggregate,
        series=fix(spec.series),
        filters=tuple(replace(f, field=fix(f.field) or f.field) for f in spec.filters),
        groups=tuple(fix(g) or g for g in spec.groups),
        weight=fix(spec.weight),
        color=fix(spec.color),
        sort=sort,
        notes=(*spec.notes, *notes),
    )


def _coerce_filters(spec: ChartSpec, rows: Sequence[Mapping[str, Any]]) -> ChartSpec:
    """A filter's text value read as the bool or number its field holds."""
    if not spec.filters:
        return spec
    sample: dict[str, Any] = {}
    for f in spec.filters:
        sample[f.field] = next((r[f.field] for r in rows if r.get(f.field) is not None), None)
    fixed = []
    for f in spec.filters:
        held, value = sample.get(f.field), f.value
        if isinstance(held, bool) and not isinstance(value, bool) and _flag(value) is not None:
            value = _flag(value)
        elif (
            isinstance(held, (int, float))
            and not isinstance(held, bool)
            and isinstance(value, str)
            and re.fullmatch(r"-?\d+(\.\d+)?", value.strip())
        ):
            value = float(value) if "." in value else int(value)
        fixed.append(replace(f, value=value))
    return replace(spec, filters=tuple(fixed))


def fit(
    spec: ChartSpec,
    rows: Sequence[Mapping[str, Any]],
    types: Mapping[str, str],
    meta: Mapping[str, Any] | None = None,
    hierarchy: Sequence[str] = (),
) -> ChartSpec:
    """*spec* checked against *rows*, with the repairs the rows decide.

    *meta* maps field names to their declared ``aggregates`` and ``weight``
    (a dataset's fields); *hierarchy* is a dataset's nesting, which a treemap
    takes its groups from when it names none. Raises ``SpecError``.
    """
    meta = meta or {}
    spec = _coerce_filters(_repair_names(spec, types), rows)
    # "x" and "value" name the axis and the plotted value when no field has that name.
    if spec.sort and spec.sort[0] not in types:
        name, order = spec.sort
        if name == "x":
            spec = replace(spec, sort=(spec.x, order))
        elif name in ("__value", "value", "y", "count", "total") and spec.measures:
            spec = replace(spec, sort=(spec.measures[0], order))
    x_types = [types.get(p) for p in (spec.x_parts or (spec.x,))]
    x_type = "text" if spec.x_parts else x_types[0]

    if spec.kind == "scatter" and x_type != "number":
        spec = replace(
            spec,
            kind="bar",
            kind_note=(
                f"a scatter needs a numeric x and {spec.x!r} holds {x_type}, so this is a bar; "
                "for a scatter pass two numeric fields as x and y"
            ),
        )

    # A declared aggregate the field cannot take is swapped for one it can, and said.
    if spec.aggregate not in (None, "count"):
        for y in spec.y:
            allowed = getattr(meta.get(y), "aggregates", ())
            if allowed and spec.aggregate not in allowed:
                spec = spec.noted(
                    f"{y} does not take {spec.aggregate}, so it is {allowed[0]}",
                    aggregate=allowed[0],
                )
                break
    if spec.aggregate == "avg" and len(spec.y) == 1:
        spec = _declared_weight(spec, types, meta, grouped=_repeats(spec, rows))

    if spec.kind == "treemap" and spec.aggregate in ("avg", "min", "max") and len(spec.y) == 1:
        measure, size = spec.y[0], spec.weight
        spec = spec.noted(
            f"a treemap's cells add up, so they are sized by {size or 'the row count'} "
            f"and coloured by the {spec.aggregate} {measure}",
            y=(size,) if size else (),
            aggregate="sum" if size else "count",
            color=spec.color or measure,
        )
    if spec.kind == "treemap" and not spec.groups and hierarchy:
        leaf = spec.x_parts[-1] if spec.x_parts else spec.x
        above = tuple(hierarchy[: hierarchy.index(leaf)]) if leaf in hierarchy else ()
        if above:
            spec = spec.noted(f"the treemap nests by {', '.join(above)}", groups=above)
    if spec.kind != "treemap" and (spec.groups or spec.color):
        spec = spec.noted("groups and color shape a treemap only, so they were left out", groups=(), color=None)

    check_fields(spec, types)

    # Rows that share an x combine: by the field's declared aggregate, else summed.
    scatter = spec.kind == "scatter" or (
        spec.kind == "auto" and spec.aggregate is None and x_type == "number"
    )
    if spec.aggregate is None and not scatter and spec.kind != "table" and _repeats(spec, rows):
        allowed = getattr(meta.get(spec.y[0]), "aggregates", ()) if spec.y else ()
        chosen = allowed[0] if allowed else "sum"
        spec = spec.noted(
            f"several rows share each {spec.x}, so {', '.join(spec.y)} is combined by {chosen}",
            aggregate=chosen,
        )
        if chosen == "avg" and len(spec.y) == 1:
            spec = _declared_weight(spec, types, meta, grouped=True)
        if spec.other and chosen not in _ADDITIVE:
            spec = spec.noted("an average does not add up, so no Other", other=False)
    return spec


def _declared_weight(
    spec: ChartSpec, types: Mapping[str, str], meta: Mapping[str, Any], *, grouped: bool
) -> ChartSpec:
    """An average of a field that declares a weight is weighted by it, as its own surface
    averages it (health by lines of code, coverage by coverable lines), unless asked not to."""
    if spec.weight is not None or spec.unweighted:
        return spec
    declared = getattr(meta.get(spec.y[0]), "weight", None)
    if not declared or declared not in types:
        return spec
    # One row per x averages nothing, so only the overall figure is weighted.
    what = f"{spec.y[0]} is averaged" if grouped else f"the overall {spec.y[0]} is"
    return spec.noted(
        f"{what} weighted by {declared}; pass weight: none for a plain mean", weight=declared
    )


def _repeats(spec: ChartSpec, rows: Sequence[Mapping[str, Any]]) -> bool:
    seen: set[Any] = set()
    for row in rows:
        if not all(_passes(f, row.get(f.field)) for f in spec.filters):
            continue
        key = (_x_value(spec, row), row.get(spec.series) if spec.series else None)
        if key[0] is None:
            continue
        if key in seen:
            return True
        seen.add(key)
    return False


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


def _x_value(spec: ChartSpec, row: Mapping[str, Any]) -> Any:
    if not spec.x_parts:
        return row.get(spec.x)
    parts = [row.get(p) for p in spec.x_parts]
    if any(p is None for p in parts):
        return None
    return f" {ARROW} ".join(_value_label(p) for p in parts)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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


def label(key: str) -> str:
    """A field name as a column label: ``line_count`` is "Line count"."""
    return key.replace("_", " ").capitalize()


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
        if spec.color:
            row_out[_color_key(spec, keys)] = found.colors.get(row_out[spec.x])
    return out


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
                if k in types and k != spec.x and k not in spec.y and k not in spec.x_parts
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
    if spec.color:
        color_label = label(spec.color)
        if spec.aggregate and types.get(spec.color) == "number":
            color_label = f"Average {color_label.lower()}"
        columns.append((_color_key(spec, keys), color_label))
    out = _chart_rows(spec, found, keys, label_key)
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
            if spec.color:
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
    return _value_label(value) if isinstance(value, bool) else value


# ---------------------------------------------------------------------------
# Chart type
# ---------------------------------------------------------------------------


def _whole(keys: list[str], rows: list[dict[str, Any]]) -> bool:
    values = [r.get(k) for r in rows for k in keys if r.get(k) is not None]
    return bool(values) and all(_number(v) and v >= 0 for v in values) and sum(values) > 0


def _choose_kind(
    spec: ChartSpec,
    x_type: str,
    keys: list[str],
    rows: list[dict[str, Any]],
    truncated: bool,
    scatter: bool,
) -> tuple[str, str, str | None]:
    """The chart type, why, and a note when the asked kind could not be drawn as asked.

    An asked kind is drawn whenever the data fits it; when it does not, the
    nearest kind that fits is drawn and ``kind_note`` says why.
    """
    additive = spec.aggregate in _ADDITIVE or spec.aggregate is None
    low, high = VIEW_LIMITS["donut_min_parts"], VIEW_LIMITS["donut_max_parts"]
    whole = _whole(keys, rows)
    donut_ok = (
        len(keys) == 1
        and low <= len(rows) <= high
        and not truncated
        and whole
        and all(r.get(keys[0]) is not None for r in rows)
    )
    stack_ok = len(keys) >= 2 and additive and whole
    kind = spec.kind
    asked = f"kind {kind} was asked for"
    if kind == "donut":
        if donut_ok and additive:
            return "donut", asked, None
        why = (
            f"an {spec.aggregate} does not add up to a whole"
            if not additive
            else f"{len(keys)} series" if len(keys) > 1 else f"{len(rows)} parts with some cut"
            if truncated or len(rows) > high
            else "the values are not parts of a positive whole"
        )
        fallback = "stacked_bar" if stack_ok else "bar"
        return fallback, f"kind {fallback} drawn in place of donut", (
            f"a donut shows one measure as {low} to {high} parts of a whole; here {why}, "
            f"so this is a {fallback.replace('_', ' ')}"
        )
    if kind == "stacked_bar":
        if stack_ok:
            return "stacked_bar", asked, None
        return "bar", "kind bar drawn in place of stacked_bar", (
            "a stacked bar needs two or more series of counts or sums; pass series "
            "(a field to split each bar by), so this is a plain bar"
        )
    if kind == "treemap":
        if len(keys) == 1 and whole and additive:
            return "treemap", asked, None
        return "bar", "kind bar drawn in place of treemap", (
            "a treemap sizes cells by one count or sum that adds up; "
            + ("an average does not" if not additive else "this measure does not")
            + ", so this is a bar (size it by a sum such as nloc and colour it by the average)"
        )
    if kind in _LINE_KINDS:
        if x_type in ("time", "number"):
            return kind, asked, None
        return kind, asked, (
            f"a {kind} joins points in order, and {spec.x!r} holds categories with no order, "
            "so the slope between them means nothing; a bar may read better"
        )
    if kind in ("bar", "horizontal_bar", "heatmap", "scatter", "table"):
        return kind, asked, None
    if scatter:
        return "scatter", f"x {spec.x!r} and the plotted values are both numeric", None
    if x_type == "time":
        return "line", f"x {spec.x!r} holds dates", None
    if spec.series and len(keys) > 1:
        if additive and whole:
            return "stacked_bar", f"series {spec.series!r} splits each bar into parts", None
        why = f"{spec.aggregate} does not add up"
        return "bar", f"series {spec.series!r} values sit side by side ({why})", None
    if len(keys) > 1:
        return "bar", "several measures sit side by side", None
    if spec.aggregate in _ADDITIVE and donut_ok and len(rows) <= 5:
        return "donut", f"{len(rows)} parts that add up to one whole", None
    return "bar", "one measure across categories", None


def default_title(spec: ChartSpec) -> str:
    if spec.aggregate == "count":
        measure = "Count"
    else:
        names = ", ".join(label(y).lower() for y in spec.y)
        measure = (
            f"{_AGGREGATE_LABEL[spec.aggregate]} {names}" if spec.aggregate else names.capitalize()
        )
    x = " and ".join(label(p).lower() for p in spec.x_parts) if spec.x_parts else label(spec.x).lower()
    by = x + (f" and {label(spec.series).lower()}" if spec.series else "")
    return f"{measure} by {by}"
