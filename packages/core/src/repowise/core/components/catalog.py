"""Read declared components from Backstage-shaped catalog files.

Two files are read, in this order: ``.repowise/components.yaml`` and a
repo-root ``catalog-info.yaml``. Both are multi-document Backstage YAML
(``apiVersion: backstage.io/v1alpha1``), so a developer portal and Repowise
share one file. Component, System and Group entities are read; the other
Backstage kinds (API, Resource, Domain, User, Location, Template) are valid
and skipped. Repowise data lives only under ``repowise.dev/*`` annotations,
which keeps the files valid Backstage.

When both files declare the same kind and name, the first one read wins.
Group entities are ownership inputs only: their name, type and hierarchy are
kept, ``spec.members`` is never read.

Reading never raises: every problem becomes a :class:`CatalogIssue`, and an
entity that cannot be identified (no name, unknown kind, wrong apiVersion) is
left out of the catalog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

COMPONENTS_FILE = ".repowise/components.yaml"
CATALOG_INFO_FILE = "catalog-info.yaml"
#: Read in this order; on a duplicate kind and name the earlier file wins.
CATALOG_FILES: tuple[str, ...] = (COMPONENTS_FILE, CATALOG_INFO_FILE)

API_VERSION = "backstage.io/v1alpha1"
READ_KINDS = frozenset({"Component", "System", "Group"})
#: Backstage kinds that are valid in a shared catalog file but carry nothing
#: Repowise reads. Template is the one kind on ``backstage.io/v1beta2``.
SKIPPED_KINDS = frozenset({"API", "Resource", "Domain", "User", "Location", "Template"})

ANNOTATION_PREFIX = "repowise.dev/"
PATHS_ANNOTATION = "repowise.dev/paths"
CRITICALITY_ANNOTATION = "repowise.dev/criticality"
TAGS_ANNOTATION = "repowise.dev/tags"
CAPABILITY_ANNOTATION = "repowise.dev/capability"
KNOWN_ANNOTATIONS = frozenset(
    {PATHS_ANNOTATION, CRITICALITY_ANNOTATION, TAGS_ANNOTATION, CAPABILITY_ANNOTATION}
)
CRITICALITY_LEVELS: tuple[str, ...] = ("low", "medium", "high", "critical")

#: Backstage's own name rule: 1-63 characters of ``[a-zA-Z0-9]`` separated by
#: single ``-``, ``_`` or ``.``.
_NAME_RE = re.compile(r"^[a-zA-Z0-9]+(?:[-_.][a-zA-Z0-9]+)*$")
_NAME_MAX = 63
#: The spec fields Backstage requires per kind.
_REQUIRED_SPEC: dict[str, tuple[str, ...]] = {
    "Component": ("type", "lifecycle", "owner"),
    "System": ("owner",),
    "Group": ("type", "children"),
}


@dataclass(frozen=True)
class CatalogIssue:
    severity: str  # "error" | "warning"
    message: str
    source: str | None = None  # the catalog file, repo-relative
    entity: str | None = None  # "Kind:name"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"severity": self.severity, "message": self.message}
        if self.source:
            out["source"] = self.source
        if self.entity:
            out["entity"] = self.entity
        return out


@dataclass(frozen=True)
class Component:
    """A component, declared in a catalog file or derived from the index."""

    name: str
    title: str | None = None
    description: str | None = None
    type: str | None = None  # spec.type
    lifecycle: str | None = None
    owner: str | None = None
    system: str | None = None
    #: gitignore-style globs, relative to the repository root.
    paths: tuple[str, ...] = ()
    criticality: str | None = None
    tags: tuple[str, ...] = ()
    capability: str | None = None
    #: "declared", or how a derived one was found: "service", "module", "directory".
    source: str = "declared"
    #: The catalog file a declared component came from.
    declared_in: str | None = None
    #: Directory a derived component covers; files go to the deepest one.
    root: str | None = None

    @property
    def declared(self) -> bool:
        return self.source == "declared"


@dataclass(frozen=True)
class System:
    name: str
    title: str | None = None
    description: str | None = None
    owner: str | None = None
    declared_in: str | None = None


@dataclass(frozen=True)
class Group:
    """A declared owning team. Membership is never read."""

    name: str
    title: str | None = None
    type: str | None = None
    parent: str | None = None
    children: tuple[str, ...] = ()
    declared_in: str | None = None


@dataclass
class Catalog:
    components: list[Component] = field(default_factory=list)
    systems: list[System] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    issues: list[CatalogIssue] = field(default_factory=list)
    #: Catalog files found and read, repo-relative.
    sources: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[CatalogIssue]:
        return [i for i in self.issues if i.severity == "error"]


def read_catalog(repo_root: Path | str) -> Catalog:
    """Every catalog file under *repo_root*, merged in :data:`CATALOG_FILES` order."""
    catalog = Catalog()
    root = Path(repo_root)
    for rel in CATALOG_FILES:
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            catalog.issues.append(CatalogIssue("error", f"cannot read: {exc}", rel))
            continue
        catalog.sources.append(rel)
        parse_into(catalog, text, rel)
    return catalog


def parse_into(catalog: Catalog, text: str, source: str) -> None:
    """Add the entities of one multi-document YAML file to *catalog*."""
    try:
        documents = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError as exc:
        catalog.issues.append(CatalogIssue("error", f"invalid YAML: {exc}", source))
        return
    seen_here: set[str] = set()
    for doc in documents:
        _parse_entity(catalog, doc, source, seen_here)


def _parse_entity(catalog: Catalog, doc: Any, source: str, seen_here: set[str]) -> None:
    def issue(severity: str, message: str, entity: str | None = None) -> None:
        catalog.issues.append(CatalogIssue(severity, message, source, entity))

    if not isinstance(doc, dict):
        issue("error", "a document is not a mapping")
        return
    kind = doc.get("kind")
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    name = metadata.get("name")
    label = f"{kind}:{name}" if name else str(kind)
    if kind in SKIPPED_KINDS:
        return
    if kind not in READ_KINDS:
        issue("error", f"unknown kind {kind!r}; expected Component, System or Group", label)
        return
    if doc.get("apiVersion") != API_VERSION:
        issue("error", f"apiVersion must be {API_VERSION!r}, got {doc.get('apiVersion')!r}", label)
        return
    if not isinstance(name, str) or not name:
        issue("error", f"{kind} has no metadata.name")
        return
    if len(name) > _NAME_MAX or not _NAME_RE.match(name):
        issue("error", f"metadata.name {name!r} is not a valid Backstage name", label)
        return
    if label in seen_here:
        issue("error", "declared twice in this file", label)
        return
    seen_here.add(label)
    if _already_declared(catalog, kind, name):
        issue("warning", "also declared in an earlier catalog file, which wins", label)
        return

    spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
    for key in _REQUIRED_SPEC[kind]:
        if spec.get(key) in (None, ""):
            issue("error", f"spec.{key} is required for a {kind}", label)
    annotations = _annotations(metadata, label, issue)
    title = _text(metadata.get("title"))
    description = _text(metadata.get("description"))

    if kind == "System":
        catalog.systems.append(
            System(name, title, description, _text(spec.get("owner")), declared_in=source)
        )
        return
    if kind == "Group":
        children = spec.get("children") if isinstance(spec.get("children"), list) else []
        profile = spec.get("profile") if isinstance(spec.get("profile"), dict) else {}
        catalog.groups.append(
            Group(
                name,
                title or _text(profile.get("displayName")),
                _text(spec.get("type")),
                _text(spec.get("parent")),
                tuple(str(c) for c in children),
                declared_in=source,
            )
        )
        return

    criticality = annotations.get(CRITICALITY_ANNOTATION)
    if criticality is not None:
        criticality = criticality.strip().lower()
        if criticality not in CRITICALITY_LEVELS:
            issue(
                "error",
                f"{CRITICALITY_ANNOTATION} must be one of {', '.join(CRITICALITY_LEVELS)}",
                label,
            )
            criticality = None
    meta_tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
    tags = [str(t).strip().lower() for t in meta_tags]
    tags += _split(annotations.get(TAGS_ANNOTATION))
    catalog.components.append(
        Component(
            name=name,
            title=title,
            description=description,
            type=_text(spec.get("type")),
            lifecycle=_text(spec.get("lifecycle")),
            owner=_text(spec.get("owner")),
            system=_text(spec.get("system")),
            paths=tuple(_split(annotations.get(PATHS_ANNOTATION), lower=False)),
            criticality=criticality,
            tags=tuple(dict.fromkeys(t for t in tags if t)),
            capability=_text(annotations.get(CAPABILITY_ANNOTATION)),
            declared_in=source,
        )
    )


def _already_declared(catalog: Catalog, kind: str, name: str) -> bool:
    pool: list[Any] = {
        "Component": catalog.components,
        "System": catalog.systems,
        "Group": catalog.groups,
    }[kind]
    return any(e.name == name for e in pool)


def _annotations(metadata: dict, label: str, issue: Any) -> dict[str, str]:
    """``repowise.dev/*`` annotations; unknown keys and non-string values are errors."""
    raw = metadata.get("annotations")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not str(key).startswith(ANNOTATION_PREFIX):
            continue
        if key not in KNOWN_ANNOTATIONS:
            issue("error", f"unknown annotation {key!r}", label)
        elif not isinstance(value, str):
            issue("error", f"annotation {key!r} must be a string (Backstage rule)", label)
        else:
            out[key] = value
    return out


def _split(value: str | None, *, lower: bool = True) -> list[str]:
    """A comma- or newline-separated annotation value as a list."""
    if not value:
        return []
    parts = [p.strip() for p in re.split(r"[,\n]", value)]
    return [p.lower() if lower else p for p in parts if p]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def owner_group_name(owner: str | None) -> str | None:
    """The group name in a Backstage owner ref: ``group:default/payments`` is ``payments``."""
    if not owner:
        return None
    kind, sep, rest = owner.partition(":")
    if sep and kind.lower() != "group":
        return None
    ref = rest if sep else owner
    return ref.rsplit("/", 1)[-1] or None
