"""Resolve local Maven coordinates into cross-repository package links."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

from repowise.core.ingestion.external_systems.maven_model import (
    MavenDependency,
    MavenModelDiagnostic,
    MavenProject,
    MavenReactor,
    load_maven_reactor,
)

_STRUCTURAL_SCOPES = frozenset({"compile", "runtime"})
#: The types of dependency whose artifact Maven hands on the dependencies of: those of the
#: artifact handlers of Maven 3.10 that do not include them. A jar of tests (classifier
#: ``tests``) is of type ``jar`` or ``test-jar``.
_HANDS_ON_TYPES = frozenset(
    {"jar", "ejb", "ejb-client", "test-jar", "maven-plugin", "pom", "javadoc", "java-source"}
)


@dataclass(frozen=True)
class MavenWorkspaceLink:
    source_repo: str
    target_repo: str
    source_manifest: str
    target_manifest: str
    target_package: str
    requested_version: str | None
    scope: str
    resolution_basis: str = "unique_workspace_coordinate"


@dataclass(frozen=True)
class MavenWorkspaceDiagnostic:
    repo: str
    manifest: str
    code: str
    detail: str = ""


def _workspace_diagnostic(
    alias: str,
    diagnostic: MavenModelDiagnostic,
) -> MavenWorkspaceDiagnostic:
    return MavenWorkspaceDiagnostic(
        repo=alias,
        manifest=diagnostic.manifest,
        code=diagnostic.code,
        detail=diagnostic.detail,
    )


def _declared_reactor_projects(
    projects: tuple[MavenProject, ...],
    declared_manifests: tuple[str, ...],
) -> tuple[MavenProject, ...]:
    """Prefer the root reactor over unrelated nested example/fixture POMs."""
    if "pom.xml" in declared_manifests:
        by_manifest = {project.manifest: project for project in projects}
        return tuple(
            project
            for manifest in declared_manifests
            if (project := by_manifest.get(manifest)) is not None
        )

    by_manifest = {project.manifest: project for project in projects}
    root = by_manifest.get("pom.xml")
    if root is None:
        return projects

    selected: list[MavenProject] = []
    pending = [root.manifest]
    seen: set[str] = set()
    while pending:
        manifest = pending.pop(0)
        if manifest in seen:
            continue
        seen.add(manifest)
        project = by_manifest.get(manifest)
        if project is None:
            continue
        selected.append(project)
        pending.extend(project.modules)
    return tuple(selected)


def _inherited_by_dependents(dependency: MavenDependency) -> bool:
    """Whether *dependency* is compile or runtime, not optional, and its metadata is resolved."""
    return not (
        dependency.optional
        or dependency.has_unresolved_structural_metadata
        or dependency.scope not in _STRUCTURAL_SCOPES
        or _unnamed(dependency)
    )


def _structural(dependency: MavenDependency) -> bool:
    """Whether *dependency* is one of those and its type is not ``pom``."""
    return _inherited_by_dependents(dependency) and dependency.dependency_type != "pom"


def _unnamed(dependency: MavenDependency) -> bool:
    return (
        dependency.has_unresolved_coordinate
        or not dependency.group_id
        or not dependency.artifact_id
    )


@dataclass(frozen=True)
class _Workspace:
    projects: dict[str, tuple[MavenProject, ...]]
    producers: dict[str, list[tuple[str, MavenProject]]]
    diagnostics: list[MavenWorkspaceDiagnostic]


def _selected_diagnostics(
    alias: str, reactor: MavenReactor, projects: tuple[MavenProject, ...]
) -> list[MavenWorkspaceDiagnostic]:
    """The diagnostics of *reactor* to keep: with a root reactor, those of its declared
    manifests; otherwise all."""
    has_root_reactor = "pom.xml" in reactor.declared_manifests
    selected_manifests = (
        set(reactor.declared_manifests)
        if has_root_reactor
        else {project.manifest for project in projects}
    )
    return [
        _workspace_diagnostic(alias, item)
        for item in reactor.diagnostics
        if not has_root_reactor or item.manifest in selected_manifests
    ]


def _is_producer(project: MavenProject) -> bool:
    """Whether the coordinate of *project* is resolved, so a dependency can name it."""
    return not (
        project.has_unresolved_coordinate or not project.group_id or not project.artifact_id
    )


def _load(repo_paths: dict[str, Path], poms_by_repo: dict[str, list[Path]]) -> _Workspace:
    projects_by_repo: dict[str, tuple[MavenProject, ...]] = {}
    diagnostics: list[MavenWorkspaceDiagnostic] = []
    producers: dict[str, list[tuple[str, MavenProject]]] = defaultdict(list)

    for alias in sorted(repo_paths):
        reactor = load_maven_reactor(
            repo_paths[alias],
            tuple(sorted(poms_by_repo.get(alias, []))),
        )
        projects_by_repo[alias] = _declared_reactor_projects(
            reactor.projects, reactor.declared_manifests
        )
        diagnostics.extend(_selected_diagnostics(alias, reactor, projects_by_repo[alias]))
        for project in projects_by_repo[alias]:
            if _is_producer(project):
                producers[project.coordinate].append((alias, project))
    return _Workspace(projects_by_repo, producers, diagnostics)


def detect_maven_dependencies(
    repo_paths: dict[str, Path],
    poms_by_repo: dict[str, list[Path]],
) -> tuple[list[MavenWorkspaceLink], list[MavenWorkspaceDiagnostic]]:
    """Return exact local Maven links and honest non-match diagnostics.

    ``poms_by_repo`` must come from the caller's bounded workspace scan.  A
    dependency matches only one effective project coordinate across the
    entire selected workspace; duplicate producers are ambiguous and do not
    create an edge.
    """
    workspace = _load(repo_paths, poms_by_repo)
    projects_by_repo, producers = workspace.projects, workspace.producers
    diagnostics = workspace.diagnostics
    links: list[MavenWorkspaceLink] = []
    for source_alias in sorted(projects_by_repo):
        for source_project in projects_by_repo[source_alias]:
            for dependency in source_project.dependencies:
                if not _structural(dependency):
                    continue

                matches = producers.get(dependency.coordinate, [])
                if not matches:
                    diagnostics.append(
                        MavenWorkspaceDiagnostic(
                            repo=source_alias,
                            manifest=source_project.manifest,
                            code="external_coordinate",
                            detail=dependency.coordinate,
                        )
                    )
                    continue
                if len(matches) != 1:
                    locations = ",".join(
                        f"{alias}:{project.manifest}" for alias, project in matches
                    )
                    diagnostics.append(
                        MavenWorkspaceDiagnostic(
                            repo=source_alias,
                            manifest=source_project.manifest,
                            code="ambiguous_coordinate",
                            detail=f"{dependency.coordinate} -> {locations}",
                        )
                    )
                    continue

                target_alias, target_project = matches[0]
                if target_alias == source_alias:
                    continue
                links.append(
                    MavenWorkspaceLink(
                        source_repo=source_alias,
                        target_repo=target_alias,
                        source_manifest=source_project.manifest,
                        target_manifest=target_project.manifest,
                        target_package=dependency.coordinate,
                        requested_version=dependency.version,
                        scope=dependency.scope,
                    )
                )

    return links, list(dict.fromkeys(diagnostics))


def _hands_on(dependency: MavenDependency) -> bool:
    """Whether Maven hands on the dependencies of the artifact *dependency* names.

    A war, an ear, a rar and a fatjar include them. A type that no handler of
    Maven 3.10 gives here (``bundle`` and the types plugins add) is left out by the
    code's choice: Maven's default handler hands dependencies on, so the modules such
    an artifact needs are missed, never a wrong one counted.
    """
    return dependency.dependency_type in _HANDS_ON_TYPES


def _resolve_producer(
    workspace: _Workspace, source_alias: str, dependency: MavenDependency
) -> tuple[str, MavenProject] | None:
    """The one project *dependency* names, as Maven finds it.

    A project of the reactor of the declaring repo comes first. Otherwise the
    coordinate must have one producer in the whole workspace.
    """
    if _unnamed(dependency):
        return None
    matches = workspace.producers.get(dependency.coordinate, [])
    own = [match for match in matches if match[0] == source_alias]
    candidates = own or matches
    return candidates[0] if len(candidates) == 1 else None


def module_dependencies(
    repo_paths: dict[str, Path],
    poms_by_repo: dict[str, list[Path]],
    repos: Collection[str],
) -> dict[str, dict[str, frozenset[str]]]:
    """``{repo: {manifest of a module: the coordinates of the modules it depends on}}``.

    For each repo of *repos*, what the code of each of its modules is built
    against, among the modules of the workspace: each module its pom declares
    a dependency on, of any scope but ``system``, and what Maven hands on from
    it (:func:`_received`). Limit: an artifact of a type that no handler gives
    (:func:`_hands_on`) hands nothing on here, so a module may lack one it
    depends on.
    """
    workspace = _load(repo_paths, poms_by_repo)
    return {
        alias: {
            project.manifest: frozenset(
                module.coordinate
                for module in _received(
                    workspace, ((alias, d) for d in project.dependencies if d.scope != "system")
                ).values()
            )
            for project in workspace.projects.get(alias, ())
        }
        for alias in repos
    }


def _received(
    workspace: _Workspace, edges: Iterable[tuple[str, MavenDependency]]
) -> dict[tuple[str, str], MavenProject]:
    """The modules that *edges* name, and what Maven hands on from them, by repo and manifest.

    An edge is a dependency and the repo whose pom declares it. It names its
    module as Maven finds it (:func:`_resolve_producer`); one that names no
    single module gives none. Where the type of the edge hands on the
    dependencies of its artifact (:func:`_hands_on`), the module hands on its
    compile and runtime dependencies that are not optional, in any repo, and
    theirs in turn. A module is walked once, so a cycle ends.
    """
    reached: dict[tuple[str, str], MavenProject] = {}
    walked: set[tuple[str, str]] = set()
    queue = list(edges)
    while queue:
        source, dependency = queue.pop()
        target = _resolve_producer(workspace, source, dependency)
        if target is None:
            continue
        alias, module = target
        key = (alias, module.manifest)
        reached[key] = module
        if key in walked or not _hands_on(dependency):
            continue
        walked.add(key)
        queue.extend((alias, d) for d in module.dependencies if _inherited_by_dependents(d))
    return reached
