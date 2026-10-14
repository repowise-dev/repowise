"""Deterministic chat views: tables and charts built from a turn's tool results.

The model never writes a number into a view. It calls the chat-only
``show_view`` tool naming a tool result already in the turn (by the
``artifact_id`` stamped on it) and a view; a registered builder makes the part
from that result, and ``build_view`` adds provenance, applies the guards and
validates it as a ``ViewPart``. Builders are pure functions registered on the
``chat-view`` seam, so a plugin adds one without editing this module.

Guards every builder passes through: no column or axis keyed by a person, no
risk score as a chart series, and an attribution figure below
``ATTRIBUTION_COVERAGE_FLOOR`` (or without coverage) is refused with its
coverage value instead of drawn.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from repowise.core.registry.plugins import SeamRegistry, load_plugins
from repowise.server.chat_artifacts import ARTIFACT_ID_FIELD
from repowise.server.schemas.evidence import MAX_WIRE_NUMBER
from repowise.server.schemas.views import (
    VIEW_LIMITS,
    VIEW_SCHEMA_VERSION,
    ViewPart,
    ViewPartList,
)

logger = logging.getLogger(__name__)

SHOW_VIEW_TOOL = "show_view"
ATTRIBUTION_COVERAGE_FLOOR = 0.30

_PART = TypeAdapter(ViewPart)
_SHA = re.compile(r"^[0-9a-f]{7,64}$")
_PERSON = re.compile(
    r"(^|_)(authors?|owners?|ownership|committers?|emails?|users?|username|login|assignees?"
    r"|reviewers?|maintainers?|contributors?|person|people|knowledge_map)(_|$)"
)
_RISK_SCORE = re.compile(r"risk|impact_score|hotspot_score")
_DEFAULT_ROWS = 50


class ViewError(ValueError):
    """A refusal the model sees as the ``show_view`` result."""

    def __init__(self, message: str, code: str = "view_unavailable", **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra

    def payload(self) -> dict[str, Any]:
        return {"error": str(self), "error_code": self.code, **self.extra}


@dataclass(frozen=True)
class ViewArtifact:
    """One tool result of this turn, as builders read it."""

    artifact_id: str
    tool_name: str
    artifact_type: str
    evidence_basis: str
    arguments: dict[str, Any]
    data: dict[str, Any]


@dataclass(frozen=True)
class ViewBuilder:
    """A pure function from an artifact and options to a part without provenance ids.

    The function returns ``kind``, ``title``, ``basis`` and the kind's fields;
    ``build_view`` adds ``schema_version``, ``id``, ``source``, ``stale`` and
    ``as_of``. ``artifact_types`` None means any artifact.
    """

    build: Callable[[ViewArtifact, Mapping[str, Any]], dict[str, Any]]
    description: str
    artifact_types: frozenset[str] | None = None
    options: frozenset[str] = frozenset({"limit"})
    coverage_metric: str | None = None


view_builders: SeamRegistry[ViewBuilder] = SeamRegistry("chat-view")


def register_view_builder(
    name: str,
    *,
    description: str,
    artifact_types: set[str] | frozenset[str] | None = None,
    options: set[str] | frozenset[str] = frozenset({"limit"}),
    coverage_metric: str | None = None,
) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    """Register a builder as view *name*; the decorated function is returned unchanged."""

    def decorate(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        view_builders.register(
            name,
            ViewBuilder(
                build=fn,
                description=description,
                artifact_types=frozenset(artifact_types) if artifact_types is not None else None,
                options=frozenset(options),
                coverage_metric=coverage_metric,
            ),
        )
        return fn

    return decorate


def _view_names() -> list[str]:
    """Registered view names, plugins included (``names`` alone does not load them)."""
    load_plugins()
    return view_builders.names


def views_for(artifact_type: str) -> list[str]:
    """View names that accept an artifact of *artifact_type*."""
    return [
        name
        for name in _view_names()
        if (types := view_builders.get(name).artifact_types) is None or artifact_type in types
    ]


def args_hash(arguments: Mapping[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _meta(data: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = data.get("_meta")
    return meta if isinstance(meta, Mapping) else {}


def _guard(part: dict[str, Any], spec: ViewBuilder) -> None:
    data = part.get("data", part)
    for column in data.get("columns", []):
        if _PERSON.search(str(column.get("key", ""))):
            raise ViewError(
                f"views never group by person (column {column.get('key')!r})",
                code="person_grouping",
            )
    if part.get("kind") == "chart":
        for key in part.get("series", []):
            if _RISK_SCORE.search(key):
                raise ViewError(
                    f"a risk score is never a chart series ({key!r}); show it in a table",
                    code="risk_score_series",
                )
    if spec.coverage_metric == "attribution":
        coverage = part.get("coverage")
        ratio = coverage.get("ratio") if isinstance(coverage, Mapping) else None
        if not isinstance(ratio, (int, float)) or ratio < ATTRIBUTION_COVERAGE_FLOOR:
            raise ViewError(
                "insufficient coverage for an attribution figure "
                f"(floor {ATTRIBUTION_COVERAGE_FLOOR})",
                code="insufficient_coverage",
                coverage=ratio,
            )


def build_view(
    artifact: ViewArtifact,
    view: str,
    options: Mapping[str, Any] | None = None,
    *,
    part_id: str,
) -> dict[str, Any]:
    """Build and validate one view part, or raise ``ViewError``."""
    if view not in _view_names():
        raise ViewError(
            f"unknown view {view!r}; views for this artifact: {views_for(artifact.artifact_type)}",
            code="unknown_view",
        )
    spec = view_builders.get(view)
    if spec.artifact_types is not None and artifact.artifact_type not in spec.artifact_types:
        raise ViewError(
            f"view {view!r} does not apply to a {artifact.artifact_type} result; "
            f"views for it: {views_for(artifact.artifact_type)}",
            code="view_not_applicable",
        )
    if "error" in artifact.data:
        raise ViewError("the artifact is a failed tool call", code="artifact_failed")
    options = dict(options or {})
    if unknown := sorted(set(options) - spec.options):
        raise ViewError(f"unknown options {unknown}; {view!r} takes {sorted(spec.options)}")

    part = spec.build(artifact, options)
    meta = _meta(artifact.data)
    commit = meta.get("indexed_commit")
    part.update(
        schema_version=VIEW_SCHEMA_VERSION,
        id=part_id,
        source={
            "artifact_id": artifact.artifact_id,
            "tool_name": artifact.tool_name,
            "args_hash": args_hash(artifact.arguments),
        },
        stale=bool(meta.get("stale_warning")),
        as_of=commit if isinstance(commit, str) and _SHA.match(commit) else None,
    )
    _guard(part, spec)
    try:
        model = _PART.validate_python(part)
    except ValidationError as exc:
        raise ViewError(
            f"view {view!r} could not be built from this result: {exc.errors()[0]['msg']}",
            code="invalid_view",
        ) from None
    return _PART.dump_python(model, mode="json", exclude_unset=True)


class TurnViews:
    """The views of one chat turn and the tool results they may be built from.

    ``record`` stamps each tool result with its artifact id before the model
    sees it; ``unstamp`` gives the stored side the server's id and the tool's
    own data back. ``show`` is the ``show_view`` tool. ``parts`` holds the
    validated views in order.
    """

    def __init__(self) -> None:
        self._artifacts: dict[str, ViewArtifact] = {}
        # The exact stamped dicts handed out, so a tool's own ``artifact_id``
        # field is never mistaken for a stamp.
        self._stamped: dict[str, dict[str, Any]] = {}
        self.parts: list[dict[str, Any]] = []

    def record(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        result: Any,
        *,
        artifact_type: str = "generic",
        evidence_basis: str = "unknown",
    ) -> Any:
        """Remember *result* and return it stamped with ``artifact_id``."""
        if not isinstance(result, dict):
            return result
        artifact_id = uuid4().hex
        self._artifacts[artifact_id] = ViewArtifact(
            artifact_id=artifact_id,
            tool_name=tool_name,
            artifact_type=artifact_type,
            evidence_basis=evidence_basis,
            arguments=dict(arguments),
            data=result,
        )
        stamped = {**result, ARTIFACT_ID_FIELD: artifact_id}
        self._stamped[artifact_id] = stamped
        return stamped

    def unstamp(self, result: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        """The server's id and unstamped data for a ``record`` result; else ``(None, result)``."""
        artifact_id = result.get(ARTIFACT_ID_FIELD)
        if isinstance(artifact_id, str) and self._stamped.get(artifact_id) is result:
            return artifact_id, self._artifacts[artifact_id].data
        return None, result

    def show(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Run ``show_view``: a short receipt on success, an error payload otherwise."""
        try:
            part = self._build(arguments)
        except ViewError as exc:
            return exc.payload()
        except Exception:
            # A builder bug (or a plugin's) is the model's error to see, not the turn's end.
            logger.exception("chat_view_builder_failed", extra={"view": arguments.get("view")})
            return ViewError("the view builder failed", code="builder_failed").payload()
        self.parts.append(part)
        receipt = {"view_id": part["id"], "kind": part["kind"], "title": part["title"]}
        if "summary" in part:
            receipt["summary"] = part["summary"]
        return receipt

    def _build(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        artifact_id = arguments.get("artifact_id")
        view = arguments.get("view")
        options = arguments.get("options") or {}
        artifact = self._artifacts.get(artifact_id) if isinstance(artifact_id, str) else None
        if artifact is None:
            raise ViewError(
                f"no tool result with artifact_id {artifact_id!r} in this turn; "
                "call the tool again and use the artifact_id on its result",
                code="unknown_artifact",
            )
        if not isinstance(view, str) or not isinstance(options, Mapping):
            raise ViewError("view must be a string and options an object", code="bad_arguments")
        part = build_view(artifact, view, options, part_id=f"v{len(self.parts) + 1}")
        try:
            ViewPartList.model_validate([*self.parts, part])
        except ValidationError:
            raise ViewError(
                f"a turn holds at most {VIEW_LIMITS['parts']} views", code="view_limit"
            ) from None
        return part

    def event(self, tool_id: str, result: Mapping[str, Any]) -> dict[str, Any] | None:
        """The SSE ``view`` payload for a successful ``show_view`` result."""
        view_id = result.get("view_id")
        part = next((p for p in self.parts if p["id"] == view_id), None)
        return None if part is None else {"type": "view", "tool_id": tool_id, "view": part}


def show_view_schema(artifact_types: list[str] | None = None) -> dict[str, Any]:
    """OpenAI-format definition of the chat-only ``show_view`` tool."""
    names = sorted(
        {n for t in artifact_types for n in views_for(t)} if artifact_types else _view_names()
    )
    return {
        "type": "function",
        "function": {
            "name": SHOW_VIEW_TOOL,
            "description": (
                "Show a table or chart built by the server from a tool result already "
                "returned in this turn. Pass that result's artifact_id. Never restate the "
                "numbers yourself; the view carries them. Views: " + ", ".join(names) + ". "
                "`table` takes options path (dotted path to a list or map in the result), "
                "columns and limit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "view": {"type": "string", "enum": names},
                    "options": {"type": "object"},
                },
                "required": ["artifact_id", "view"],
            },
        },
    }


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _basis(kind: str, source: str) -> dict[str, str]:
    return {"kind": kind, "source": source}


def _tool_basis(artifact: ViewArtifact) -> dict[str, str]:
    declared = artifact.data.get("evidence_kind") or artifact.data.get("basis")
    kind = declared if declared in ("measured", "inferred", "declared") else artifact.evidence_basis
    if kind not in ("measured", "inferred", "declared"):
        return _basis("unresolved", f"{artifact.tool_name} declares no basis")
    return _basis(kind, artifact.tool_name)


def _limit(options: Mapping[str, Any], default: int = _DEFAULT_ROWS, cap: int | None = None) -> int:
    cap = cap or VIEW_LIMITS["rows"]
    value = options.get("limit", default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ViewError("limit must be a positive integer", code="bad_arguments")
    return min(value, cap)


def _wire(value: Any) -> Any:
    """A source value as a cell, or ``...`` when it cannot be one."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and abs(value) <= MAX_WIRE_NUMBER and value == value:
        return value
    return ...


def _cell(key: str, value: Any) -> Any:
    if key in ("file_path", "path") and isinstance(value, str) and value:
        return {"type": "file", "id": value}
    return _wire(value)


def _label(key: str) -> str:
    return key.replace("_", " ").capitalize()


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


def _tabular(node: Any) -> list[dict[str, Any]] | None:
    """Rows from a list of objects, or a map of objects keyed by id."""
    if isinstance(node, list) and node and all(isinstance(row, Mapping) for row in node):
        return [dict(row) for row in node]
    if isinstance(node, Mapping) and node and all(isinstance(v, Mapping) for v in node.values()):
        return [{"id": key, **value} for key, value in node.items()]
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


@register_view_builder(
    "table",
    description="Any list or map of objects in a result, as a table.",
    options={"path", "columns", "limit"},
)
def table_view(artifact: ViewArtifact, options: Mapping[str, Any]) -> dict[str, Any]:
    path = options.get("path")
    if path is None:
        candidates = _table_paths(artifact.data)
        if len(candidates) != 1:
            raise ViewError(
                f"pass options.path; tables in this result: {candidates}", code="bad_path"
            )
        path = candidates[0]
    if not isinstance(path, str) or any(_PERSON.search(seg) for seg in path.split(".")):
        raise ViewError(f"views never group by person ({path!r})", code="person_grouping")
    node = _node(artifact.data, path)
    rows = _tabular(node)
    if rows is None:
        raise ViewError(f"{path!r} is not a list or map of objects", code="bad_path")

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
            raise ViewError(f"columns not available as cells: {missing}", code="bad_arguments")
        keys = list(dict.fromkeys(wanted))
    keys = keys[: VIEW_LIMITS["columns"]]
    if not keys:
        raise ViewError(f"{path!r} has no scalar fields to show", code="bad_path")

    shown = rows[: _limit(options)]
    parent_path, _, leaf = path.rpartition(".")
    parent = _node(artifact.data, parent_path) if parent_path else artifact.data
    total = parent.get(f"{leaf}_total") if isinstance(parent, Mapping) else None
    total_rows = total if isinstance(total, int) and total >= len(rows) else len(rows)
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
) -> dict[str, Any]:
    if not rows:
        raise ViewError("this result has nothing to chart", code="no_data")
    total = len(rows) if total_rows is None else max(total_rows, len(rows))
    return {
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


_HEALTH_BANDS = ("at_risk", "needs_work", "fair", "good", "excellent")


@register_view_builder(
    "health.bands",
    description="Files per health band.",
    artifact_types={"health", "overview"},
    options=frozenset(),
)
def health_bands_view(artifact: ViewArtifact, options: Mapping[str, Any]) -> dict[str, Any]:
    data = artifact.data
    distribution = data.get("distribution") or (data.get("code_health") or {}).get("distribution")
    bands = distribution.get("bands") if isinstance(distribution, Mapping) else None
    if not isinstance(bands, Mapping):
        raise ViewError("this result has no health distribution; call get_health with no targets")
    rows = [
        {"band": band.replace("_", " "), "files": bands[band]["files"]}
        for band in _HEALTH_BANDS
        if isinstance(bands.get(band), Mapping) and isinstance(bands[band].get("files"), int)
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
    )


@register_view_builder(
    "health.trend",
    description="Average and hotspot health over recent snapshots.",
    artifact_types={"health"},
    options=frozenset(),
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
    )


@register_view_builder(
    "health.test_coverage",
    description="Measured line coverage per file, from the ingested coverage report.",
    artifact_types={"health"},
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
    )
    if isinstance(summary_pct, (int, float)) and 0 <= summary_pct <= 100:
        # Rounded so the percentage converts without float noise (88.84 -> 0.8884).
        part["coverage"] = {"metric": "test_reach", "ratio": round(summary_pct / 100, 6)}
    return part


@register_view_builder(
    "dead_code.by_tier",
    description="Dead-code findings per confidence tier.",
    artifact_types={"dead_code"},
    options=frozenset(),
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
    )


@register_view_builder(
    "dead_code.by_kind",
    description="Open dead-code findings per kind.",
    artifact_types={"dead_code"},
    options=frozenset(),
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
    )


@register_view_builder(
    "risk.hotspots",
    description="Bug-fix commits on the repository's top hotspots.",
    artifact_types={"risk"},
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
    return _chart(
        title="Bug fixes on top hotspots",
        summary=f"Bug-fix commits on {len(rows)} hotspot files.",
        basis=_basis("measured", "git history"),
        x=("file", "File"),
        series=[("fix_count", "Bug-fix commits")],
        unit="commits",
        rows=rows,
        total_rows=total,
    )
