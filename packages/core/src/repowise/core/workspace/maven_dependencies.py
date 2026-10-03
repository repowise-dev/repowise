"""Resolve local Maven coordinates into cross-repository package links."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from repowise.core.ingestion.external_systems.maven_model import (
    MavenModelDiagnostic,
    MavenProject,
    load_maven_reactor,
)

_STRUCTURAL_SCOPES = frozenset({"compile", "runtime"})


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
        has_root_reactor = "pom.xml" in reactor.declared_manifests
        selected_manifests = (
            set(reactor.declared_manifests)
            if has_root_reactor
            else {project.manifest for project in projects_by_repo[alias]}
        )
        diagnostics.extend(
            _workspace_diagnostic(alias, item)
            for item in reactor.diagnostics
            if not has_root_reactor or item.manifest in selected_manifests
        )
        for project in projects_by_repo[alias]:
            if project.has_unresolved_coordinate or not project.group_id or not project.artifact_id:
                continue
            producers[project.coordinate].append((alias, project))

    links: list[MavenWorkspaceLink] = []
    for source_alias in sorted(projects_by_repo):
        for source_project in projects_by_repo[source_alias]:
            for dependency in source_project.dependencies:
                if (
                    dependency.optional
                    or dependency.has_unresolved_structural_metadata
                    or dependency.scope not in _STRUCTURAL_SCOPES
                    or dependency.dependency_type == "pom"
                    or dependency.has_unresolved_coordinate
                    or not dependency.group_id
                    or not dependency.artifact_id
                ):
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
