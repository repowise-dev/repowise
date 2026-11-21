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
from repowise.core.ingestion.resolvers.scala_build import (
    ScalaProjectIndex,
    get_or_build_scala_index,
)


def _sbt_projects(ctx: ResolverContext) -> tuple[ScalaProjectIndex, dict[str, str]] | None:
    """The sbt index and its subprojects (name -> dir), or None when there are none."""
    if ctx.repo_path is None or "build.sbt" not in ctx.path_set:
        return None
    index = get_or_build_scala_index(ctx)
    if index.build_tool != "sbt":
        return None
    # "<root>" is the placeholder for a single-project build or an unreadable build.sbt
    projects = {name: d for name, d in index.projects.items() if name != "<root>"}
    return (index, projects) if projects else None


def _unit(index: ScalaProjectIndex, projects: dict[str, str], name: str) -> BuildUnit:
    """The unit for subproject *name*, linked to the subprojects its ``dependsOn`` names."""
    deps = tuple(
        f"scala:{dep}" for dep in index.depends_on.get(name, ()) if dep in projects and dep != name
    )
    return BuildUnit(
        id=f"scala:{name}",
        ecosystem="scala",
        name=name,
        root=projects[name],
        manifest="build.sbt",
        source="sbt",
        deps=deps,
    )


# A claimed build.sbt is decided, so the manifest fallback never owns root-level
# files (an aggregate-only root, or sbt's implicit root); give them the unit it would have made.
_ROOT_FALLBACK = BuildUnit(
    id="manifest:.",
    ecosystem="manifest",
    name=".",
    root="",
    manifest="build.sbt",
    source="manifest-root",
)


def units(ctx: ResolverContext) -> Found:
    found = _sbt_projects(ctx)
    if found is None:
        return Found([])
    index, projects = found
    root_has_sources = any(
        p.startswith("src/") and p.endswith((".scala", ".java")) for p in ctx.path_set
    )
    # An aggregate-only root (no sources of its own) is not a unit
    names = [n for n, d in projects.items() if d or root_has_sources]
    claims = [(_unit(index, projects, name), ()) for name in names]
    if claims and not any(unit.root == "" for unit, _files in claims):
        claims.append((_ROOT_FALLBACK, ()))
    return Found(claims)
