"""The chart type a spec is drawn as: the asked kind when the rows fit it, else
the nearest one that does, with a note saying why.
"""

from __future__ import annotations

from typing import Any

from repowise.server.chat_spec.parse import _ADDITIVE, _AGGREGATE_LABEL, ChartSpec, _number, label
from repowise.server.schemas.views import VIEW_LIMITS

_LINE_KINDS = frozenset({"line", "area"})


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
        if not additive:
            why = f"an {spec.aggregate} does not add up to a whole"
        elif len(keys) > 1:
            why = f"there are {len(keys)} series"
        elif len(rows) < low:
            why = f"there is only {len(rows)} part" if len(rows) == 1 else "there are no parts"
        elif truncated or len(rows) > high:
            why = f"{len(rows)} parts with some cut"
        else:
            why = "the values are not parts of a positive whole"
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
