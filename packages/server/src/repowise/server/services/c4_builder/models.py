"""Plain dataclasses produced by the C4 builder.

These are framework-agnostic (no Pydantic, no SQLAlchemy) so the builder
can be unit-tested without spinning up a session, and so a future Mermaid
emitter can consume the same data structures.

The server routers wrap these into Pydantic response models (see
``server/schemas.py``); the conversion is mechanical.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    description: str = ""


@dataclass(frozen=True)
class System:
    """The 'system under design' — the indexed repo as a whole."""

    id: str
    name: str
    description: str = ""


@dataclass(frozen=True)
class ExternalSystemView:
    id: str            # stable id used in edges; e.g., "ext:react"
    name: str
    display_name: str
    category: str      # framework | service | tool | library
    ecosystem: str
    version: str | None = None


@dataclass(frozen=True)
class Container:
    """A deployable / runnable unit. Typically a workspace package, or a
    top-level directory in a non-monorepo. ``path`` is repo-relative.
    """

    id: str            # "pkg:packages/core"
    name: str
    path: str
    language: str
    file_count: int
    symbol_count: int
    hotspot_count: int = 0
    dead_count: int = 0


@dataclass(frozen=True)
class Component:
    """A sub-module inside a container — top-level child directory, or the
    synthetic ``_root`` group for files at the container root.
    """

    id: str            # "cmp:packages/core/ingestion"
    name: str
    path: str          # repo-relative
    container_id: str
    file_count: int
    symbol_count: int


@dataclass(frozen=True)
class Relation:
    """A typed edge between any two C4 boxes (container ↔ container,
    container ↔ external, component ↔ component, component ↔ external).
    """

    source_id: str
    target_id: str
    label: str = ""
    edge_count: int = 1
    edge_types: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class C4L1:
    system: System
    people: list[Person]
    external_systems: list[ExternalSystemView]
    relations: list[Relation]


@dataclass(frozen=True)
class C4L2:
    containers: list[Container]
    external_systems: list[ExternalSystemView]
    relations: list[Relation]


@dataclass(frozen=True)
class C4L3:
    container: Container
    components: list[Component]
    external_systems: list[ExternalSystemView]
    relations: list[Relation]
