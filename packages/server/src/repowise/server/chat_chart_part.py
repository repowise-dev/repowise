"""Rows and a chart spec to a chart or table part, shared by ``show_view``'s
``chart`` view and the chat-only ``chart`` tool, and an earlier view redrawn.

Spec errors come back with a ``suggested_spec`` that was itself run, so the
model can resend it as is.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from repowise.server import chat_spec
from repowise.server.chat_spec import SpecError
from repowise.server.chat_spec import label as _label
from repowise.server.chat_views import (
    _ATTRIBUTION,
    _PERSON,
    _RISK_SCORE,
    ViewError,
    _cell,
    _wire,
    finish_part,
)
from repowise.server.schemas.views import (
    VIEW_LIMITS,
)


def _spec_error(exc: SpecError, suggested: Mapping[str, Any] | None = None) -> ViewError:
    extra: dict[str, Any] = {"fields": exc.fields} if exc.fields else {}
    if suggested is not None:
        extra["suggested_spec"] = dict(suggested)
    return ViewError(str(exc), code=exc.code, **extra)


@dataclass(frozen=True)
class ChartSource:
    """The rows a chart is drawn over, and what they are."""

    rows: list[dict[str, Any]]
    #: Names the rows in the summary: "files", "rows of tiers.medium.findings".
    what: str
    basis: dict[str, str]
    #: Rows the source holds when the rows given are fewer (a capped tool list).
    total: int | None = None
    #: Declared fields (``aggregates``, ``weight``, ``unit``), when a dataset gives them.
    meta: Mapping[str, Any] = field(default_factory=dict)
    hierarchy: tuple[str, ...] = ()
    #: A spec known to chart these rows, the last resort of a ``suggested_spec``.
    example: Mapping[str, Any] | None = None
    #: The result path the rows came from; the guards read its segments too.
    path: tuple[str, ...] = ()


def _check_names(names: list[str], path: tuple[str, ...]) -> None:
    for name in names:
        if _PERSON.search(name):
            raise ViewError(
                f"views never group or filter by person ({name!r})", code="person_grouping"
            )
    for name in [*names, *path]:
        if _ATTRIBUTION.search(name):
            # Deliberate ceiling: no coverage check per row here, so these are
            # refused; changes.provenance draws them with their coverage.
            raise ViewError(
                f"attribution figures ({name!r}) are shown only by changes.provenance, "
                "which checks their coverage",
                code="attribution_figure",
            )


def _plan(
    source: ChartSource, options: Mapping[str, Any]
) -> tuple[chat_spec.ChartSpec, chat_spec.ChartRows]:
    spec = chat_spec.parse_spec(options)
    _check_names(spec.fields(), source.path)
    types = {
        k: t for k, t in chat_spec.field_types(source.rows).items() if not _PERSON.search(k)
    }
    spec = chat_spec.fit(spec, source.rows, types, source.meta, source.hierarchy)
    _check_names(spec.fields(), ())
    numeric_x = not spec.x_parts and types.get(spec.x) == "number"
    plotted = [*spec.y, *([spec.series] if spec.series else []), *([spec.x] if numeric_x else [])]
    if spec.color and types.get(spec.color) == "number":
        plotted.append(spec.color)
    for name in plotted:
        if _RISK_SCORE.search(name):
            raise ViewError(
                f"a risk score is never a chart series ({name!r}); show it in a table",
                code="risk_score_series",
            )
    return spec, chat_spec.transform(spec, source.rows, types)


def _closest(name: Any, fields: list[str]) -> str | None:
    if not isinstance(name, str):
        return None
    if name in fields:
        return name
    close = difflib.get_close_matches(name, fields, n=1, cutoff=0.5)
    return close[0] if close else None


def _repaired(options: Mapping[str, Any], fields: list[str]) -> dict[str, Any]:
    """*options* with each field name it gets wrong swapped for the closest real one, or dropped."""
    fixed = dict(options)
    for key in ("x", "y", "groups"):
        value = fixed.get(key)
        names = value if isinstance(value, list) else [value] if value is not None else []
        kept = [c for n in names if (c := _closest(n, fields))]
        if key == "x" and not kept:
            continue
        if not kept:
            fixed.pop(key, None)
        else:
            fixed[key] = kept if isinstance(value, list) and len(kept) > 1 else kept[0]
    for key in ("series", "weight", "color"):
        if key in fixed:
            name = _closest(fixed[key], fields)
            if name is None:
                fixed.pop(key)
            else:
                fixed[key] = name
    if "y" not in fixed and fixed.get("aggregate") not in (None, "count"):
        fixed["aggregate"] = "count"
    filters = fixed.get("filter")
    if isinstance(filters, (list, Mapping)):
        filters = [filters] if isinstance(filters, Mapping) else filters
        fixed["filter"] = [
            {**f, "field": name}
            for f in filters
            if isinstance(f, Mapping) and (name := _closest(f.get("field"), fields))
        ]
    return fixed


def _suggest(source: ChartSource, options: Mapping[str, Any]) -> dict[str, Any] | None:
    """A spec that draws, close to *options*, which the model can resend as is.

    Each candidate is run, so a suggestion is never itself an error: the
    names repaired, then without kind, filters, sort and top, or series, and
    last the source's own example.
    """
    fields = sorted(chat_spec.field_types(source.rows))
    base = _repaired(options, fields)
    drops = ((), ("kind",), ("filter",), ("sort", "top", "other"), ("series",))
    candidates = [{k: v for k, v in base.items() if k not in keys} for keys in drops]
    if source.example is not None:
        candidates.append({**dict(source.example), **({"rows": options["rows"]} if "rows" in options else {})})
    for candidate in candidates:
        if candidate == dict(options):
            continue
        try:
            _plan(source, candidate)
        except (SpecError, ViewError):
            continue
        return candidate
    return None


def _units(spec: chat_spec.ChartSpec, meta: Mapping[str, Any]) -> str | None:
    """The plotted columns' one unit, when the fields declare one they share."""
    if spec.aggregate == "count":
        return None
    units = {getattr(meta.get(y), "unit", None) for y in spec.y}
    return units.pop() if len(units) == 1 else None


def _n(value: Any) -> str:
    """A number as a summary writes it: digits grouped, averages to two places."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}"


def chart_part(source: ChartSource, options: Mapping[str, Any]) -> dict[str, Any]:
    """A chart (or table) part from *source*'s rows by the spec in *options*, with its receipt.

    Shared by ``show_view``'s ``chart`` view and the ``chart`` tool. Raises
    ``ViewError``; a spec error carries a ``suggested_spec`` when one draws.
    """
    try:
        spec, result = _plan(source, options)
    except SpecError as exc:
        raise _spec_error(exc, _suggest(source, options)) from None

    def cell(key: str, value: Any) -> Any:
        value = _cell(key, value) if key not in result.series else _wire(value)
        return None if value is ... else value

    figures = result.figures
    x_label = _label(spec.x) if not spec.x_parts else dict(result.columns).get(spec.x, spec.x)
    drawn = result.matched - result.missing_x
    grouped = bool(spec.aggregate) and figures.get("groups", drawn) < drawn
    shown = (
        f"{_n(result.matched)} of {_n(result.source_rows)}"
        if spec.filters
        else _n(result.source_rows)
    )
    summary = f"{shown} {source.what}"
    if grouped:
        summary += f", grouped by {x_label.lower()} into {_n(figures['groups'])}"
    summary += "."
    if grouped and spec.aggregate == "sum" and "total" in figures:
        measures = " and ".join(_label(y).lower() for y in spec.y)
        summary += f" Total {measures}: {_n(figures['total'])}."
    for name, value in (figures.get("overall") or {}).items():
        if grouped and value is not None:
            weighted = f", weighted by {_label(spec.weight).lower()}" if spec.weight else ""
            summary += f" {name} across them: {_n(value)}{weighted}."
    if result.total_rows > len(result.rows) and not result.other:
        summary += f" Showing {_n(len(result.rows))} of {_n(result.total_rows)}."
        figures["drawn"] = len(result.rows)
    if result.missing_x:
        summary += f" {_n(result.missing_x)} without {x_label.lower()} are left out."
    if result.other:
        summary += f" {chat_spec.OTHER_LABEL} adds up the rest."
    if source.total is not None and source.total > len(source.rows):
        summary += (
            f" The tool returned {len(source.rows)} of {source.total} rows; "
            "the chart covers those only."
        )
    unit = _units(spec, source.meta)
    columns = [
        {"key": k, "label": label, **({"unit": unit} if unit and k in result.series else {})}
        for k, label in result.columns
    ]
    rows = [{k: cell(k, v) for k, v in row.items()} for row in result.rows]
    title = spec.title or chat_spec.default_title(spec)
    summary = f"{spec.subtitle} {summary}" if spec.subtitle else summary
    receipt: dict[str, Any] = {"reason": result.reason}
    if result.kind_note:
        receipt["kind_note"] = result.kind_note
    if result.notes:
        receipt["notes"] = result.notes
    receipt["figures"] = figures
    if result.chart_type == "table":
        return {
            "kind": "table",
            "title": title,
            "summary": summary,
            "basis": source.basis,
            "columns": columns[: VIEW_LIMITS["columns"]],
            "rows": [
                {c["key"]: row.get(c["key"]) for c in columns[: VIEW_LIMITS["columns"]]}
                for row in rows
            ],
            "total_rows": result.total_rows,
            "truncated": result.total_rows > len(rows),
            "receipt": receipt,
        }
    part: dict[str, Any] = {
        "kind": "chart",
        "chart_type": result.chart_type,
        "title": title,
        "summary": summary,
        "basis": source.basis,
        "x": result.x,
        "series": result.series,
        "data": {
            "columns": columns,
            "rows": rows,
            "total_rows": result.total_rows,
            "truncated": result.total_rows > len(result.rows),
        },
        "receipt": {"chart_type": result.chart_type, **receipt},
    }
    if result.sort:
        part["sort"] = result.sort
    if result.chart_type == "treemap":
        if result.groups:
            part["groups"] = result.groups
        if result.color:
            part["color"] = result.color
    return part


def redraw(part: Mapping[str, Any], options: Any, *, part_id: str) -> dict[str, Any]:
    """An earlier chart drawn again as ``options.kind``: another chart type, or its table.

    Its rows and provenance are the earlier part's; only the form changes, and
    the part is validated again, so a kind the rows cannot take is refused.
    """
    asked = options.get("kind") if isinstance(options, Mapping) else None
    if asked is None:
        raise ViewError("pass options.kind: the kind to draw this view as", code="bad_arguments")
    try:
        kind, _ = chat_spec.read_kind(asked)
    except SpecError as exc:
        raise ViewError(str(exc), code="bad_arguments") from None
    if part.get("kind") != "chart":
        raise ViewError(
            "only a chart can be redrawn; call chart with kind table or another kind",
            code="bad_arguments",
        )
    copy = {
        k: v
        for k, v in part.items()
        if k not in ("id", "schema_version", "source", "stale", "as_of", "groups", "color", "sort")
    }
    if kind == "table":
        for key in ("chart_type", "x", "series", "data"):
            copy.pop(key, None)
        copy.update(kind="table", **part["data"])
    elif kind != "auto":
        copy["chart_type"] = kind
        if kind == "treemap":
            copy.update({k: part[k] for k in ("groups", "color") if k in part})
    if isinstance(options.get("title"), str) and options["title"].strip():
        copy["title"] = options["title"].strip()
    if kind in ("bar", "horizontal_bar", "stacked_bar", "line", "area") and "sort" in part:
        copy["sort"] = part["sort"]
    try:
        return finish_part(
            copy,
            part_id=part_id,
            source=part["source"],
            stale=bool(part.get("stale")),
            as_of=part.get("as_of"),
            what=f"a {kind}",
        )
    except ViewError as exc:
        raise ViewError(
            f"{exc}; call chart with the spec and kind {kind} to draw it from the data",
            code=exc.code,
        ) from None
