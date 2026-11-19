"""The named views ``show_view`` builds from a tool result: ``table``, ``chart``
and one per known result shape (``health.bands``, ``changes.by_week``, ...).

Each is registered on the ``chat-view`` seam when :mod:`repowise.server.chat_views`
is imported. A named view takes ``options.kind`` when its rows fit that kind.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# The module, not its names: it may still be loading when chat_views imports this one.
from repowise.server import chat_chart_part, chat_spec
from repowise.server.chat_references import (
    ViewArtifact,
)
from repowise.server.chat_spec import SpecError
from repowise.server.chat_spec import label as _label
from repowise.server.chat_views import _PERSON, ViewError, _cell, _wire, register_view_builder
from repowise.server.schemas.views import (
    VIEW_LIMITS,
)

_DEFAULT_ROWS = 50


def _basis(kind: str, source: str) -> dict[str, str]:
    return {"kind": kind, "source": source}


def _tool_basis(artifact: ViewArtifact) -> dict[str, str]:
    declared = artifact.data.get("evidence_kind") or artifact.data.get("basis")
    kind = declared if declared in ("measured", "inferred", "declared") else artifact.evidence_basis
    if kind not in ("measured", "inferred", "declared"):
        return _basis("unresolved", artifact.tool_name)
    return _basis(kind, artifact.tool_name)


def _limit(options: Mapping[str, Any], default: int = _DEFAULT_ROWS, cap: int | None = None) -> int:
    cap = cap or VIEW_LIMITS["rows"]
    value = options.get("limit", default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ViewError("limit must be a positive integer", code="bad_arguments")
    return min(value, cap)


def _node(data: Any, path: str) -> Any:
    node = data
    for segment in path.split("."):
        if isinstance(node, Mapping) and segment in node:
            node = node[segment]
        elif isinstance(node, list) and segment.isdigit() and int(segment) < len(node):
            node = node[int(segment)]
        else:
            raise ViewError(f"no {path!r} in the artifact", code="bad_path")
    return node


def _tabular(node: Any, scalars: bool = False) -> list[dict[str, Any]] | None:
    """Rows from a list of objects, a map of objects keyed by id, or (*scalars*)
    a map of plain values such as ``{"fix": 312, "feature": 120}`` as ``{key, value}``."""
    if isinstance(node, list) and node and all(isinstance(row, Mapping) for row in node):
        return [dict(row) for row in node]
    if isinstance(node, Mapping) and node and all(isinstance(v, Mapping) for v in node.values()):
        return [{"id": key, **value} for key, value in node.items()]
    if (
        scalars
        and isinstance(node, Mapping)
        and node
        and all(isinstance(v, (str, int, float, bool)) or v is None for v in node.values())
    ):
        return [{"key": str(key), "value": value} for key, value in node.items()]
    return None


def _table_paths(data: Mapping[str, Any], prefix: str = "", depth: int = 0) -> list[str]:
    paths: list[str] = []
    for key, value in data.items():
        if key == "_meta" or _PERSON.search(key):
            continue
        path = f"{prefix}{key}"
        if _tabular(value) is not None:
            paths.append(path)
        elif isinstance(value, Mapping) and depth < 2:
            paths.extend(_table_paths(value, f"{path}.", depth + 1))
    return paths


def _rows_at(
    data: Mapping[str, Any], path: Any, option: str
) -> tuple[str, list[dict[str, Any]], int]:
    """The list or map of objects at *path* (picked when the result holds one),
    its rows, and the row count the tool reports for it (``<leaf>_total``)."""
    if path is None:
        candidates = _table_paths(data)
        if len(candidates) != 1:
            raise ViewError(
                f"pass options.{option}; tables in this result: {candidates}", code="bad_path"
            )
        path = candidates[0]
    if not isinstance(path, str) or any(_PERSON.search(seg) for seg in path.split(".")):
        raise ViewError(f"views never group by person ({path!r})", code="person_grouping")
    rows = _tabular(_node(data, path), scalars=True)
    if rows is None:
        raise ViewError(
            f"{path!r} is not a list or map; tables in this result: {_table_paths(data)}",
            code="bad_path",
        )
    parent_path, _, leaf = path.rpartition(".")
    parent = _node(data, parent_path) if parent_path else data
    total = parent.get(f"{leaf}_total") if isinstance(parent, Mapping) else None
    return path, rows, total if isinstance(total, int) and total >= len(rows) else len(rows)


@register_view_builder(
    "table",
    description="Any list or map of objects in a result, as a table.",
    options={"path", "columns", "limit"},
)
def table_view(artifact: ViewArtifact, options: Mapping[str, Any]) -> dict[str, Any]:
    path, rows, total_rows = _rows_at(artifact.data, options.get("path"), "path")
    wanted = options.get("columns")
    if wanted is not None and not (
        isinstance(wanted, list) and all(isinstance(k, str) for k in wanted)
    ):
        raise ViewError("columns must be a list of keys", code="bad_arguments")
    keys = list(dict.fromkeys(k for row in rows for k in row))
    # A column is a key whose every value is a cell; nested values and people are left out.
    keys = [
        k
        for k in keys
        if not _PERSON.search(k)
        and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", k)
        and all(_cell(k, row.get(k)) is not ... for row in rows)
    ]
    if wanted is not None:
        if missing := [k for k in wanted if k not in keys]:
            raise ViewError(
                f"columns not available as cells: {missing}; columns of {path!r}: {keys}",
                code="bad_arguments",
                suggested_options={**options, "columns": [k for k in wanted if k in keys] or keys},
            )
        keys = list(dict.fromkeys(wanted))
    keys = keys[: VIEW_LIMITS["columns"]]
    if not keys:
        raise ViewError(f"{path!r} has no scalar fields to show", code="bad_path")

    shown = rows[: _limit(options)]
    return {
        "kind": "table",
        "title": f"{artifact.tool_name}: {path}",
        "summary": f"{len(shown)} of {total_rows} rows.",
        "basis": _tool_basis(artifact),
        "columns": [{"key": k, "label": _label(k)} for k in keys],
        "rows": [{k: _cell(k, row.get(k)) for k in keys} for row in shown],
        "total_rows": total_rows,
        "truncated": total_rows > len(shown),
    }


@register_view_builder(
    "chart",
    description="A chart you specify over any list or map in a result.",
    options=chat_spec.OPTIONS,
)
def chart_view(artifact: ViewArtifact, options: Mapping[str, Any]) -> dict[str, Any]:
    path, rows, source_total = _rows_at(artifact.data, options.get("rows"), "rows")
    source = chat_chart_part.ChartSource(
        rows=rows,
        what=f"rows of {path}",
        basis=_tool_basis(artifact),
        total=source_total,
        path=tuple(path.split(".")),
    )
    return chat_chart_part.chart_part(source, options)


#: Kinds a named view takes in ``options.kind``: categories, or points in time.
_CATEGORY_KINDS = ("bar", "horizontal_bar", "donut", "table")


_TIME_KINDS = ("line", "area", "bar", "table")


def _chart(
    *,
    title: str,
    summary: str,
    basis: dict[str, str],
    x: tuple[str, str],
    series: list[tuple[str, str]],
    unit: str,
    rows: list[dict[str, Any]],
    total_rows: int | None = None,
    chart_type: str = "bar",
    options: Mapping[str, Any] | None = None,
    kinds: tuple[str, ...] = _CATEGORY_KINDS,
    parts: bool = True,
) -> dict[str, Any]:
    """A named view's chart. ``options.kind`` redraws it as another of *kinds*
    when the rows fit that kind; *parts* says the rows add up to a whole, so
    the receipt carries their total and shares."""
    if not rows:
        raise ViewError("this result has nothing to chart", code="no_data")
    total = len(rows) if total_rows is None else max(total_rows, len(rows))
    part: dict[str, Any] = {
        "kind": "chart",
        "chart_type": chart_type,
        "title": title,
        "summary": summary,
        "basis": basis,
        "x": x[0],
        "series": [key for key, _ in series],
        "data": {
            "columns": [
                {"key": x[0], "label": x[1]},
                *({"key": key, "label": label, "unit": unit} for key, label in series),
            ],
            "rows": rows,
            "total_rows": total,
            "truncated": total > len(rows),
        },
    }
    receipt: dict[str, Any] = {}
    if parts and len(series) == 1:
        receipt["figures"] = _shares(rows, x[0], series[0][0])
    asked = (options or {}).get("kind")
    if asked is not None:
        _redraw(part, asked, kinds, receipt)
    if receipt:
        part["receipt"] = {**({"chart_type": part["chart_type"]} if part["kind"] == "chart" else {}), **receipt}
    return part


def _shares(rows: list[dict[str, Any]], x: str, key: str) -> dict[str, Any]:
    values = [(_label_of(r[x]), r[key]) for r in rows if isinstance(r.get(key), (int, float))]
    whole = sum(v for _, v in values)
    figures: dict[str, Any] = {"total": whole}
    if whole > 0:
        figures["shares"] = [
            {"x": name, "value": v, "pct": round(100 * v / whole, 1)} for name, v in values[:12]
        ]
    return figures


def _label_of(cell: Any) -> str:
    return str(cell.get("id")) if isinstance(cell, Mapping) else str(cell)


def _redraw(part: dict[str, Any], asked: Any, kinds: tuple[str, ...], receipt: dict[str, Any]) -> None:
    """Draw *part* as the *asked* kind when its rows fit it; else keep it and say why."""
    try:
        kind, note = chat_spec.read_kind(asked)
    except SpecError as exc:
        raise ViewError(f"{exc}; this view takes {list(kinds)}", code="bad_arguments") from None
    if note:
        receipt.setdefault("notes", []).append(note)
    drawn = part["chart_type"]
    if kind == drawn or kind == "auto":
        return
    if kind not in kinds:
        receipt["kind_note"] = (
            f"this view draws as {', '.join(kinds)}, not {kind}, so it stays a {drawn}"
        )
        return
    data = part["data"]
    if kind == "table":
        for key in ("chart_type", "x", "series", "sort", "data"):
            part.pop(key, None)
        part.update(kind="table", **data)
        return
    if kind == "donut":
        values = [r.get(s) for r in data["rows"] for s in part["series"]]
        low, high = VIEW_LIMITS["donut_min_parts"], VIEW_LIMITS["donut_max_parts"]
        fits = (
            len(part["series"]) == 1
            and low <= len(data["rows"]) <= high
            and not data["truncated"]
            and all(isinstance(v, (int, float)) and v >= 0 for v in values)
            and sum(values) > 0
        )
        if not fits:
            receipt["kind_note"] = (
                f"a donut shows {low} to {high} parts of one whole, which these "
                f"{len(data['rows'])} rows are not, so it stays a {drawn}"
            )
            return
    part["chart_type"] = kind
    part.pop("sort", None)


_HEALTH_BANDS = ("at_risk", "needs_work", "fair", "good", "excellent")


@register_view_builder(
    "health.bands",
    description="Files per health band.",
    artifact_types={"health", "overview"},
    options=frozenset({"kind"}),
)
def health_bands_view(artifact: ViewArtifact, options: Mapping[str, Any]) -> dict[str, Any]:
    data = artifact.data
    distribution = data.get("distribution")
    bands = distribution.get("bands") if isinstance(distribution, Mapping) else None
    if isinstance(bands, Mapping):
        files = {b: v.get("files") for b, v in bands.items() if isinstance(v, Mapping)}
    else:
        # The overview carries the per-band file counts alone.
        files = (data.get("code_health") or {}).get("files_by_band")
    if not isinstance(files, Mapping):
        raise ViewError("this result has no health distribution; call get_health with no targets")
    rows = [
        {"band": band.replace("_", " "), "files": files[band]}
        for band in _HEALTH_BANDS
        if isinstance(files.get(band), int)
    ]
    total = sum(row["files"] for row in rows)
    return _chart(
        title="Files by health band",
        summary=f"{total} files across {len(rows)} health bands.",
        basis=_basis("inferred", "health engine"),
        x=("band", "Health band"),
        series=[("files", "Files")],
        unit="files",
        rows=rows,
        options=options,
    )


@register_view_builder(
    "health.trend",
    description="Average and hotspot health over recent snapshots.",
    artifact_types={"health"},
    options=frozenset({"kind"}),
)
def health_trend_view(artifact: ViewArtifact, options: Mapping[str, Any]) -> dict[str, Any]:
    trend = artifact.data.get("trend")
    recent = trend.get("recent") if isinstance(trend, Mapping) else None
    if not isinstance(recent, list) or not recent:
        raise ViewError('this result has no trend; call get_health(include=["trend"])')
    snapshots = sorted(
        (s for s in recent if isinstance(s, Mapping) and isinstance(s.get("taken_at"), str)),
        key=lambda s: s["taken_at"],
    )
    rows = [
        {
            "taken_at": s["taken_at"],
            "average_health": _wire(s.get("average_health")),
            "hotspot_health": _wire(s.get("hotspot_health")),
        }
        for s in snapshots
    ]
    total = trend.get("recent_total")
    return _chart(
        chart_type="line",
        title="Health over recent snapshots",
        summary=f"{len(rows)} health snapshots, oldest first.",
        basis=_basis("inferred", "health snapshots"),
        x=("taken_at", "Snapshot"),
        series=[("average_health", "Average health"), ("hotspot_health", "Hotspot health")],
        unit="score",
        rows=rows,
        total_rows=total if isinstance(total, int) else None,
        options=options,
        kinds=_TIME_KINDS,
        parts=False,
    )


@register_view_builder(
    "health.test_coverage",
    description="Measured line coverage per file, from the ingested coverage report.",
    artifact_types={"health"},
    options=frozenset({"limit", "kind"}),
)
def health_test_coverage_view(
    artifact: ViewArtifact, options: Mapping[str, Any]
) -> dict[str, Any]:
    coverage = artifact.data.get("coverage")
    files = coverage.get("files") if isinstance(coverage, Mapping) else None
    if not isinstance(files, list):
        raise ViewError('this result has no coverage; call get_health(include=["coverage"])')
    usable = [f for f in files if isinstance(f, Mapping) and f.get("file_path")]
    shown = usable[: _limit(options, cap=100)]
    rows = [
        {
            "file": {"type": "file", "id": f["file_path"]},
            "line_coverage": _wire(f.get("line_coverage_pct")),
        }
        for f in shown
    ]
    summary_pct = (coverage.get("summary") or {}).get("line_coverage_pct")
    total = coverage.get("files_total")
    total = max(total, len(usable)) if isinstance(total, int) else len(usable)
    part = _chart(
        title="Line coverage by file",
        summary=f"{len(rows)} files from the coverage report.",
        basis=_basis("measured", "coverage report"),
        x=("file", "File"),
        series=[("line_coverage", "Line coverage")],
        unit="%",
        rows=rows,
        total_rows=total,
        options=options,
        parts=False,
    )
    if isinstance(summary_pct, (int, float)) and 0 <= summary_pct <= 100:
        # Rounded so the percentage converts without float noise (88.84 -> 0.8884).
        part["coverage"] = {"metric": "test_reach", "ratio": round(summary_pct / 100, 6)}
    return part


@register_view_builder(
    "dead_code.by_tier",
    description="Dead-code findings per confidence tier.",
    artifact_types={"dead_code"},
    options=frozenset({"kind"}),
)
def dead_code_by_tier_view(artifact: ViewArtifact, options: Mapping[str, Any]) -> dict[str, Any]:
    tiers = artifact.data.get("tiers")
    if not isinstance(tiers, Mapping):
        raise ViewError("this result has no confidence tiers")
    rows = [
        {"tier": tier, "findings": tiers[tier]["count"]}
        for tier in ("high", "medium", "low")
        if isinstance(tiers.get(tier), Mapping) and isinstance(tiers[tier].get("count"), int)
    ]
    return _chart(
        title="Dead-code findings by tier",
        summary=f"{sum(r['findings'] for r in rows)} findings kept by the call's filters.",
        basis=_basis("inferred", "reachability graph"),
        x=("tier", "Confidence tier"),
        series=[("findings", "Findings")],
        unit="findings",
        rows=rows,
        options=options,
    )


@register_view_builder(
    "dead_code.by_kind",
    description="Open dead-code findings per kind.",
    artifact_types={"dead_code"},
    options=frozenset({"kind"}),
)
def dead_code_by_kind_view(artifact: ViewArtifact, options: Mapping[str, Any]) -> dict[str, Any]:
    by_kind = (artifact.data.get("summary") or {}).get("by_kind")
    if not isinstance(by_kind, Mapping) or not by_kind:
        raise ViewError("this result has no findings by kind")
    rows = [
        {"kind": kind, "findings": count}
        for kind, count in by_kind.items()
        if isinstance(count, int) and not isinstance(count, bool)
    ]
    return _chart(
        title="Dead-code findings by kind",
        summary=f"{sum(r['findings'] for r in rows)} open findings.",
        basis=_basis("inferred", "reachability graph"),
        x=("kind", "Kind"),
        series=[("findings", "Findings")],
        unit="findings",
        rows=rows,
        options=options,
    )


@register_view_builder(
    "risk.hotspots",
    description="Bug-fix commits on the repository's top hotspots.",
    artifact_types={"risk"},
    options=frozenset({"limit", "kind"}),
)
def risk_hotspots_view(artifact: ViewArtifact, options: Mapping[str, Any]) -> dict[str, Any]:
    hotspots = artifact.data.get("global_hotspots")
    if not isinstance(hotspots, list) or not hotspots:
        raise ViewError("this result has no global hotspots")
    usable = [h for h in hotspots if isinstance(h, Mapping) and h.get("file_path")]
    shown = usable[: _limit(options, cap=100)]
    rows = [
        {"file": {"type": "file", "id": h["file_path"]}, "fix_count": _wire(h.get("fix_count"))}
        for h in shown
    ]
    total = artifact.data.get("global_hotspots_total")
    total = max(total, len(usable)) if isinstance(total, int) else len(usable)
    # The rows share one window; name it so the counts never read as 90-day churn.
    window = next((h["window"] for h in shown if h.get("window")), None)
    over = f" in the last {window}" if window else ""
    return _chart(
        title="Bug fixes on top hotspots",
        summary=f"Bug-fix commits{over} on {len(rows)} hotspot files.",
        basis=_basis("measured", "git history"),
        x=("file", "File"),
        series=[("fix_count", "Bug-fix commits")],
        unit="commits",
        rows=rows,
        total_rows=total,
        options=options,
        parts=False,
    )


def _changes_block(artifact: ViewArtifact) -> Mapping[str, Any]:
    block = artifact.data.get("changes")
    if not isinstance(block, Mapping) or not block.get("total"):
        raise ViewError(
            'this result has no change records; call get_overview(include=["changes"])'
        )
    return block


def _window(block: Mapping[str, Any]) -> str:
    window = block.get("window")
    start = window.get("history_from") if isinstance(window, Mapping) else None
    if isinstance(start, str):
        return f" since {start[:10]}, where the indexed history starts"
    days = window.get("days") if isinstance(window, Mapping) else None
    return f" over the last {days} days of merged changes" if isinstance(days, int) else ""


@register_view_builder(
    "changes.by_category",
    description="Merged changes per category (feature, fix, ...).",
    artifact_types={"overview"},
    options=frozenset({"kind"}),
)
def changes_by_category_view(artifact: ViewArtifact, options: Mapping[str, Any]) -> dict[str, Any]:
    block = _changes_block(artifact)
    by_category = block.get("by_category")
    rows = [
        {"category": category, "changes": count}
        for category, count in (by_category.items() if isinstance(by_category, Mapping) else ())
        if isinstance(count, int) and not isinstance(count, bool)
    ]
    return _chart(
        title="Changes by category",
        summary=f"{sum(r['changes'] for r in rows)} changes{_window(block)}.",
        basis=_basis("measured", "git history"),
        x=("category", "Category"),
        series=[("changes", "Changes")],
        unit="changes",
        rows=rows,
        options=options,
    )


@register_view_builder(
    "changes.by_week",
    description="Merged changes per week.",
    artifact_types={"overview"},
    options=frozenset({"kind"}),
)
def changes_by_week_view(artifact: ViewArtifact, options: Mapping[str, Any]) -> dict[str, Any]:
    block = _changes_block(artifact)
    weeks = block.get("by_week")
    rows = [
        {"week": w["week"], "changes": w["changes"]}
        for w in (weeks if isinstance(weeks, list) else ())
        if isinstance(w, Mapping)
        and isinstance(w.get("week"), str)
        and isinstance(w.get("changes"), int)
        and not isinstance(w.get("changes"), bool)
    ][-VIEW_LIMITS["chart_points"] :]
    return _chart(
        chart_type="line",
        title="Changes per week",
        summary=f"{len(rows)} weeks{_window(block)}, oldest first.",
        basis=_basis("measured", "git history"),
        x=("week", "Week of"),
        series=[("changes", "Changes")],
        unit="changes",
        rows=rows,
        options=options,
        kinds=_TIME_KINDS,
    )


_PROVENANCE_BANDS = (
    ("agent", "agent_changes"),
    ("human attested", "human_attested_changes"),
    ("unknown", "unknown_changes"),
)


@register_view_builder(
    "changes.provenance",
    description="Merged changes by source: agent, human-attested, unknown.",
    artifact_types={"overview"},
    options=frozenset(),
    coverage_metric="attribution",
)
def changes_provenance_view(artifact: ViewArtifact, options: Mapping[str, Any]) -> dict[str, Any]:
    block = _changes_block(artifact)
    provenance = block.get("provenance")
    if not isinstance(provenance, Mapping) or "coverage" not in provenance:
        raise ViewError("attribution is off for this repository", code="attribution_off")
    # Unknown is its own band, never folded into human.
    rows = [
        {"source": label, "changes": provenance[key]}
        for label, key in _PROVENANCE_BANDS
        if isinstance(provenance.get(key), int) and not isinstance(provenance.get(key), bool)
    ]
    part = _chart(
        chart_type="donut",
        title="Changes by source",
        summary=f"{sum(r['changes'] for r in rows)} changes{_window(block)}.",
        basis=_basis("measured", "git attribution markers"),
        x=("source", "Source"),
        series=[("changes", "Changes")],
        unit="changes",
        rows=rows,
    )
    coverage = provenance.get("coverage")
    if isinstance(coverage, (int, float)) and not isinstance(coverage, bool):
        part["coverage"] = {"metric": "attribution", "ratio": coverage}
    return part
