"""The build-unit tier: a changed file's unit and its dependents, when nothing finer knows its tests.

:func:`~.test_selection.select_tests` runs every test for a changed code file no
coverage, graph route or paired name links to a test, and for a manifest or
build file. Where the repository's build units are known, :func:`unit_scopes`
narrows two of those cases:

- a code file runs the tests its build units own, plus those of every unit
  depending on them, transitively;
- a unit's own manifest (a ``.csproj``, a module ``pom.xml``) runs the same for
  that unit, when no other unit sits below its directory (a parent POM or a
  root project configures the units under it, which no edge records).

The graph found no route to such a file, so a dependent's import would have
been an edge already; what the units add is reach the graph cannot see (a
same-package link on the JVM or .NET, a string-loaded type). Declared
dependents cover it only where the build refuses an undeclared in-repo
dependency, which :data:`~repowise.core.test_runners.unit_runs.UNIT_RUNS` says
per ecosystem. A file the tier does not apply to (no units are stored, none
owns it, it is neither code nor a unit's own manifest) gets ``None`` and keeps
its full run as it was. One whose units cannot vouch keeps it too, with a
string saying why:

- the repository-root unit owns it (a root's tests and harnesses reach every
  member without an edge);
- an owner's ecosystem is not trusted, its reader failed when the index was
  built, a tracked file lifts its enforcement (``go.work``), or a shipped
  manifest of its language fell back to a ``manifest:`` unit, whose deps, and
  so whether it depends on the owner, are unknown;
- the chosen units own no known test, or one of them holds test code (by
  ``code_origin``) but no known test, so its tests cannot be listed.

Ceiling: a dependency across ecosystems (a wrapper over a native library, a
tool run in a child process) is no edge; the graph, ``tests.always_run`` and a
full run on the default branch cover it.
"""

from __future__ import annotations

import fnmatch
import functools
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..build_units.model import BuildUnit, BuildUnitIndex
from ..ingestion.languages.registry import REGISTRY as _LANG_REGISTRY
from ..test_runners import is_code_file
from ..test_runners.unit_runs import UNIT_RUNS, UnitRun

#: How :attr:`~.test_selection.Selection.why` and ``basis`` name the tier.
UNIT_VIA = "build-unit"


@dataclass(frozen=True)
class UnitScope:
    """The tests a changed file's build units stand for.

    *owners* are the units owning the file (or declared by it), *units* those
    and every unit depending on them, *tests* the known tests the units own.
    """

    owners: tuple[str, ...]
    units: tuple[str, ...]
    tests: tuple[str, ...]
    why: str


def unit_scopes(
    paths: Iterable[str],
    index: BuildUnitIndex | None,
    known_tests: Collection[str],
    tracked: Collection[str] = (),
) -> dict[str, UnitScope | str | None]:
    """Each of *paths* mapped to its :class:`UnitScope`, to why its units cannot
    vouch, or to ``None`` when the tier does not apply to it.

    *index* is the stored build-unit index (``None`` or empty when unknown),
    *known_tests* the checkout's runnable tests, *tracked* its files.
    """
    paths = list(paths)
    if index is None or not index.units:
        return dict.fromkeys(paths)
    units = _Units(index, tuple(known_tests), frozenset(PurePosixPath(p).name for p in tracked))
    return {path: units.scope(path) for path in paths}


class _Units:
    """One selection's view of the index, each derived map worked out once."""

    def __init__(
        self, index: BuildUnitIndex, known: tuple[str, ...], names: frozenset[str]
    ) -> None:
        self.index = index
        self.known = known
        self.names = names
        self.refusals: dict[str, str | None] = {}
        self.by_manifest: dict[str, list[BuildUnit]] = {}
        for unit in index.units.values():
            self.by_manifest.setdefault(unit.manifest, []).append(unit)

    @functools.cached_property
    def test_owners(self) -> dict[str, frozenset[str]]:
        return {t: frozenset(u.id for u in self.index.units_owning(t)) for t in self.known}

    def scope(self, path: str) -> UnitScope | str | None:
        owners = self._owners(path)
        if owners is None or isinstance(owners, str):
            return owners
        for unit in owners:
            if unit.ecosystem not in self.refusals:
                self.refusals[unit.ecosystem] = self._refusal(unit.ecosystem)
            if refusal := self.refusals[unit.ecosystem]:
                return f"{unit.id}: {refusal}"
        ids = tuple(u.id for u in owners)
        units = self._closure(ids)
        tests = tuple(t for t in self.known if self.test_owners[t] & units)
        named = " and ".join(ids)
        if unknown := self._unknown_tests(units, tests):
            return unknown
        lead = (
            f"it declares {named}" if path in self.by_manifest else "no test is known to reach it"
        )
        why = f"{lead}; the tests of {named} and of {len(units) - len(ids)} unit(s) depending on it run"
        return UnitScope(ids, tuple(sorted(units)), tests, why)

    @functools.cached_property
    def test_code_units(self) -> frozenset[str]:
        """Units owning test code (``code_origin`` test), runnable or not."""
        from ..code_origin import path_origin

        return frozenset(
            uid
            for path, ids in self.index.file_owners.items()
            if is_code_file(path) and path_origin(path) == "test"
            for uid in ids
        )

    def _unknown_tests(self, units: frozenset[str], tests: tuple[str, ...]) -> str | None:
        """Why *units*' tests are not all among *tests*, or ``None``.

        A unit holding test code of which no file is a known test (a Reqnroll or
        Cucumber project whose steps no test-shaped name marks) has tests the
        selection cannot list, so leaving it out would drop them silently.
        """
        if not tests:
            return "the units owning it and depending on it own no known test"
        holding = frozenset().union(*(self.test_owners[t] for t in tests))
        silent = sorted((units & self.test_code_units) - holding)
        if silent:
            return f"{silent[0]} holds test code but no known test, so its tests cannot be listed"
        return None

    def _owners(self, path: str) -> tuple[BuildUnit, ...] | str | None:
        declared = self.by_manifest.get(path)
        if declared:
            owners = tuple(declared)
            below = [u.id for u in self.index.units.values() if _strictly_under(u.root, owners)]
            if below:
                return f"it configures {below[0]} below its directory, which no edge records"
        elif is_code_file(path):
            owners = self.index.units_owning(path)
        else:
            return None
        if not owners:
            return None
        if any(u.root == "" for u in owners):
            return (
                f"the repository-root unit {owners[0].id} owns it, and a root unit's tests "
                "reach its members without a declared edge"
            )
        return owners

    def _refusal(self, ecosystem: str) -> str | None:
        """Why *ecosystem*'s declared dependents are not all of them, or ``None``."""
        run = UNIT_RUNS.get(ecosystem)
        if run is None:
            return f"build units of {ecosystem} are not used for test selection"
        if ecosystem in self.index.failed_ecosystems:
            return (
                f"the {ecosystem} reader failed when the index was built, so its units may be stale"
            )
        if not run.deps_enforced:
            return run.why
        if voider := next((n for n in run.voided_by if n in self.names), None):
            return f"a tracked {voider} lets its units use one another undeclared"
        if fallback := self._fallback_of(run):
            return (
                f"{fallback.manifest} fell back to {fallback.id}, so whether it depends on "
                "this unit is unknown"
            )
        return None

    def _fallback_of(self, run: UnitRun) -> BuildUnit | None:
        """A shipped ``manifest:`` unit whose manifest is one *run*'s languages declare."""
        from ..test_runners.stacks import is_shipped_manifest

        patterns = _manifest_patterns(run.languages)
        return next(
            (
                u
                for u in self.index.units.values()
                if u.ecosystem == "manifest"
                and any(fnmatch.fnmatch(PurePosixPath(u.manifest).name, p) for p in patterns)
                and is_shipped_manifest(u.manifest)
            ),
            None,
        )

    def _closure(self, ids: tuple[str, ...]) -> frozenset[str]:
        """*ids* and every unit depending on one of them, transitively."""
        seen = set(ids)
        frontier = list(ids)
        while frontier:
            for unit in self.index.dependents(frontier.pop()):
                if unit.id not in seen:
                    seen.add(unit.id)
                    frontier.append(unit.id)
        return frozenset(seen)


def _strictly_under(root: str, owners: tuple[BuildUnit, ...]) -> bool:
    """Whether *root* lies below the directory of any of *owners*, not at it."""
    return any(root != o.root and (o.root == "" or root.startswith(f"{o.root}/")) for o in owners)


@functools.cache
def _manifest_patterns(languages: tuple[str, ...]) -> tuple[str, ...]:
    """The package manifest names and globs the registry declares for *languages*."""
    out: list[str] = []
    for tag in languages:
        spec = _LANG_REGISTRY.get(tag)
        if spec is not None:
            out += [f for f in spec.manifest_files if f not in spec.build_config_manifests]
            out += list(spec.manifest_globs)
    return tuple(out)


__all__ = ["UNIT_VIA", "UnitScope", "unit_scopes"]
