"""Resolve local Maven coordinates into cross-repository package links."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from repowise.core.analysis.published_packages import maven_stands_alone
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
    workspace: _Workspace,
    edges: Iterable[tuple[str, MavenDependency]],
    stops: Callable[[str, MavenProject], bool] = lambda alias, module: False,
) -> dict[tuple[str, str], MavenProject]:
    """The modules that *edges* name, and what Maven hands on from them, by repo and manifest.

    An edge is a dependency and the repo whose pom declares it. It names its
    module as Maven finds it (:func:`_resolve_producer`); one that names no
    single module gives none. Where the type of the edge hands on the
    dependencies of its artifact (:func:`_hands_on`), the module hands on its
    compile and runtime dependencies that are not optional, in any repo, and
    theirs in turn, unless *stops* holds for the module and its repo. A module is
    walked once, so a cycle ends.
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
        if not stops(alias, module):
            queue.extend((alias, d) for d in module.dependencies if _inherited_by_dependents(d))
    return reached


def _stands_alone(repo_root: Path, project: MavenProject) -> bool:
    """Whether *project* stands alone, as :func:`maven_stands_alone` reads its pom."""
    try:
        text = (repo_root / project.manifest).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return maven_stands_alone(text)


def depended_manifests(
    repo_paths: dict[str, Path],
    poms_by_repo: dict[str, list[Path]],
) -> dict[str, frozenset[str]]:
    """The manifests of the modules a pom of another repo depends on, and what they need.

    A dependency names an artifact that someone deploys, so every module reached
    through an edge Maven resolves for a dependent counts, of any type. The producers
    and the reactor selection are those of :func:`detect_maven_dependencies`; a
    dependency names its module as Maven finds it (:func:`_resolve_producer`).

    - First hop: a pom of another repo declares a dependency on a module, of any
      scope but ``system``, any classifier, optional or not.
    - Then what Maven hands on from that module (:func:`_received`): the type of the
      edge decides only whether the walk goes on past the module it names
      (:func:`_hands_on`). An edge of the walk into another repo is a first hop of
      its own.

    By the code's choice, the walk stops at a module that the pom reader marks as standing
    alone (:func:`_stands_alone`): a war or ear packaging, or the Spring Boot plugin named
    anywhere in its pom, whatever the type or the classifier of the edge. Every such module
    is an app in :func:`jvm_published` too, so it gives no contract. A packaging ``pom`` and
    a skipped deploy are no stop. Limits. Missed, never wrong: the needs of a module that
    stands alone when the edge names one of its artifacts that hands them on (its
    ``test-jar`` or tests jar, its ``classes`` jar, or the plain jar of a Boot module whose
    fat jar has a classifier such as ``exec``); and a module that only names the Boot plugin
    (in ``pluginManagement``, a profile or a property), which stops the walk. Walked
    through, wrong only on a build that Maven rejects (a jar edge to a war resolves no
    artifact): a war whose packaging a property holds, or whose own ``<packaging>`` comes
    after a ``<packaging>`` in a plugin's configuration. The other order, a plugin's war
    before a jar module's own packaging, stops the walk: missed.

    A repo no other repo depends on has no entry. The module of an application counts
    here; :func:`jvm_published` leaves it out, as it does a ``pom`` and a skipped deploy.
    """
    workspace = _load(repo_paths, poms_by_repo)
    first_hops = (
        (source, dependency)
        for source, dependency, (target, _) in _resolved_dependencies(workspace)
        if target != source and dependency.scope != "system"
    )
    reached: dict[str, set[str]] = defaultdict(set)
    for alias, manifest in _received(
        workspace, first_hops, lambda alias, module: _stands_alone(repo_paths[alias], module)
    ):
        reached[alias].add(manifest)
    return {alias: frozenset(manifests) for alias, manifests in reached.items()}


def _resolved_dependencies(
    workspace: _Workspace,
) -> Iterator[tuple[str, MavenDependency, tuple[str, MavenProject]]]:
    """Each dependency that names exactly one module (:func:`_resolve_producer`), as the
    declaring repo, the dependency and the module as (repo, project)."""
    for source_alias, projects in workspace.projects.items():
        for dependency in (d for project in projects for d in project.dependencies):
            target = _resolve_producer(workspace, source_alias, dependency)
            if target is not None:
                yield source_alias, dependency, target


def depended_dirs(repo_paths: dict[str, Path]) -> dict[str, frozenset[str]]:
    """The dirs of the modules a pom of another repo depends on, and what they need.

    :func:`depended_manifests` over the poms of every repo, as the cross-repo
    package step reads them. One repo gives nothing.
    """
    from .manifests import find_poms

    if len(repo_paths) < 2:
        return {}
    poms = {alias: find_poms(path) for alias, path in repo_paths.items()}
    return {
        alias: frozenset(
            "" if (parent := PurePosixPath(manifest).parent.as_posix()) == "." else parent
            for manifest in manifests
        )
        for alias, manifests in depended_manifests(repo_paths, poms).items()
    }
