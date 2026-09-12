"""Bounded, filesystem-only Maven POM model resolution.

This module deliberately implements a small Maven subset.  It reads POM XML
already present in a repository and resolves local parents, reactor modules,
properties, and dependency-management entries.  It never invokes Maven,
loads settings, downloads artifacts, or attempts transitive resolution.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

_NS = re.compile(r"\{[^}]*\}")
_PROP_RE = re.compile(r"\$\{([^}]+)\}")
_MAX_PARENT_DEPTH = 16
_MAX_PROPERTY_PASSES = 8


@dataclass(frozen=True)
class MavenDependency:
    group_id: str
    artifact_id: str
    version: str | None
    scope: str
    optional: bool
    dependency_type: str
    classifier: str
    declared_in: str
    has_unresolved_structural_metadata: bool = False

    @property
    def coordinate(self) -> str:
        return f"{self.group_id}:{self.artifact_id}"

    @property
    def has_unresolved_coordinate(self) -> bool:
        return "${" in self.group_id or "${" in self.artifact_id


@dataclass(frozen=True)
class MavenProject:
    group_id: str
    artifact_id: str
    version: str | None
    packaging: str
    manifest: str
    modules: tuple[str, ...]
    dependencies: tuple[MavenDependency, ...]

    @property
    def coordinate(self) -> str:
        return f"{self.group_id}:{self.artifact_id}"

    @property
    def has_unresolved_coordinate(self) -> bool:
        return "${" in self.group_id or "${" in self.artifact_id


@dataclass(frozen=True)
class MavenModelDiagnostic:
    manifest: str
    code: str
    detail: str = ""


@dataclass(frozen=True)
class MavenReactor:
    projects: tuple[MavenProject, ...] = ()
    diagnostics: tuple[MavenModelDiagnostic, ...] = ()
    declared_manifests: tuple[str, ...] = ()


@dataclass
class _RawDependency:
    group_id: str
    artifact_id: str
    version: str
    scope: str
    optional: str
    dependency_type: str
    classifier: str


@dataclass
class _RawPom:
    path: Path
    relative_path: str
    group_id: str
    artifact_id: str
    version: str
    packaging: str
    parent_group_id: str
    parent_artifact_id: str
    parent_version: str
    parent_path: Path | None
    external_parent_detail: str
    properties: dict[str, str] = field(default_factory=dict)
    managed: list[_RawDependency] = field(default_factory=list)
    dependencies: list[_RawDependency] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)


@dataclass
class _EffectivePom:
    project: MavenProject
    properties: dict[str, str]
    raw_properties: dict[str, str]
    managed_entries: tuple[tuple[_RawDependency, str], ...]
    dependency_entries: tuple[tuple[_RawDependency, str], ...]


def _strip_ns(tag: str) -> str:
    return _NS.sub("", tag)


def _find(element: ET.Element, local_name: str) -> ET.Element | None:
    return next(
        (child for child in element if _strip_ns(child.tag) == local_name),
        None,
    )


def _find_text(element: ET.Element, local_name: str) -> str:
    child = _find(element, local_name)
    return (child.text or "").strip() if child is not None else ""


def _dependencies(element: ET.Element | None) -> list[_RawDependency]:
    if element is None:
        return []
    dependencies = _find(element, "dependencies")
    if dependencies is None:
        return []
    result: list[_RawDependency] = []
    for dependency in dependencies:
        if _strip_ns(dependency.tag) != "dependency":
            continue
        result.append(
            _RawDependency(
                group_id=_find_text(dependency, "groupId"),
                artifact_id=_find_text(dependency, "artifactId"),
                version=_find_text(dependency, "version"),
                scope=_find_text(dependency, "scope"),
                optional=_find_text(dependency, "optional"),
                dependency_type=_find_text(dependency, "type"),
                classifier=_find_text(dependency, "classifier"),
            )
        )
    return result


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _parse_raw(
    manifest_path: Path,
    repo_root: Path,
    diagnostics: list[MavenModelDiagnostic],
) -> _RawPom | None:
    try:
        root = ET.parse(manifest_path).getroot()
    except (ET.ParseError, OSError) as exc:
        diagnostics.append(
            MavenModelDiagnostic(
                manifest=_relative(manifest_path, repo_root),
                code="malformed_pom",
                detail=type(exc).__name__,
            )
        )
        return None

    parent = _find(root, "parent")
    parent_path: Path | None = None
    external_parent_detail = ""
    parent_group_id = parent_artifact_id = parent_version = ""
    if parent is not None:
        parent_group_id = _find_text(parent, "groupId")
        parent_artifact_id = _find_text(parent, "artifactId")
        parent_version = _find_text(parent, "version")
        relative = _find(parent, "relativePath")
        # Maven defaults to ../pom.xml.  An explicitly empty element disables
        # local parent lookup and leaves the parent external to this model.
        if relative is None:
            relative_text: str | None = "../pom.xml"
        else:
            relative_text = (relative.text or "").strip() or None
        if relative_text is not None:
            candidate = (manifest_path.parent / relative_text).resolve()
            if candidate.is_dir():
                candidate /= "pom.xml"
            root_resolved = repo_root.resolve()
            if _is_within(candidate, root_resolved):
                parent_path = candidate
            else:
                external_parent_detail = (
                    f"{parent_group_id}:{parent_artifact_id}:{parent_version} "
                    f"(outside repository: {relative_text})"
                )
        else:
            external_parent_detail = (
                f"{parent_group_id}:{parent_artifact_id}:{parent_version} (local lookup disabled)"
            )

    properties: dict[str, str] = {}
    props = _find(root, "properties")
    if props is not None:
        for child in props:
            if child.text is not None:
                properties[_strip_ns(child.tag)] = child.text.strip()

    modules: list[str] = []
    modules_element = _find(root, "modules")
    if modules_element is not None:
        modules = [
            (module.text or "").strip()
            for module in modules_element
            if _strip_ns(module.tag) == "module" and (module.text or "").strip()
        ]

    return _RawPom(
        path=manifest_path.resolve(),
        relative_path=_relative(manifest_path, repo_root),
        group_id=_find_text(root, "groupId"),
        artifact_id=_find_text(root, "artifactId"),
        version=_find_text(root, "version"),
        packaging=_find_text(root, "packaging") or "jar",
        parent_group_id=parent_group_id,
        parent_artifact_id=parent_artifact_id,
        parent_version=parent_version,
        parent_path=parent_path,
        external_parent_detail=external_parent_detail,
        properties=properties,
        managed=_dependencies(_find(root, "dependencyManagement")),
        dependencies=_dependencies(root),
        modules=modules,
    )


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def _resolve(value: str, properties: dict[str, str]) -> str:
    current = value
    for _ in range(_MAX_PROPERTY_PASSES):
        resolved = _PROP_RE.sub(
            lambda match: properties.get(match.group(1), match.group(0)), current
        )
        if resolved == current:
            break
        current = resolved
    return current


def _resolve_properties(properties: dict[str, str]) -> dict[str, str]:
    resolved = dict(properties)
    for _ in range(_MAX_PROPERTY_PASSES):
        changed = False
        for key, value in tuple(resolved.items()):
            new_value = _resolve(value, resolved)
            if new_value != value:
                resolved[key] = new_value
                changed = True
        if not changed:
            break
    return resolved


def load_maven_reactor(
    repo_root: Path,
    manifest_paths: list[Path] | tuple[Path, ...],
) -> MavenReactor:
    """Resolve the supported local Maven model for the supplied POM files.

    Parent POMs referenced by those files are read on demand when they remain
    inside ``repo_root``.  Reactor module POMs should be supplied by the
    caller's existing bounded repository walk; missing declared modules are
    reported and never discovered with a second unbounded scan.
    """

    root = repo_root.resolve()
    diagnostics: list[MavenModelDiagnostic] = []
    raw_by_path: dict[Path, _RawPom] = {}
    pending = sorted({path.resolve() for path in manifest_paths})

    while pending:
        path = pending.pop(0)
        if path in raw_by_path or not _is_within(path, root):
            continue
        raw = _parse_raw(path, root, diagnostics)
        if raw is None:
            continue
        raw_by_path[path] = raw
        if raw.external_parent_detail:
            diagnostics.append(
                MavenModelDiagnostic(
                    manifest=raw.relative_path,
                    code="external_parent_unsupported",
                    detail=raw.external_parent_detail,
                )
            )
        if raw.parent_path is not None and raw.parent_path not in raw_by_path:
            if raw.parent_path.is_file():
                pending.append(raw.parent_path)
            else:
                diagnostics.append(
                    MavenModelDiagnostic(
                        manifest=raw.relative_path,
                        code="unresolved_parent",
                        detail=_relative(raw.parent_path, root),
                    )
                )

    for raw in raw_by_path.values():
        if raw.parent_path is not None and raw.parent_path not in raw_by_path:
            diagnostics.append(
                MavenModelDiagnostic(
                    manifest=raw.relative_path,
                    code="unresolved_parent",
                    detail=_relative(raw.parent_path, root),
                )
            )

    cache: dict[Path, _EffectivePom | None] = {}
    failed: set[Path] = set()
    failure_codes: dict[Path, str] = {}

    def build(path: Path, stack: tuple[Path, ...]) -> _EffectivePom | None:
        if path in failed:
            return None
        if path in cache:
            return cache[path]
        raw = raw_by_path.get(path)
        if raw is None:
            return None
        if path in stack:
            diagnostics.append(MavenModelDiagnostic(raw.relative_path, "parent_cycle"))
            cycle_paths = stack[stack.index(path) :]
            failed.update(cycle_paths)
            for invalid_path in cycle_paths:
                cache[invalid_path] = None
                failure_codes[invalid_path] = "parent_cycle"
            return None
        if len(stack) >= _MAX_PARENT_DEPTH:
            diagnostics.append(MavenModelDiagnostic(raw.relative_path, "parent_depth_exceeded"))
            depth_paths = set(stack) | {path}
            next_path = raw.parent_path
            while next_path in raw_by_path and next_path not in depth_paths:
                depth_paths.add(next_path)
                next_path = raw_by_path[next_path].parent_path
            failed.update(depth_paths)
            for invalid_path in depth_paths:
                cache[invalid_path] = None
                failure_codes[invalid_path] = "parent_depth_exceeded"
            return None

        has_local_parent = raw.parent_path is not None and raw.parent_path in raw_by_path
        parent = (
            build(raw.parent_path, (*stack, path))
            if raw.parent_path is not None and has_local_parent
            else None
        )
        if has_local_parent and parent is None:
            assert raw.parent_path is not None
            failure_code = failure_codes.get(raw.parent_path, "invalid_parent_chain")
            diagnostics.append(
                MavenModelDiagnostic(
                    raw.relative_path,
                    failure_code,
                    _relative(raw.parent_path, root),
                )
            )
            failed.add(path)
            failure_codes[path] = failure_code
            cache[path] = None
            return None
        if parent is not None:
            declared_parent = (
                _resolve(raw.parent_group_id, parent.properties),
                _resolve(raw.parent_artifact_id, parent.properties),
                _resolve(raw.parent_version, parent.properties),
            )
            effective_parent = (
                parent.project.group_id,
                parent.project.artifact_id,
                parent.project.version or "",
            )
            coordinates_match = all(
                declared == effective or not declared or "${" in declared
                for declared, effective in zip(
                    declared_parent,
                    effective_parent,
                    strict=True,
                )
            )
            if not coordinates_match:
                diagnostics.append(
                    MavenModelDiagnostic(
                        raw.relative_path,
                        "parent_coordinate_mismatch",
                        f"declared={':'.join(declared_parent)} local={':'.join(effective_parent)}",
                    )
                )
                parent = None
        raw_properties = dict(parent.raw_properties) if parent is not None else {}
        raw_properties.update(raw.properties)
        properties = dict(raw_properties)

        parent_group = parent.project.group_id if parent is not None else raw.parent_group_id
        parent_version = (
            (parent.project.version or "") if parent is not None else raw.parent_version
        )
        if parent_group:
            properties.setdefault("project.parent.groupId", parent_group)
            properties.setdefault("pom.parent.groupId", parent_group)
        if parent_version:
            properties.setdefault("project.parent.version", parent_version)
            properties.setdefault("pom.parent.version", parent_version)

        properties = _resolve_properties(properties)
        group_id = _resolve(raw.group_id or parent_group, properties)
        artifact_id = _resolve(raw.artifact_id, properties)
        version = _resolve(raw.version or parent_version, properties)
        builtins = {
            "project.groupId": group_id,
            "pom.groupId": group_id,
            "project.artifactId": artifact_id,
            "pom.artifactId": artifact_id,
            "project.version": version,
            "pom.version": version,
        }
        properties.update({key: value for key, value in builtins.items() if value})
        properties = _resolve_properties(properties)
        group_id = _resolve(group_id, properties)
        artifact_id = _resolve(artifact_id, properties)
        version = _resolve(version, properties)

        managed_entries = list(parent.managed_entries) if parent is not None else []
        managed_entries.extend((dependency, raw.relative_path) for dependency in raw.managed)
        managed: dict[tuple[str, str, str, str], str] = {}
        for dependency, _declared_in in managed_entries:
            dep_group = _resolve(dependency.group_id, properties)
            dep_artifact = _resolve(dependency.artifact_id, properties)
            dep_version = _resolve(dependency.version, properties)
            dep_type = _resolve(dependency.dependency_type, properties).strip().lower() or "jar"
            dep_classifier = _resolve(dependency.classifier, properties).strip()
            if dep_group and dep_artifact and dep_version and "${" not in dep_version:
                managed[(dep_group, dep_artifact, dep_type, dep_classifier)] = dep_version

        dependency_entries = list(parent.dependency_entries) if parent is not None else []
        dependency_entries.extend(
            (dependency, raw.relative_path) for dependency in raw.dependencies
        )
        dependencies_by_key: dict[tuple[str, str, str, str], MavenDependency] = {}
        for dependency, declared_in in dependency_entries:
            dep_group = _resolve(dependency.group_id, properties)
            dep_artifact = _resolve(dependency.artifact_id, properties)
            coordinate = f"{dep_group}:{dep_artifact}"
            dep_type = _resolve(dependency.dependency_type, properties).strip().lower() or "jar"
            dep_classifier = _resolve(dependency.classifier, properties).strip()
            dep_version = _resolve(dependency.version, properties)
            if not dep_version:
                dep_version = managed.get((dep_group, dep_artifact, dep_type, dep_classifier), "")
            unresolved_version = "${" in dep_version
            dep_scope = _resolve(dependency.scope, properties).strip().lower() or "compile"
            dep_optional_text = _resolve(dependency.optional, properties).strip().lower()
            dep_optional = dep_optional_text == "true"
            unresolved_metadata = any(
                "${" in value for value in (dep_scope, dep_optional_text, dep_type, dep_classifier)
            )
            dep = MavenDependency(
                group_id=dep_group,
                artifact_id=dep_artifact,
                version=dep_version if dep_version and not unresolved_version else None,
                scope=dep_scope,
                optional=dep_optional,
                dependency_type=dep_type,
                classifier=dep_classifier,
                declared_in=declared_in,
                has_unresolved_structural_metadata=unresolved_metadata,
            )
            if not dep.group_id or not dep.artifact_id or dep.has_unresolved_coordinate:
                diagnostics.append(
                    MavenModelDiagnostic(
                        declared_in,
                        "unresolved_dependency_coordinate",
                        coordinate,
                    )
                )
            if unresolved_version:
                diagnostics.append(
                    MavenModelDiagnostic(
                        declared_in,
                        "unresolved_dependency_version",
                        dep_version,
                    )
                )
            if unresolved_metadata:
                diagnostics.append(
                    MavenModelDiagnostic(
                        declared_in,
                        "unresolved_dependency_metadata",
                        coordinate,
                    )
                )
            dependencies_by_key[
                (dep.group_id, dep.artifact_id, dep.dependency_type, dep.classifier)
            ] = dep

        dependencies = list(dependencies_by_key.values())

        modules: list[str] = []
        for module in raw.modules:
            resolved_module = _resolve(module, properties)
            module_path = (raw.path.parent / resolved_module).resolve()
            if module_path.is_dir():
                module_path /= "pom.xml"
            if not _is_within(module_path, root) or module_path not in raw_by_path:
                diagnostics.append(
                    MavenModelDiagnostic(
                        raw.relative_path,
                        "unresolved_module",
                        resolved_module,
                    )
                )
                continue
            modules.append(_relative(module_path, root))

        project = MavenProject(
            group_id=group_id,
            artifact_id=artifact_id,
            version=version if version and "${" not in version else None,
            packaging=_resolve(raw.packaging, properties),
            manifest=raw.relative_path,
            modules=tuple(modules),
            dependencies=tuple(dependencies),
        )
        if not group_id or not artifact_id or project.has_unresolved_coordinate:
            diagnostics.append(
                MavenModelDiagnostic(
                    raw.relative_path,
                    "unresolved_project_coordinate",
                    project.coordinate,
                )
            )
        if "${" in version:
            diagnostics.append(
                MavenModelDiagnostic(
                    raw.relative_path,
                    "unresolved_project_version",
                    version,
                )
            )
        cache[path] = _EffectivePom(
            project=project,
            properties=properties,
            raw_properties=raw_properties,
            managed_entries=tuple(managed_entries),
            dependency_entries=tuple(dependency_entries),
        )
        return cache[path]

    for path in sorted(raw_by_path):
        build(path, ())

    projects = tuple(
        effective.project for path in sorted(cache) if (effective := cache[path]) is not None
    )
    declared_manifests: list[str] = []
    root_manifest = root / "pom.xml"
    if root_manifest in raw_by_path:
        pending_manifests = [root_manifest]
        seen_manifests: set[Path] = set()
        while pending_manifests:
            manifest_path = pending_manifests.pop(0)
            if manifest_path in seen_manifests:
                continue
            seen_manifests.add(manifest_path)
            raw = raw_by_path.get(manifest_path)
            if raw is None:
                continue
            declared_manifests.append(raw.relative_path)
            effective = cache.get(manifest_path)
            module_properties = (
                effective.properties
                if effective is not None
                else _resolve_properties(raw.properties)
            )
            for module in raw.modules:
                module_path = (raw.path.parent / _resolve(module, module_properties)).resolve()
                if module_path.is_dir():
                    module_path /= "pom.xml"
                if module_path in raw_by_path:
                    pending_manifests.append(module_path)
    elif root_manifest.is_file():
        # A malformed physical root still establishes the reactor boundary.
        # Do not promote unrelated nested example/fixture POMs to standalone
        # workspace projects merely because the root could not be parsed.
        declared_manifests.append("pom.xml")
    unique_diagnostics = tuple(dict.fromkeys(diagnostics))
    return MavenReactor(
        projects=projects,
        diagnostics=unique_diagnostics,
        declared_manifests=tuple(declared_manifests),
    )
