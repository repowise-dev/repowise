"""Delphi and Lazarus projects and packages from the resolver's :class:`PascalProjectIndex`.

One unit per readable ``.dpr``, ``.dpk``, ``.lpr`` and ``.lpk``, rooted at its
directory, listing the files its ``uses`` and ``contains`` clauses reach. A unit
nothing reaches is owned through the deepest root above it. The id is
``pascal:<name>``. Names sharing a spelling, compared case-insensitively, are
common (``Project1.dpr`` in every demo folder, ``Tool.dpr`` beside
``Tool.lpr``), so each of those units is ``pascal:<manifest>`` instead.

The packages each project requires (see the resolver) link by name,
case-insensitively, to in-repo packages of its own toolchain: ``.dpr`` and
``.dpk`` to ``.dpk``, ``.lpr`` and ``.lpk`` to ``.lpk``. Among several packages
of one name the one in the requiring project's directory wins; without one,
every candidate is linked, because the build's library path picks among them.

An unreadable project is no unit, so its files go to the deepest other unit.

Ceiling: conditional defines and ``.dproj`` build configurations are not
evaluated, so a unit or package named under any of them counts. The compiler's
real ``-U`` paths are not known: a same-named file is chosen by the directories
the project already reaches, and when those do not single one out, every
candidate is listed. One project is one unit, so a project built for several
platforms lists each platform's files.
"""

from __future__ import annotations

import posixpath

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.pascal import (
    PascalProject,
    compiled_files,
    get_pascal_index,
)

# Without one of these there is nothing to own, and no reason to build the index.
_SOURCE_SUFFIXES = (".pas", ".pp", ".dpr", ".dpk", ".lpr")
# The package file each kind of project links against.
_PACKAGE_SUFFIX = {".dpr": ".dpk", ".dpk": ".dpk", ".lpr": ".lpk", ".lpk": ".lpk"}


def _suffix(project: PascalProject) -> str:
    return posixpath.splitext(project.path)[1].lower()


def _unit_ids(projects: list[PascalProject]) -> dict[str, str]:
    """Each project's unit id by path: its name, or its manifest when the name is shared."""
    by_name: dict[str, list[PascalProject]] = {}
    for project in projects:
        by_name.setdefault(project.name.lower(), []).append(project)
    return {
        project.path: f"pascal:{project.path if len(group) > 1 else project.name}"
        for group in by_name.values()
        for project in group
    }


def _required(
    project: PascalProject, name: str, packages: dict[str, list[PascalProject]]
) -> list[PascalProject]:
    """The packages *project* means by *name*."""
    kind = _PACKAGE_SUFFIX[_suffix(project)]
    same_kind = [p for p in packages.get(name.lower(), ()) if _suffix(p) == kind]
    base = posixpath.dirname(project.path)
    return [p for p in same_kind if posixpath.dirname(p.path) == base] or same_kind


def _packages_by_name(projects: list[PascalProject]) -> dict[str, list[PascalProject]]:
    packages: dict[str, list[PascalProject]] = {}
    for project in projects:
        if _suffix(project) in (".dpk", ".lpk"):
            packages.setdefault(project.name.lower(), []).append(project)
    return packages


def _unit(
    project: PascalProject, ids: dict[str, str], packages: dict[str, list[PascalProject]]
) -> BuildUnit:
    deps = {
        ids[package.path]
        for name in project.requires
        for package in _required(project, name, packages)
    }
    return BuildUnit(
        id=ids[project.path],
        ecosystem="pascal",
        name=project.name,
        root=posixpath.dirname(project.path),
        manifest=project.path,
        source="pascal",
        deps=tuple(sorted(deps)),
    )


def units(ctx: ResolverContext) -> Found:
    if not any(path.lower().endswith(_SOURCE_SUFFIXES) for path in ctx.path_set):
        return Found([])
    projects = [project for project in get_pascal_index(ctx).projects if project.readable]
    ids = _unit_ids(projects)
    packages = _packages_by_name(projects)
    compiled = compiled_files(ctx)
    return Found(
        [
            (_unit(project, ids, packages), tuple(sorted(compiled[project.path])))
            for project in projects
        ]
    )
