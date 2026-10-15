"""Deterministic chat views: tables and charts built from a turn's tool results.

The model never writes a number into a view. It calls the chat-only
``show_view`` tool naming a tool result of the conversation (by the
``artifact_id`` stamped on it, or ``latest:<tool_name>``; see
:mod:`repowise.server.chat_references`) and a view; a registered builder makes the part
from that result, and ``build_view`` adds provenance, applies the guards and
validates it as a ``ViewPart``. Builders are pure functions registered on the
``chat-view`` seam, so a plugin adds one without editing this module.

``chart_part`` turns rows and a chart spec into a part; the ``chart`` view
and the chat-only ``chart`` tool (:mod:`repowise.server.chat_chart`) share it.

Guards every builder passes through: no column or axis keyed by a person, no
risk score as a chart series, and an attribution figure below
``ATTRIBUTION_COVERAGE_FLOOR`` (or without coverage) is refused with its
coverage value instead of drawn.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from repowise.core.registry.plugins import SeamRegistry, load_plugins
from repowise.server import chat_view_spec
from repowise.server.chat_artifacts import ARTIFACT_ID_FIELD
from repowise.server.chat_references import (
    LATEST_PREFIX,
    SHOW_VIEW_TOOL,
    ArtifactIndex,
    ViewArtifact,
    stamp,
)
from repowise.server.chat_view_spec import SpecError
from repowise.server.chat_view_spec import label as _label
from repowise.server.schemas.evidence import MAX_WIRE_NUMBER
from repowise.server.schemas.views import (
    VIEW_LIMITS,
    VIEW_SCHEMA_VERSION,
    ViewPart,
    ViewPartList,
)

logger = logging.getLogger(__name__)

ATTRIBUTION_COVERAGE_FLOOR = 0.30

_PART = TypeAdapter(ViewPart)
_SHA = re.compile(r"^[0-9a-f]{7,64}$")
_PERSON = re.compile(
    r"(^|_)(authors?|owners?|ownership|committers?|emails?|users?|username|login|assignees?"
    r"|reviewers?|maintainers?|contributors?|person|people|knowledge_map)(_|$)"
)
_RISK_SCORE = re.compile(r"risk|impact_score|hotspot_score")
# Attribution figures are drawn only by views that check their coverage.
_ATTRIBUTION = re.compile(r"(^|_)(agents?|attested|provenance|attribution)(_|$)")
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
class ViewBuilder:
    """A pure function from an artifact and options to a part without provenance ids.

    The function returns ``kind``, ``title``, ``basis`` and the kind's fields;
    ``build_view`` adds ``schema_version``, ``id``, ``source``, ``stale`` and
    ``as_of``. A ``receipt`` dict, if returned, goes to the model's
    ``show_view`` result and never to the part. ``artifact_types`` None means
    any artifact.
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


def _guard(part: dict[str, Any], coverage_metric: str | None = None) -> None:
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
    if coverage_metric == "attribution":
        coverage = part.get("coverage")
        ratio = coverage.get("ratio") if isinstance(coverage, Mapping) else None
        if not isinstance(ratio, (int, float)) or ratio < ATTRIBUTION_COVERAGE_FLOOR:
            raise ViewError(
                "insufficient coverage for an attribution figure "
                f"(floor {ATTRIBUTION_COVERAGE_FLOOR})",
                code="insufficient_coverage",
                coverage=ratio,
            )


def _all_zero(part: Mapping[str, Any]) -> bool:
    """True when no series of a chart holds a value other than zero or empty."""
    data = part.get("data")
    rows = data.get("rows") if isinstance(data, Mapping) else None
    series = part.get("series")
    if not (isinstance(rows, list) and rows and isinstance(series, list) and series):
        # No rows is its own refusal; a malformed part is left to validation.
        return False
    if not all(isinstance(key, str) for key in series):
        return False
    return all(
        row.get(key) in (0, None) for row in rows if isinstance(row, Mapping) for key in series
    )


def build_view(
    artifact: ViewArtifact,
    view: str,
    options: Mapping[str, Any] | None = None,
    *,
    part_id: str,
) -> dict[str, Any]:
    """Build and validate one view part, or raise ``ViewError``.

    A builder's ``receipt`` comes back under ``"receipt"``; ``TurnViews`` pops it.
    """
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
    return finish_part(
        part,
        part_id=part_id,
        source={
            "artifact_id": artifact.artifact_id,
            "tool_name": artifact.tool_name,
            "args_hash": args_hash(artifact.arguments),
        },
        stale=bool(meta.get("stale_warning")),
        as_of=commit if isinstance(commit, str) else None,
        coverage_metric=spec.coverage_metric,
        what=f"view {view!r}",
    )


def finish_part(
    part: dict[str, Any],
    *,
    part_id: str,
    source: Mapping[str, str],
    stale: bool,
    as_of: str | None,
    coverage_metric: str | None = None,
    what: str = "the chart",
) -> dict[str, Any]:
    """Add provenance to a builder's part, apply the guards and validate it.

    A builder's ``receipt`` comes back under ``"receipt"``; ``TurnViews`` pops it.
    """
    receipt = part.pop("receipt", None)
    part.update(
        schema_version=VIEW_SCHEMA_VERSION,
        id=part_id,
        source=dict(source),
        stale=stale,
        as_of=as_of if isinstance(as_of, str) and _SHA.match(as_of) else None,
    )
    if part.get("kind") == "chart" and _all_zero(part):
        raise ViewError(
            "every value in this chart is zero, so there is nothing to draw; "
            "say so in a sentence instead",
            code="no_data",
        )
    _guard(part, coverage_metric)
    try:
        model = _PART.validate_python(part)
    except ValidationError as exc:
        raise ViewError(
            f"{what} could not be built from this result: {exc.errors()[0]['msg']}",
            code="invalid_view",
        ) from None
    built = _PART.dump_python(model, mode="json", exclude_unset=True)
    if receipt:
        built["receipt"] = receipt
    return built


class TurnViews:
    """The views of one chat turn and the tool results they may be built from.

    ``record`` stamps each tool result with its artifact id before the model
    sees it; ``unstamp`` gives the stored side the server's id and the tool's
    own data back. ``show`` is the ``show_view`` tool; it resolves its source
    in *artifacts*, the conversation's results indexed once for the turn.
    ``parts`` holds the validated views in order.
    """

    def __init__(self, artifacts: ArtifactIndex | None = None) -> None:
        self.artifacts = artifacts if artifacts is not None else ArtifactIndex()
        # The exact stamped dicts handed out and the data each came from, so a
        # tool's own ``artifact_id`` field is never mistaken for a stamp.
        self._stamped: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
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
        self.artifacts.add(
            ViewArtifact(
                artifact_id=artifact_id,
                tool_name=tool_name,
                artifact_type=artifact_type,
                evidence_basis=evidence_basis,
                arguments=dict(arguments),
                data=result,
            )
        )
        stamped = stamp(result, artifact_id)
        self._stamped[artifact_id] = (stamped, result)
        return stamped

    def unstamp(self, result: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        """The server's id and unstamped data for a ``record`` result; else ``(None, result)``."""
        artifact_id = result.get(ARTIFACT_ID_FIELD)
        handed = self._stamped.get(artifact_id) if isinstance(artifact_id, str) else None
        if handed is not None and handed[0] is result:
            return artifact_id, handed[1]
        return None, result

    def show(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Run ``show_view``: a short receipt on success, an error payload otherwise."""
        try:
            part, extra = self._build(arguments)
        except ViewError as exc:
            return exc.payload()
        except Exception:
            # A builder bug (or a plugin's) is the model's error to see, not the turn's end.
            logger.exception("chat_view_builder_failed", extra={"view": arguments.get("view")})
            return ViewError("the view builder failed", code="builder_failed").payload()
        self.parts.append(part)
        receipt = part_receipt(part)
        reference = arguments.get("artifact_id")
        if isinstance(reference, str) and reference.startswith(LATEST_PREFIX):
            # Say which result a symbolic reference picked.
            receipt["resolved_artifact_id"] = part["source"]["artifact_id"]
            receipt["source_tool"] = part["source"]["tool_name"]
        return {**receipt, **extra}

    def next_id(self) -> str:
        return f"v{len(self.parts) + 1}"

    def keep(self, part: dict[str, Any]) -> None:
        """Add a finished part to the turn, within the turn's view cap."""
        self._check_room(part)
        self.parts.append(part)

    def _check_room(self, part: dict[str, Any]) -> None:
        try:
            ViewPartList.model_validate([*self.parts, part])
        except ValidationError:
            raise ViewError(
                f"a turn holds at most {VIEW_LIMITS['parts']} views", code="view_limit"
            ) from None

    def _build(self, arguments: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        reference = arguments.get("artifact_id")
        view = arguments.get("view")
        options = arguments.get("options") or {}
        artifact = self.artifacts.resolve(reference)
        if artifact is None and isinstance(reference, str) and re.fullmatch(r"v\d+", reference):
            shown = next((p for p in reversed(self.parts) if p["id"] == reference), None)
            shown = shown or self.artifacts.views.get(reference)
            if shown is None:
                raise ViewError(
                    f"no view {reference!r} in this conversation; call chart to draw one",
                    code="unknown_artifact",
                )
            part = redraw(shown, options, part_id=self.next_id())
            self._check_room(part)
            return part, {"redrawn_from": reference}
        if artifact is None:
            raise ViewError(
                f"no tool result matches artifact_id {reference!r}. Pass an artifact_id "
                f"from `available` or {LATEST_PREFIX}<tool_name>; if the result you need "
                "is not listed, call its tool first, or draw repository data with chart",
                code="unknown_artifact",
                available=self.artifacts.available(),
            )
        if not isinstance(view, str) or not isinstance(options, Mapping):
            raise ViewError("view must be a string and options an object", code="bad_arguments")
        part = build_view(artifact, view, options, part_id=self.next_id())
        extra = part.pop("receipt", None) or {}
        self._check_room(part)
        return part, extra

    def event(self, tool_id: str, result: Mapping[str, Any]) -> dict[str, Any] | None:
        """The SSE ``view`` payload for a successful ``show_view`` result."""
        view_id = result.get("view_id")
        part = next((p for p in self.parts if p["id"] == view_id), None)
        return None if part is None else {"type": "view", "tool_id": tool_id, "view": part}


def redraw(part: Mapping[str, Any], options: Any, *, part_id: str) -> dict[str, Any]:
    """An earlier chart drawn again as ``options.kind``: another chart type, or its table.

    Its rows and provenance are the earlier part's; only the form changes, and
    the part is validated again, so a kind the rows cannot take is refused.
    """
    asked = options.get("kind") if isinstance(options, Mapping) else None
    if asked is None:
        raise ViewError("pass options.kind: the kind to draw this view as", code="bad_arguments")
    try:
        kind, _ = chat_view_spec.read_kind(asked)
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


def part_receipt(part: Mapping[str, Any]) -> dict[str, Any]:
    """What the model reads back about a part it made: its id, kind, title and summary."""
    receipt = {"view_id": part["id"], "kind": part["kind"], "title": part["title"]}
    if part.get("kind") == "chart":
        receipt["chart_type"] = part["chart_type"]
    if "summary" in part:
        receipt["summary"] = part["summary"]
    return receipt


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
                "Show a table or chart of a tool result already in this conversation "
                "(for repository data prefer the chart tool, which needs no prior call). "
                "Pass its artifact_id, or latest:<tool_name>, or an earlier view's id (v1) "
                "with options.kind to redraw it. Views: " + ", ".join(names) + "; "
                "named views take options.kind (e.g. donut, bar, table). `table` takes "
                "options path (a list or map in the result), columns, limit. `chart` plots "
                "any list or map in the result with the chart tool's spec (rows: the path, "
                "x, y, series, filter, aggregate, sort, top, other, kind, title). Quote "
                "numbers from the receipt; on an error resend its suggested_spec."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "A tool result's artifact_id, or latest:<tool_name>.",
                    },
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
        return _basis("unresolved", artifact.tool_name)
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
) -> tuple[chat_view_spec.ChartSpec, chat_view_spec.ChartRows]:
    spec = chat_view_spec.parse_spec(options)
    _check_names(spec.fields(), source.path)
    types = {
        k: t for k, t in chat_view_spec.field_types(source.rows).items() if not _PERSON.search(k)
    }
    spec = chat_view_spec.fit(spec, source.rows, types, source.meta, source.hierarchy)
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
    return spec, chat_view_spec.transform(spec, source.rows, types)


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
    fields = sorted(chat_view_spec.field_types(source.rows))
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


def _units(spec: chat_view_spec.ChartSpec, meta: Mapping[str, Any]) -> str | None:
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
        summary += f" {chat_view_spec.OTHER_LABEL} adds up the rest."
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
    title = spec.title or chat_view_spec.default_title(spec)
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


@register_view_builder(
    "chart",
    description="A chart you specify over any list or map in a result.",
    options=chat_view_spec.OPTIONS,
)
def chart_view(artifact: ViewArtifact, options: Mapping[str, Any]) -> dict[str, Any]:
    path, rows, source_total = _rows_at(artifact.data, options.get("rows"), "rows")
    source = ChartSource(
        rows=rows,
        what=f"rows of {path}",
        basis=_tool_basis(artifact),
        total=source_total,
        path=tuple(path.split(".")),
    )
    return chart_part(source, options)


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
        kind, note = chat_view_spec.read_kind(asked)
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
    return _chart(
        title="Bug fixes on top hotspots",
        summary=f"Bug-fix commits on {len(rows)} hotspot files.",
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
