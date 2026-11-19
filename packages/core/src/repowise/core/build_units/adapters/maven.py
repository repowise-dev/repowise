"""Maven reactor modules: the root ``pom.xml`` and its ``<modules>``, followed recursively.

Read with :func:`load_maven_reactor`, which resolves coordinates through each
module's local parents, so a module that leaves out ``groupId`` takes its
parent's. A POM it parsed outside the root's reactor (an example or fixture
project) is ruled out, so the fallback skips it too. A POM it could not
parse, or one whose coordinates keep an unresolved ``${...}``, is left to the
fallback. Deps are the ``<dependencies>`` naming another module's
``groupId:artifactId``, inherited parent dependencies included, at every
scope: a ``test`` dependency is built first like any other.
"""

from __future__ import annotations

import posixpath

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.adapters.manifest_roots import package_manifests
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.external_systems.maven_model import load_maven_reactor
from repowise.core.ingestion.resolvers.context import ResolverContext

# Without a JVM source file a reactor owns nothing worth parsing POMs for.
_SOURCE_SUFFIXES = (".java", ".kt", ".scala", ".groovy")


def units(ctx: ResolverContext) -> Found:
    manifests = package_manifests(ctx)
    if "pom.xml" not in manifests.get("", ()) or ctx.repo_path is None:
        return Found([])
    if not any(path.endswith(_SOURCE_SUFFIXES) for path in ctx.path_set):
        return Found([])
    repo = ctx.repo_path
    poms = [repo / root / "pom.xml" for root, names in manifests.items() if "pom.xml" in names]
    reactor = load_maven_reactor(repo, poms)
    declared = set(reactor.declared_manifests)
    projects = [
        project
        for project in reactor.projects
        if project.manifest in declared and not project.has_unresolved_coordinate
    ]
    coordinates = {project.coordinate for project in projects}

    out: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for project in projects:
        deps = {
            f"maven:{dep.coordinate}"
            for dep in project.dependencies
            if dep.coordinate in coordinates
        }
        unit = BuildUnit(
            id=f"maven:{project.coordinate}",
            ecosystem="maven",
            name=project.artifact_id,
            root=posixpath.dirname(project.manifest),
            manifest=project.manifest,
            source="maven",
            deps=tuple(deps),
        )
        out.append((unit, ()))
    outside = frozenset(p.manifest for p in reactor.projects if p.manifest not in declared)
    return Found(out, outside)
