"""A spec checked against the rows it charts, with the repairs the rows decide.

``fit`` swaps a near-miss field name for the field, combines rows that share
an x by the field's declared aggregate, and weights an average the way the
field's own surface does. Every repair is a note.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from repowise.server.chat_spec.parse import (
    _ADDITIVE,
    _KEY,
    ChartSpec,
    SpecError,
    _composite_key,
    _flag,
    _passes,
    _x_value,
)

_TIME = re.compile(
    r"^\d{4}-\d{2}(-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?)?$"
)


_FIELDS_SHOWN = 30


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
    # parse_spec drops these too; a repaired name can make the repeat only now.
    if spec.series and (spec.series == spec.x or spec.series in spec.y):
        spec = spec.noted(f"series {spec.series!r} repeats x or y, so it was dropped", series=None)
    # "x" and "value" name the axis and the plotted value when no field has that name.
    if spec.sort and spec.sort[0] not in types:
        name, order = spec.sort
        if name == "x":
            spec = replace(spec, sort=(spec.x, order))
        elif name in ("__value", "value", "y", "count", "total") and spec.measures:
            spec = replace(spec, sort=(spec.measures[0], order))
    x_types = [types.get(p) for p in (spec.x_parts or (spec.x,))]
    x_type = "text" if spec.x_parts else x_types[0]

    step = None if spec.x_parts else getattr(meta.get(spec.x), "step", None)
    if step:
        spec = replace(spec, x_step=step)
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
    # A group that is also the x, a part of it or a plotted field would be its column
    # twice. Dropped before the hierarchy fill, so a treemap left with none still nests.
    taken = {spec.x, *spec.x_parts, *spec.y, *([spec.series] if spec.series else [])}
    if spec.kind == "treemap" and (repeats := [g for g in spec.groups if g in taken]):
        kept = tuple(g for g in spec.groups if g not in taken)
        spec = spec.noted(
            f"groups {repeats} are already the x or a plotted field, so they were left out"
            + (f"; the treemap nests by {', '.join(kept)}" if kept else ""),
            groups=kept,
        )
    if spec.kind == "treemap" and not spec.groups and hierarchy:
        leaf = spec.x_parts[-1] if spec.x_parts else spec.x
        levels = hierarchy[: hierarchy.index(leaf)] if leaf in hierarchy else ()
        above = tuple(h for h in levels if h not in taken)
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
    if spec.unweighted:
        return spec
    declared = getattr(meta.get(spec.y[0]), "weight", None)
    if spec.weight is not None:
        # The declared weight asked for by name takes its declared floor too.
        if spec.weight == declared:
            return replace(spec, weight_floor=getattr(meta.get(spec.y[0]), "weight_floor", None))
        return spec
    if not declared or declared not in types:
        return spec
    # One row per x averages nothing, so only the overall figure is weighted.
    what = f"{spec.y[0]} is averaged" if grouped else f"the overall {spec.y[0]} is"
    return spec.noted(
        f"{what} weighted by {declared}; pass weight: none for a plain mean",
        weight=declared,
        weight_floor=getattr(meta.get(spec.y[0]), "weight_floor", None),
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
