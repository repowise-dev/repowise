"""The build-unit model: one unit per thing a build tool declares, and who owns each file."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BuildUnit:
    """One unit a build declares: a .NET project, a crate, a workspace package.

    ``id`` is ecosystem-prefixed and stable across runs: ``dotnet:<manifest>``,
    ``cargo:<crate>``, ``npm:<package>``, ``gradle:<project path>``,
    ``go:<module path>``, ``maven:<groupId>:<artifactId>``,
    ``python:<PEP 503 name>``, ``dart:<package name>``, ``php:<composer name>`` /
    ``php:<dir>`` when unnamed, ``pascal:<project name>`` (``pascal:<manifest>``
    when several projects share the name), ``manifest:<root>`` (``manifest:.`` at
    the repo root).
    ``root`` and ``manifest`` are repo-relative POSIX paths, ``root`` is ``""``
    at the repo root. ``deps`` are the ids of the in-repo units this one
    depends on, sorted; third-party dependencies are not units. ``source``
    names the reader that produced the unit.
    """

    id: str
    ecosystem: str
    name: str
    root: str
    manifest: str
    source: str
    deps: tuple[str, ...] = ()


@dataclass(frozen=True)
class BuildUnitIndex:
    """Every unit of a repo by id, and the units owning each file.

    ``file_owners`` maps a repo-relative path to the ids of its units. A file
    an adapter lists (a .NET project's sources, a Gradle source set's files)
    is owned by the units listing it; any other file by the units whose root
    is the deepest one containing it. A file may belong to several units.
    Both maps are kept sorted, ids and paths alike, so two runs over the same
    tree compare equal and store the same rows.

    ``failed_ecosystems`` names the ecosystems whose reader failed during
    collection, so their units here are incomplete and a store should keep
    what it already holds for them. It describes one collection run, not the
    repo, so it is left out of equality: an index read back from storage
    compares equal to the one written.
    """

    units: Mapping[str, BuildUnit] = field(default_factory=dict)
    file_owners: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    failed_ecosystems: frozenset[str] = field(default=frozenset(), compare=False)
    _by_root: dict[str, tuple[str, ...]] = field(init=False, repr=False, compare=False)
    _dependents: dict[str, tuple[str, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        units = {uid: self.units[uid] for uid in sorted(self.units)}
        owners = {
            path: tuple(sorted(set(ids))) for path, ids in sorted(self.file_owners.items())
        }
        by_root: dict[str, list[str]] = {}
        dependents: dict[str, list[str]] = {}
        for uid, unit in units.items():
            by_root.setdefault(unit.root, []).append(uid)
            for dep in unit.deps:
                dependents.setdefault(dep, []).append(uid)
        object.__setattr__(self, "units", units)
        object.__setattr__(self, "file_owners", owners)
        object.__setattr__(self, "_by_root", {r: tuple(ids) for r, ids in by_root.items()})
        object.__setattr__(self, "_dependents", {d: tuple(ids) for d, ids in dependents.items()})

    @classmethod
    def from_units(
        cls,
        units: Iterable[BuildUnit],
        paths: Iterable[str],
        explicit: Mapping[str, Iterable[str]] | None = None,
        failed_ecosystems: frozenset[str] = frozenset(),
    ) -> BuildUnitIndex:
        """Index *units*, owning each of *paths* by *explicit* listing, else by deepest root."""
        index = cls(units={unit.id: unit for unit in units})
        explicit = explicit or {}
        owners: dict[str, tuple[str, ...]] = {}
        for path in paths:
            listed = tuple(explicit.get(path, ()))
            ids = listed or index._root_owners(path)
            if ids:
                owners[path] = ids
        return cls(units=index.units, file_owners=owners, failed_ecosystems=failed_ecosystems)

    def unit(self, unit_id: str) -> BuildUnit | None:
        """The unit with *unit_id*, or None."""
        return self.units.get(unit_id)

    def units_owning(self, path: str) -> tuple[BuildUnit, ...]:
        """The units owning *path*, sorted by id; by deepest root when the index never saw it."""
        ids = self.file_owners.get(path)
        if ids is None:
            ids = self._root_owners(path)
        return tuple(self.units[uid] for uid in ids if uid in self.units)

    def dependents(self, unit_id: str) -> tuple[BuildUnit, ...]:
        """The units that list *unit_id* in their ``deps``, sorted by id."""
        return tuple(self.units[uid] for uid in self._dependents.get(unit_id, ()))

    def _root_owners(self, path: str) -> tuple[str, ...]:
        """Ids of the units whose root is the deepest directory above *path*."""
        parts = path.split("/")[:-1]
        for depth in range(len(parts), -1, -1):
            ids = self._by_root.get("/".join(parts[:depth]))
            if ids:
                return ids
        return ()
