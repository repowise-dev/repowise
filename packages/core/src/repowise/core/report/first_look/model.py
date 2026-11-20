"""The first-look report: its items, sections and stamp, and the section seam.

The report is one versioned JSON document (:class:`FirstLookReport`), the
canonical form every renderer reads. Each item names where it is
(:class:`Location`), how it is known (``basis``), how urgent it is
(``severity``, the action tier vocabulary), an app route key (``link``, never a
URL) and the agent prompt that fixes it (``prompt``, rendered on demand by
:mod:`.prompts`). An item holds facts, never source text: a secret is a path,
a line and a kind.

Sections are providers on ``SeamRegistry("first-look-section")``, the same
shape as ``report_sections``: a plugin adds one by registering a
:class:`FirstLookSection` whose ``collect`` returns :class:`Collected`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from repowise.core.analysis.actions.model import DETAILS_CAP
from repowise.core.registry.plugins import SeamRegistry

#: Bumped when the document's shape changes; a reader refuses a newer one.
SCHEMA_VERSION = 1

#: Fixed section order (the order a person reads the report in).
SECTIONS: tuple[str, ...] = (
    "security",
    "performance",
    "health",
    "tests",
    "dead_code",
    "docs",
    "decisions",
    "hygiene",
    "ownership",
)

#: The analysis layer each section reads.
SOURCE_LAYERS: dict[str, str] = {
    "security": "security",
    "performance": "performance",
    "health": "code_health",
    "tests": "test_intelligence",
    "dead_code": "dead_code",
    "docs": "doc_drift",
    "decisions": "decisions",
    "hygiene": "graph",
    "ownership": "ownership",
}

#: How an item is known, best first (``report.model.BASES`` vocabulary).
BASES: tuple[str, ...] = ("measured", "inferred", "declared")
#: The action tier vocabulary, as the report spells it.
SEVERITIES: tuple[str, ...] = ("act_now", "plan", "improve")
CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")
EFFORTS: tuple[str, ...] = ("S", "M", "L")

#: App route keys an item links to. Each shell maps them to its own URLs:
#: ``file`` (params ``path``, ``line``), ``security``, ``dead_code``,
#: ``health_file`` (``path``), ``docs_drift``, ``decision`` (``id``), ``ownership``.
ROUTE_KEYS: tuple[str, ...] = (
    "file",
    "security",
    "dead_code",
    "health_file",
    "docs_drift",
    "decision",
    "ownership",
)

#: Renderers an item's prompt resolves through (:func:`.prompts.render_item_prompt`).
PROMPT_RENDERERS: tuple[str, ...] = ("action", "fix_item", "perf_opportunity", "first_look_item")

#: The stores an item's members live in; :mod:`.marks` reads and writes each
#: through its existing writer. ``action``: a next action's id (``action_states``);
#: ``dead_code``: ``[kind, symbol, path]`` as JSON, the identity a dismissal
#: survives re-index on; ``health_finding``: a health finding's public id;
#: ``performance``: a performance cause's id (its health findings);
#: ``refactoring_opportunity`` and ``refactoring_plan``: their public ids.
MEMBER_STORES: tuple[str, ...] = (
    "action",
    "dead_code",
    "health_finding",
    "performance",
    "refactoring_opportunity",
    "refactoring_plan",
)

#: Items one section shows on a page before "N more in the app". The JSON keeps
#: every item, so staleness compares whole sets.
SECTION_CAP = 12
#: Locations one item carries; the same cap an action's evidence has.
LOCATIONS_CAP = DETAILS_CAP


def source_layer(section: str) -> str:
    """The layer *section* reads; a plugin section is its own layer."""
    return SOURCE_LAYERS.get(section, section)


def item_id(section: str, rule: str, path: str, symbol: str | None = None) -> str:
    """``fl_`` + 12 hex of ``section:rule:path:symbol``: the ``action_id`` scheme.

    No line goes in, so an item keeps its id when its code moves.
    """
    key = f"{section}:{rule}:{path}:{symbol or ''}"
    return "fl_" + hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class Location:
    path: str
    line: int | None = None
    symbol: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line, "symbol": self.symbol}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Location:
        return cls(data["path"], data.get("line"), data.get("symbol"))


@dataclass(frozen=True, slots=True)
class Link:
    """An app route key and its parameters, never a URL."""

    route: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.route not in ROUTE_KEYS:
            raise ValueError(f"unknown route {self.route!r}; expected one of {ROUTE_KEYS}")

    def to_json(self) -> dict[str, Any]:
        return {"route": self.route, "params": dict(self.params)}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Link:
        return cls(data["route"], dict(data.get("params") or {}))


def file_link(path: str, line: int | None = None) -> Link:
    return Link("file", {"path": path, **({"line": line} if line else {})})


@dataclass(frozen=True, slots=True)
class PromptRef:
    """Which renderer writes the item's agent prompt, and the id it reads."""

    renderer: str
    payload_ref: str | None = None

    def __post_init__(self) -> None:
        if self.renderer not in PROMPT_RENDERERS:
            raise ValueError(f"unknown renderer {self.renderer!r}; expected one of {PROMPT_RENDERERS}")

    def to_json(self) -> dict[str, Any]:
        return {"renderer": self.renderer, "payload_ref": self.payload_ref}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> PromptRef:
        return cls(data["renderer"], data.get("payload_ref"))


@dataclass(frozen=True, slots=True)
class Member:
    """One stored row (or next action) an item stands for, by a stable id.

    ``fingerprint`` is an action's own, which a person's answer holds against.
    """

    store: str
    ref: str
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if self.store not in MEMBER_STORES:
            raise ValueError(f"unknown store {self.store!r}; expected one of {MEMBER_STORES}")

    def to_json(self) -> dict[str, Any]:
        return {"store": self.store, "ref": self.ref, "fingerprint": self.fingerprint}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Member:
        return cls(data["store"], data["ref"], data.get("fingerprint") or "")


def dead_code_ref(kind: str, symbol: str | None, path: str) -> str:
    """A dead-code finding's identity as a :class:`Member` ref (open rows get new
    ids on every index; this triple does not)."""
    return json.dumps([kind, symbol or "", path], separators=(",", ":"))


def parse_dead_code_ref(ref: str) -> tuple[str, str, str]:
    """``(kind, symbol, path)`` from :func:`dead_code_ref`; ``symbol`` is ``""`` for none."""
    kind, symbol, path = json.loads(ref)
    return kind, symbol, path


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    section: str
    #: The source rule or finding kind (``hardcoded_password``, ``perf_fix``, ...).
    rule: str
    #: What the template and a model write from: counts, names, metric values.
    #: Never source text.
    title_facts: Mapping[str, Any]
    locations: tuple[Location, ...]
    confidence: str
    basis: str
    severity: str
    effort: str
    link: Link
    prompt: PromptRef
    #: Changes when the facts behind the item change; staleness compares it.
    fingerprint: str
    #: The stored origin of the item's file; ``None`` for a non-file item.
    origin: str | None = None
    #: The confidence as a number where the source has one (dead code).
    confidence_score: float | None = None
    #: ``shared_priority`` (value x confidence / effort): orders a section.
    priority: float = 0.0
    #: Rule-local order (lines a deletion removes), comparable only within a rule.
    weight: float = 0.0
    #: Every location the source had; ``locations`` keeps :data:`LOCATIONS_CAP`.
    locations_total: int = 0
    #: Every rule a grouped item merged (:func:`.rank.group_items`), primary
    #: first; empty reads as ``(rule,)``.
    rules: tuple[str, ...] = ()
    #: The stored rows the item stands for; empty for an item no store holds
    #: (its answer is kept under the item's own id, :mod:`.marks`).
    members: tuple[Member, ...] = ()

    def __post_init__(self) -> None:
        for name, value, allowed in (
            ("basis", self.basis, BASES),
            ("severity", self.severity, SEVERITIES),
            ("confidence", self.confidence, CONFIDENCES),
            ("effort", self.effort, EFFORTS),
        ):
            if value not in allowed:
                raise ValueError(f"unknown {name} {value!r}; expected one of {allowed}")

    @property
    def source_layer(self) -> str:
        return source_layer(self.section)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "section": self.section,
            "rule": self.rule,
            "rules": list(self.rules or (self.rule,)),
            "title_facts": dict(self.title_facts),
            "locations": [loc.to_json() for loc in self.locations],
            "locations_total": max(self.locations_total, len(self.locations)),
            "confidence": self.confidence,
            "confidence_score": self.confidence_score,
            "basis": self.basis,
            "source_layer": self.source_layer,
            "origin": self.origin,
            "severity": self.severity,
            "effort": self.effort,
            "priority": round(self.priority, 4),
            "weight": self.weight,
            "link": self.link.to_json(),
            "prompt": self.prompt.to_json(),
            "fingerprint": self.fingerprint,
            "members": [m.to_json() for m in self.members],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Item:
        return cls(
            id=data["id"],
            section=data["section"],
            rule=data["rule"],
            title_facts=dict(data.get("title_facts") or {}),
            locations=tuple(Location.from_json(x) for x in data.get("locations") or ()),
            confidence=data["confidence"],
            basis=data["basis"],
            severity=data["severity"],
            effort=data["effort"],
            link=Link.from_json(data["link"]),
            prompt=PromptRef.from_json(data["prompt"]),
            fingerprint=data.get("fingerprint") or "",
            origin=data.get("origin"),
            confidence_score=data.get("confidence_score"),
            priority=float(data.get("priority") or 0.0),
            weight=float(data.get("weight") or 0.0),
            locations_total=int(data.get("locations_total") or 0),
            rules=tuple(data.get("rules") or ()),
            members=tuple(Member.from_json(m) for m in data.get("members") or ()),
        )


def locations(found: Sequence[Location]) -> tuple[tuple[Location, ...], int]:
    """*found* capped at :data:`LOCATIONS_CAP`, and how many there were."""
    return tuple(found[:LOCATIONS_CAP]), len(found)


@dataclass(frozen=True, slots=True)
class Collected:
    """What a section's ``collect`` returns: its items and the counts its intro
    is written from (``{"accepted": 4}``, ``{"lower_confidence": 1811}``)."""

    items: tuple[Item, ...] = ()
    facts: Mapping[str, Any] = field(default_factory=dict)
    #: Keep the section with no items: its facts are the content (coverage).
    stands_alone: bool = False


@dataclass(frozen=True)
class FirstLookSection:
    """A report section; ``collect(ctx)`` gets a :class:`.collect.FirstLookContext`."""

    #: One of :data:`SECTIONS`, or a plugin's own key (listed after them).
    key: str
    collect: Callable[[Any], Awaitable[Collected]]


first_look_sections: SeamRegistry[FirstLookSection] = SeamRegistry("first-look-section")


@dataclass(frozen=True, slots=True)
class SectionBlock:
    key: str
    #: Every item, ranked (:mod:`.rank`). A page shows :data:`SECTION_CAP`.
    items: tuple[Item, ...]
    facts: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "source_layer": source_layer(self.key),
            "items": [i.to_json() for i in self.items],
            "facts": dict(self.facts),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> SectionBlock:
        return cls(
            data["key"],
            tuple(Item.from_json(i) for i in data.get("items") or ()),
            dict(data.get("facts") or {}),
        )


@dataclass(frozen=True, slots=True)
class SetupStep:
    """One "Turn on more" step: an ``improve_signal`` action ``repowise next`` lists."""

    action_id: str
    rule: str
    title: str
    command: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "rule": self.rule,
            "title": self.title,
            "command": self.command,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> SetupStep:
        return cls(data["action_id"], data["rule"], data["title"], data.get("command"))


@dataclass(frozen=True, slots=True)
class Stamp:
    """When and how the report was written."""

    commit: str | None
    indexed_at: str | None
    created_at: str
    #: ``template``, or the model id that wrote the prose.
    generator: str = "template"
    #: What the prose cost in USD; ``None`` for a template.
    cost: float | None = None
    schema_version: int = SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "commit": self.commit,
            "indexed_at": self.indexed_at,
            "created_at": self.created_at,
            "generator": self.generator,
            "cost": self.cost,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Stamp:
        return cls(
            data.get("commit"),
            data.get("indexed_at"),
            data["created_at"],
            data.get("generator") or "template",
            data.get("cost"),
            int(data.get("schema_version") or SCHEMA_VERSION),
        )


@dataclass(frozen=True, slots=True)
class FirstLookReport:
    stamp: Stamp
    #: ``name``, ``files``, ``health`` (``score``, ``band``) and ``headline_facts``.
    repo: Mapping[str, Any]
    #: Non-empty sections in :data:`SECTIONS` order (security always present).
    sections: tuple[SectionBlock, ...]
    #: "Do these first": at most three item ids (:func:`.rank.top_items`).
    top: tuple[str, ...] = ()
    setup: tuple[SetupStep, ...] = ()
    #: Section key -> why it could not be collected.
    unavailable: Mapping[str, str] = field(default_factory=dict)
    #: Prose a model wrote, by item id and section key; empty for a template.
    prose: Mapping[str, Any] = field(default_factory=dict)

    def items(self) -> list[Item]:
        return [i for s in self.sections for i in s.items]

    def item(self, item_id_: str) -> Item | None:
        return next((i for i in self.items() if i.id == item_id_), None)

    def section(self, key: str) -> SectionBlock | None:
        return next((s for s in self.sections if s.key == key), None)

    def prose_source(self) -> dict[str, Any]:
        """Who wrote the wording, when and what the model call cost: ``writer``
        is ``template`` or the model id; ``written_at`` is ``None`` for the
        template, which is written fresh on every read; ``cost_usd`` is ``None``
        when no model was called (a template that kept a failed call's spend
        carries it)."""
        model = self.stamp.generator != "template"
        return {
            "writer": self.stamp.generator,
            "written_at": self.stamp.created_at if model else None,
            "cost_usd": self.stamp.cost,
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.stamp.schema_version,
            "stamp": self.stamp.to_json(),
            "prose_source": self.prose_source(),
            "repo": dict(self.repo),
            "top": list(self.top),
            "sections": [s.to_json() for s in self.sections],
            "setup": [s.to_json() for s in self.setup],
            "unavailable": dict(self.unavailable),
            "prose": dict(self.prose),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> FirstLookReport:
        version = int(data.get("schema_version") or 0)
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"Repo report schema {version} is newer than this Repowise reads "
                f"({SCHEMA_VERSION}); upgrade Repowise or rebuild the report"
            )
        return cls(
            stamp=Stamp.from_json(data["stamp"]),
            repo=dict(data.get("repo") or {}),
            sections=tuple(SectionBlock.from_json(s) for s in data.get("sections") or ()),
            top=tuple(data.get("top") or ()),
            setup=tuple(SetupStep.from_json(s) for s in data.get("setup") or ()),
            unavailable=dict(data.get("unavailable") or {}),
            prose=dict(data.get("prose") or {}),
        )


__all__ = [
    "BASES",
    "CONFIDENCES",
    "EFFORTS",
    "LOCATIONS_CAP",
    "MEMBER_STORES",
    "PROMPT_RENDERERS",
    "ROUTE_KEYS",
    "SCHEMA_VERSION",
    "SECTIONS",
    "SECTION_CAP",
    "SEVERITIES",
    "SOURCE_LAYERS",
    "Collected",
    "FirstLookReport",
    "FirstLookSection",
    "Item",
    "Link",
    "Location",
    "Member",
    "PromptRef",
    "SectionBlock",
    "SetupStep",
    "Stamp",
    "dead_code_ref",
    "file_link",
    "first_look_sections",
    "item_id",
    "locations",
    "parse_dead_code_ref",
    "source_layer",
]
