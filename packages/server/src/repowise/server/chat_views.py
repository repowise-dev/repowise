"""Deterministic chat views: tables and charts built from a turn's tool results.

The model never writes a number into a view. It calls the chat-only
``show_view`` tool naming a tool result of the conversation (by the
``artifact_id`` stamped on it, or ``latest:<tool_name>``; see
:mod:`repowise.server.chat_references`) and a view; a registered builder makes the part
from that result, and ``build_view`` adds provenance, applies the guards and
validates it as a ``ViewPart``. Builders are pure functions registered on the
``chat-view`` seam, so a plugin adds one without editing this module.

The named views live in :mod:`repowise.server.chat_view_builders`; the
``chart`` view and the chat-only ``chart`` tool share
:mod:`repowise.server.chat_chart_part`.

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
from repowise.server import chat_spec
from repowise.server.chat_artifacts import ARTIFACT_ID_FIELD
from repowise.server.chat_references import (
    LATEST_PREFIX,
    SHOW_VIEW_TOOL,
    ArtifactIndex,
    ViewArtifact,
    stamp,
)
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
        for name, builder in view_builders.all()
        if (types := builder.artifact_types) is None or artifact_type in types
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
            # Deferred: chat_chart_part builds on this module.
            from repowise.server.chat_chart_part import redraw

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
                "any list or map in the result: options rows is its path. "
                + chat_spec.SPEC_HELP
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


# The named views register themselves on import; they build on this module, so last.
from repowise.server import chat_view_builders as _builders  # noqa: E402, F401
