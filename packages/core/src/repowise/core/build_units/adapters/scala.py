"""Scala units from SBT projects, linked by ``dependsOn``.

One unit per subproject defined in ``build.sbt`` via
``project.in(file(...))``. Deps are the in-repo projects a unit
declares via ``dependsOn(...)`` arguments, extracted conservatively
by identifier name (the leading identifier before any ``%``
configuration mapping).

Ceiling: only ``project.in(file(...))`` definitions the regex
matches; ``aggregate`` calls, ``dependsOn`` with ``LocalProject("x")``,
``ProjectRef``, settings in ``project/*.scala`` files, cross-projects
and conditional configuration are not read; reading sbt's own model
export (e.g. ``sbt projects`` or ``inspect``) is the upgrade path.
Mill is not supported (no ``dependsOn`` equivalent parsed).
"""

from __future__ import annotations

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.scala_build import get_or_build_scala_index


def units(ctx: ResolverContext) -> Found:
    if ctx.repo_path is None:
        return Found([])
    if "build.sbt" not in ctx.path_set:
        return Found([])

    index = get_or_build_scala_index(ctx)

    if index.build_tool != "sbt":
        return Found([])

    projects = index.projects

    if not projects:
        return Found([])

    # Filter out the "<root>" placeholder (single-project build, or unreadable build.sbt)
    real_projects = {
        name: dir_posix
        for name, dir_posix in projects.items()
        if name != "<root>"
    }

    if not real_projects:
        return Found([])

    # Check if root has sources (for aggregate-only roots)
    root_has_sources = any(
        p.startswith("src/") and p.endswith((".scala", ".java"))
        for p in ctx.path_set
    )

    claims: list[tuple[BuildUnit, tuple[str, ...]]] = []

    for name, project_dir in real_projects.items():
        # Skip aggregate-only roots without sources
        if project_dir == "" and not root_has_sources:
            continue

        # Build deps: link to project names that are themselves subprojects
        dep_names = index.depends_on.get(name, ())
        deps = tuple(
            f"scala:{dep_name}"
            for dep_name in dep_names
            if dep_name in real_projects and dep_name != name
        )

        unit = BuildUnit(
            id=f"scala:{name}",
            ecosystem="scala",
            name=name,
            root=project_dir,
            manifest="build.sbt",
            source="sbt",
            deps=deps,
        )
        claims.append((unit, ()))

    # Rule out build.sbt only when a subproject is claimed, so a build with
    # none keeps its manifest:. unit.
    ruled_out = frozenset({"build.sbt"}) if claims else frozenset()

    return Found(claims, ruled_out)