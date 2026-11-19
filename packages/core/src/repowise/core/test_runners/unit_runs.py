"""Which build units' ecosystems test selection may trust, and why.

:mod:`repowise.core.analysis.unit_scope` runs the tests of a changed file's
build unit and of every unit depending on it, when nothing finer knows the
file's tests. That is only safe where the build tool refuses an in-repo
dependency the manifest does not declare, so a unit's declared dependents are
all of them. :data:`UNIT_RUNS` names, per ``BuildUnit.ecosystem``, whether
that holds; an ecosystem missing from it (``manifest``, ``pascal``, Cargo until
its runner can run the tests that live in source files) is never trusted.

This module is the one place to add an ecosystem; see "Adding a unit runner"
in ``docs/architecture/build-units.md``. It reads no file, so the selection
path can import it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnitRun:
    """How an ecosystem's units relate to their tests.

    *deps_enforced* is true when the build fails on an in-repo dependency its
    manifest does not declare; *why* says what the build does, as a selection
    reason reads it. *languages* are the language registry tags whose package
    manifests the ecosystem reads, so a ``manifest:`` unit holding one (a
    manifest its adapter could not read) is known to be of this ecosystem.
    A tracked file named in *voided_by* lifts the enforcement repo-wide.
    """

    deps_enforced: bool
    why: str
    languages: tuple[str, ...]
    voided_by: tuple[str, ...] = ()


UNIT_RUNS: dict[str, UnitRun] = {
    # ``go build`` refuses an import of a module no ``require`` names
    # ("no required module provides package"); local ``replace`` targets are
    # deps too (#5865). A ``go.work`` makes every module it lists a main
    # module, importable by the others with no ``require``.
    "go": UnitRun(True, "Go builds only modules a go.mod requires", ("go",), ("go.work",)),
    # MSBuild compiles a project against its ``ProjectReference`` closure only.
    "dotnet": UnitRun(True, ".NET compiles a project against its references only", ("csharp",)),
    # Maven puts only declared dependencies on a module's classpath.
    "maven": UnitRun(
        True, "Maven compiles a module against its declared dependencies only", ("java", "kotlin")
    ),
    # Listed untrusted so the reason is named. Ceiling for each: measure, then flip.
    "npm": UnitRun(
        False,
        "npm and yarn hoist packages, so a package can load a sibling it does not declare",
        ("javascript", "typescript"),
    ),
    "gradle": UnitRun(
        False,
        "Gradle dependencies added by type-safe accessors or convention plugins are not read",
        ("java", "kotlin"),
    ),
    "python": UnitRun(
        False, "one Python environment imports any installed package, declared or not", ("python",)
    ),
}


__all__ = ["UNIT_RUNS", "UnitRun"]
