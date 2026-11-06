"""A chart spec as sent: parsed, checked for shape, and the obvious slips repaired.

``parse_spec`` reads chart options into a ``ChartSpec``; every repair is a note
the receipt reports, so a change to what is drawn is never silent.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
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
#: The spec in one paragraph, for every tool that takes one (chart, show_view's chart view).
SPEC_HELP = (
    "Spec: x is a field (a list joins them, e.g. [from_package, to_package]). Omit y to "
    "count rows (the usual case: changes by week is x week, no y); y names a numeric field "
    "to sum or average (aggregate count|sum|avg|min|max). series splits by a field; filter "
    "[{field, op, value}]; sort {field, order}; top; other adds up the rest; color colours "
    "treemap cells; kind, title. The receipt says the kind drawn and why; on an error, "
    "resend its suggested_spec and tell the user its adjustments."
)


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


_MAX_FILTERS = 10


_MAX_TEXT = 120


_MAX_X_PARTS = 3


#: Parts a donut keeps when it folds the rest into Other.
_DONUT_KEEP = VIEW_LIMITS["donut_max_parts"] - 1


_DEFAULT_TOP = 10


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
    #: The calendar step of a date x ("week"), so a grouped series shows empty steps as zero.
    x_step: str | None = None
    #: Each row weighs at least this much (``nloc_weighted_score`` floors lines at 1).
    weight_floor: float | None = None
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


def _value_label(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def label(key: str) -> str:
    """A field name as a column label: ``line_count`` is "Line count"."""
    return key.replace("_", " ").capitalize()
