"""The build-unit model: one unit per thing a build tool declares, and who owns each file."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from repowise.core.ingestion.languages.registry import REGISTRY

_CODE_LANGUAGES = REGISTRY.code_languages()


@dataclass(frozen=True)
class BuildUnit:
    """One unit a build declares: a .NET project, a crate, a workspace package.

    ``id`` is ecosystem-prefixed and stable across runs: ``dotnet:<manifest>``,
    ``cargo:<crate>``, ``npm:<package>``, ``gradle:<project path>``,
    ``go:<module path>``, ``maven:<groupId>:<artifactId>``,
    ``python:<PEP 503 name>``, ``dart:<package name>``, ``php:<composer name>`` /
    ``php:<dir>`` when unnamed, ``pascal:<project name>`` (``pascal:<manifest>``
    when several projects share the name), ``elixir:<app name>``,
    ``manifest:<root>`` (``manifest:.`` at the repo root).
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


def build_file_languages(unit: BuildUnit) -> frozenset[str]:
    """The languages *unit*'s build compiles when its manifest is build config, else empty.

    A unit declared in a build file (``CMakeLists.txt``, a Bazel ``BUILD``)
    is rooted at that file's directory, which is no package root: the
    language registry lists the file as build config, not as a package
    manifest. A unit declared in a package manifest gives the empty set.
    """
    return REGISTRY.build_config_languages(unit.manifest)


@dataclass(frozen=True)
class BuildUnitIndex:
    """Every unit of a repo by id, and the units owning each file.

    ``file_owners`` maps a repo-relative path to the ids of its units. A file
    an adapter lists (a .NET project's sources, a Gradle source set's files)
    is owned by the units listing it; any other file by the units whose root
    is the deepest one containing it. A unit declared in a build file
    (:func:`build_file_languages`) does not own, at its root, a file written
    in another code language: a Ruby file under a ``CMakeLists.txt`` goes on
    to the deepest root holding a unit that may own it. Its own build file
    and files in no code language (a README, ``*.cmake``) stay with it. A
    file may belong to several units.
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
    _scoped: dict[str, frozenset[str]] = field(init=False, repr=False, compare=False)

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
        # Unit id -> the languages its build compiles, for units declared in
        # a build file only.
        scoped = {uid: langs for uid, u in units.items() if (langs := build_file_languages(u))}
        object.__setattr__(self, "_scoped", scoped)

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
        """Ids of the units that may own *path* at the deepest root above it holding any."""
        parts = path.split("/")
        name = parts.pop()
        language: str | None = None
        for depth in range(len(parts), -1, -1):
            ids = self._by_root.get("/".join(parts[:depth]), ())
            if any(uid in self._scoped for uid in ids):
                if language is None:
                    ext = name[name.rfind(".") :].lower() if "." in name else ""
                    language = REGISTRY.from_filename(name) or REGISTRY.from_extension(ext)
                ids = tuple(uid for uid in ids if self._may_own(uid, path, language))
            if ids:
                return ids
        return ()

    def _may_own(self, uid: str, path: str, language: str) -> bool:
        """Whether unit *uid* may own unlisted *path*, written in *language*, at its root."""
        langs = self._scoped.get(uid)
        return (
            langs is None
            or language in langs
            or language not in _CODE_LANGUAGES
            or path == self.units[uid].manifest
        )
