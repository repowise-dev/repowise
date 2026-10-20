"""Package manifests across a workspace: one table keyed by ecosystem.

Each :class:`Ecosystem` names the files that mark a directory as a service root
(read by :mod:`.extractors.service_boundary`) and, where it has one, the scanner
that turns its manifests into cross-repo package edges (read by
:func:`detect_package_dependencies_with_diagnostics`). Adding an ecosystem is
one row; the two consumers cannot drift apart.

Service markers are deliberately not the language registry's package-root
manifests: a ``Dockerfile`` or ``requirements.txt`` marks a deployable service
without being a package, while build files such as ``*.csproj`` mark packages
inside one service.

:func:`_index_manifests` reads every repo once up front: one pruned walk finds
the .NET, Maven and composer manifests, and the declared npm workspace gives
the npm packages the repo publishes, so a dependency can match a sibling repo
by package *name*, not only by a relative path.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repowise.core.ingestion.composer import (
    COMPOSER_JSON,
    ComposerManifest,
    is_package_dir,
    read_composer,
)
from repowise.core.ingestion.resolvers.ts_workspace import (
    build_workspace_map,
    read_workspaces_field,
)

_log = logging.getLogger("repowise.workspace.manifests")

PACKAGE_JSON = "package.json"

# Package non-matches are useful evidence but ordinary workspaces can declare
# thousands of external libraries. Persist a deterministic sample plus the
# uncapped total so diagnostics cannot make the overlay unbounded.
_MAX_PACKAGE_DIAGNOSTICS: int = 200

_NPM_DEP_SECTIONS = ("dependencies", "devDependencies", "peerDependencies")


@dataclass
class CrossRepoPackageDep:
    source_repo: str
    target_repo: str
    source_manifest: str
    # npm_local_path, npm_workspace, npm_package, composer_path, composer_package,
    # pip_path, cargo_path, go_replace, dotnet_project_ref, dotnet_nuget_internal,
    # maven_coordinate
    kind: str
    target_package: str = ""
    target_manifest: str = ""
    requested_version: str | None = None
    scope: str = ""
    resolution_basis: str = ""


@dataclass
class CrossRepoPackageDiagnostic:
    repo: str
    source_manifest: str
    code: str
    detail: str = ""


# ---------------------------------------------------------------------------
# The workspace index
# ---------------------------------------------------------------------------

_CSPROJ_SKIP_DIRS = frozenset(
    {"bin", "obj", ".vs", "packages", "node_modules", ".git", "TestResults"}
)
_CSPROJ_SKIP_DIRS_LOWER = frozenset(name.lower() for name in _CSPROJ_SKIP_DIRS)


@dataclass
class _ManifestIndex:
    """Project manifests in the workspace, read and parsed exactly once.

    The assembly-name and package-name maps depend only on ``repo_paths``,
    never on the alias being scanned, so they are built here once instead of
    per repo scanned.
    """

    repo_paths: dict[str, Path] = field(default_factory=dict)
    # Parsed once and shared; ElementTree instances are only ever read.
    trees: dict[Path, Any] = field(default_factory=dict)
    by_repo: dict[str, list[Path]] = field(default_factory=dict)
    poms_by_repo: dict[str, list[Path]] = field(default_factory=dict)
    assembly_to_repo: dict[str, str] = field(default_factory=dict)
    #: alias -> [(repo-relative manifest path, decoded package.json)]
    npm_by_repo: dict[str, list[tuple[str, dict]]] = field(default_factory=dict)
    composer_by_repo: dict[str, list[ComposerManifest]] = field(default_factory=dict)
    #: (ecosystem, published name) -> (alias, manifest), None once two repos claim it
    published: dict[tuple[str, str], tuple[str, str] | None] = field(default_factory=dict)

    def claim(self, ecosystem: str, name: str, alias: str, manifest: str) -> None:
        key = (ecosystem, name)
        if key not in self.published:
            self.published[key] = (alias, manifest)
        elif (owner := self.published[key]) is not None and owner[0] != alias:
            self.published[key] = None  # ambiguous: no edge to anyone

    def owner(self, ecosystem: str, name: str) -> tuple[str, str] | None:
        return self.published.get((ecosystem, name))


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _npm_packages(repo_path: Path) -> list[tuple[str, dict]]:
    """The root ``package.json`` and every declared workspace member's.

    Workspace membership is the npm definition of "a package this repo
    ships"; a nested ``package.json`` that is not a member is a fixture, an
    example or a vendored copy (``compiled/semver``), and matching on its
    name would invent an edge.
    """
    out: list[tuple[str, dict]] = []
    # A member glob can reach outside the repo (``../shared``); that package
    # belongs to the sibling, which claims it itself.
    members = {
        d for d in build_workspace_map(repo_path).values() if d != "." and not d.startswith("..")
    }
    rels = [PACKAGE_JSON] + [f"{d}/{PACKAGE_JSON}" for d in sorted(members)]
    for rel in rels:
        data = _read_json(repo_path / rel)
        if isinstance(data, dict):
            out.append((rel, data))
    return out


def _walk_project_manifests(
    repo_root: Path,
    index: _ManifestIndex,
) -> tuple[list[Path], list[Path], list[ComposerManifest]]:
    """Collect .NET, Maven and first-party composer manifests in one pruned walk."""
    from xml.etree import ElementTree as ET

    from repowise.core.fs_walk import PRUNED_DIRS, walk_repo

    csprojects: list[Path] = []
    poms: list[Path] = []
    composer: list[ComposerManifest] = []
    # ``packages`` is a .NET package cache convention but also a legitimate
    # Maven module directory, so it cannot be pruned for the shared walk.
    prune = PRUNED_DIRS | {
        "target",
        "dist",
        "build",
        "bin",
        "obj",
        ".vs",
        "TestResults",
    }
    for dirpath, _dirnames, filenames in walk_repo(repo_root, prune_dirs=prune):
        for filename in sorted(filenames):
            path = dirpath / filename
            if filename == "pom.xml":
                poms.append(path)
            elif filename == COMPOSER_JSON:
                rel = path.parent.relative_to(repo_root).as_posix()
                rel = "" if rel == "." else rel
                if is_package_dir(rel) and (manifest := read_composer(path, rel)) is not None:
                    composer.append(manifest)
            elif filename.lower().endswith(".csproj"):
                relative_dirs = path.relative_to(repo_root).parts[:-1]
                if any(part.lower() in _CSPROJ_SKIP_DIRS_LOWER for part in relative_dirs):
                    continue
                try:
                    index.trees[path] = ET.parse(path)
                except (ET.ParseError, OSError):
                    continue
                csprojects.append(path)
    # Root first, then by directory: the order a name is first claimed in.
    composer.sort(key=lambda m: (m.rel_dir != "", m.rel_dir))
    return csprojects, poms, composer


def _assembly_name(tree: Any, csproj: Path) -> str:
    """The project's assembly name, defaulting to the filename minus extension."""
    for elem in tree.getroot().iter():
        tag = elem.tag.split("}", 1)[1] if elem.tag.startswith("{") else elem.tag
        if tag == "AssemblyName" and elem.text:
            return elem.text.strip()
    return csproj.stem


def _index_manifests(repo_paths: dict[str, Path]) -> _ManifestIndex:
    """Read each repo's manifests once, parse each once, build the name maps."""
    index = _ManifestIndex(repo_paths=repo_paths)
    # Insertion order matches the previous per-alias rebuild, so a name
    # defined in two repos still resolves to the last repo in repo_paths.
    for alias, path in repo_paths.items():
        found, poms, composer = _walk_project_manifests(path, index)
        for csproj in found:
            index.assembly_to_repo[_assembly_name(index.trees[csproj], csproj)] = alias
        index.by_repo[alias] = found
        index.poms_by_repo[alias] = poms
        index.npm_by_repo[alias] = _npm_packages(path)
        index.composer_by_repo[alias] = composer
        for rel, data in index.npm_by_repo[alias]:
            name = data.get("name")
            if isinstance(name, str) and name:
                index.claim("npm", name, alias, rel)
        for manifest in index.composer_by_repo[alias]:
            if manifest.name:
                index.claim("composer", manifest.name, alias, _composer_path(manifest))
    return index


def _composer_path(manifest: ComposerManifest) -> str:
    return f"{manifest.rel_dir}/{COMPOSER_JSON}" if manifest.rel_dir else COMPOSER_JSON


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------

#: What a scanner yields; ``None`` is a reference that matched no sibling.
_Deps = Iterator["CrossRepoPackageDep | None"]


def _resolve_target_repo(
    relative_ref: str,
    source_repo_path: Path,
    repo_paths: dict[str, Path],
) -> str | None:
    """Resolve a relative path reference to a repo alias, or None."""
    try:
        target_abs = (source_repo_path / relative_ref).resolve()
        # Most-specific root first makes physically nested selected repos
        # deterministic. Path.relative_to supplies the separator-aware
        # containment check that string prefixes (``lib`` vs ``lib-old``) do not.
        roots = sorted(
            repo_paths.items(),
            key=lambda item: len(item[1].parts),
            reverse=True,
        )
        for alias, repo_path in roots:
            try:
                target_abs.relative_to(repo_path)
            except ValueError:
                continue
            else:
                return alias
    except Exception:
        # A manifest reference that cannot be resolved to a path is the one
        # way this returns None without having compared anything, so it is
        # worth distinguishing from "resolved fine, matched no repo".
        _log.warning(
            "Could not resolve manifest reference %r relative to %s",
            relative_ref,
            source_repo_path,
            exc_info=True,
        )
    return None


def _path_dep(
    index: _ManifestIndex, alias: str, base: Path, ref: str, manifest: str, kind: str
) -> CrossRepoPackageDep | None:
    target = _resolve_target_repo(ref, base, index.repo_paths)
    if target and target != alias:
        return CrossRepoPackageDep(
            source_repo=alias, target_repo=target, source_manifest=manifest, kind=kind
        )
    return None


def _name_dep(
    index: _ManifestIndex,
    alias: str,
    ecosystem: str,
    name: str,
    version: str,
    manifest: str,
    kind: str,
) -> CrossRepoPackageDep | None:
    owner = index.owner(ecosystem, name)
    if owner is None or owner[0] == alias:
        return None
    return CrossRepoPackageDep(
        source_repo=alias,
        target_repo=owner[0],
        source_manifest=manifest,
        kind=kind,
        target_package=name,
        target_manifest=owner[1],
        requested_version=version,
        resolution_basis="unique_workspace_package_name",
    )


def _scan_npm(index: _ManifestIndex, alias: str, repo_path: Path) -> _Deps:
    """``file:`` paths and workspace globs into a sibling, and deps on a sibling's name."""
    for rel, data in index.npm_by_repo.get(alias, ()):
        base = (repo_path / rel).parent
        for dep_key in _NPM_DEP_SECTIONS:
            deps = data.get(dep_key, {})
            if not isinstance(deps, dict):
                continue
            for name, version in deps.items():
                if not isinstance(version, str):
                    continue
                if version.startswith("file:"):
                    yield _path_dep(index, alias, base, version[5:], rel, "npm_local_path")
                elif not version.startswith("workspace:"):
                    yield _name_dep(index, alias, "npm", name, version, rel, "npm_package")

        # Workspaces are globs; only those reaching outside the repo can name a sibling.
        for ws in read_workspaces_field(data):
            if ".." in ws:
                yield _path_dep(index, alias, base, ws.rstrip("/*"), rel, "npm_workspace")


def _scan_composer(index: _ManifestIndex, alias: str, repo_path: Path) -> _Deps:
    """Path repositories into a sibling, and requires of a sibling's package name."""
    for manifest in index.composer_by_repo.get(alias, ()):
        rel = _composer_path(manifest)
        base = repo_path / manifest.rel_dir
        for url in manifest.path_repositories:
            yield _path_dep(index, alias, base, url, rel, "composer_path")
        for name, version in manifest.requires.items():
            yield _name_dep(index, alias, "composer", name, version, rel, "composer_package")


def _load_toml(path: Path) -> dict:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _scan_pyproject(index: _ManifestIndex, alias: str, repo_path: Path) -> _Deps:
    """Poetry and PEP 621 path dependencies."""
    toml_path = repo_path / "pyproject.toml"
    if not toml_path.is_file():
        return
    data = _load_toml(toml_path)
    poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    specs = list(poetry_deps.values()) if isinstance(poetry_deps, dict) else []
    # PEP 621 dependencies with path (uncommon but possible via tool configs)
    for group_key in ("dependencies", "optional-dependencies"):
        group = data.get("project", {}).get(group_key, {})
        if isinstance(group, dict):
            specs.extend(group.values())
    for spec in specs:
        if isinstance(spec, dict) and "path" in spec:
            yield _path_dep(index, alias, repo_path, spec["path"], "pyproject.toml", "pip_path")


def _scan_cargo(index: _ManifestIndex, alias: str, repo_path: Path) -> _Deps:
    """Cargo path dependencies."""
    cargo_path = repo_path / "Cargo.toml"
    if not cargo_path.is_file():
        return
    data = _load_toml(cargo_path)
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        for spec in data.get(section, {}).values():
            if isinstance(spec, dict) and "path" in spec:
                yield _path_dep(index, alias, repo_path, spec["path"], "Cargo.toml", "cargo_path")


def _scan_go_mod(index: _ManifestIndex, alias: str, repo_path: Path) -> _Deps:
    """``replace`` directives pointing at a sibling repo."""
    go_mod = repo_path / "go.mod"
    if not go_mod.is_file():
        return
    try:
        content = go_mod.read_text(encoding="utf-8")
    except Exception:
        return
    in_replace_block = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("replace ("):
            in_replace_block = True
            continue
        if in_replace_block and stripped == ")":
            in_replace_block = False
            continue
        if stripped.startswith("replace ") or in_replace_block:
            parts = stripped.replace("replace ", "").split("=>")
            if len(parts) == 2:
                target_path = parts[1].strip().split()[0]  # first token after =>
                if target_path.startswith("..") or target_path.startswith("./"):
                    yield _path_dep(index, alias, repo_path, target_path, "go.mod", "go_replace")


def _scan_csproj(
    repo_path: Path,
    repo_paths: dict[str, Path],
    alias: str,
    *,
    csproj_index: _ManifestIndex | None = None,
) -> list[CrossRepoPackageDep]:
    """Scan every .csproj for cross-repo references.

    Two patterns are recognised:

    1. ``<ProjectReference Include="..\\..\\OtherRepo\\Foo.csproj"/>``: a
       relative path that resolves into a sibling indexed repo. Emits
       ``kind="dotnet_project_ref"``.

    2. ``<PackageReference Include="MyOrg.SharedLib"/>`` whose package id
       matches a sibling repo's ``<AssemblyName>`` or ``.csproj`` filename.
       Emits ``kind="dotnet_nuget_internal"``: the "internal NuGet
       feed" pattern enterprise teams use when their shared libs live
       in a separate repo.

    Skips ``bin``/``obj``/``packages``/``.vs``/``TestResults`` build outputs.

    ``csproj_index`` lets a caller scanning several repos share one walk;
    omitted, the scan builds its own and behaves exactly as a standalone call.
    """
    index = csproj_index if csproj_index is not None else _index_manifests(repo_paths)

    own = index.by_repo.get(alias)
    if own is None or repo_paths.get(alias) != repo_path:
        # The index does not describe this exact tree: *alias* is absent from
        # repo_paths, or the caller passed a repo_path that disagrees with it.
        # Walk the tree we were actually handed, and leave assembly_to_repo
        # alone: it is defined by repo_paths, and folding this repo's own
        # names into it would let them shadow a sibling that legitimately
        # owns the same assembly name.
        own, _poms, _composer = _walk_project_manifests(repo_path, index)

    assembly_to_repo = index.assembly_to_repo

    results: list[CrossRepoPackageDep] = []

    for csproj in own:
        tree = index.trees[csproj]
        try:
            rel_manifest = csproj.relative_to(repo_path).as_posix()
        except ValueError:
            rel_manifest = csproj.name

        for elem in tree.getroot().iter():
            tag = elem.tag.split("}", 1)[1] if elem.tag.startswith("{") else elem.tag
            include = elem.get("Include") if elem.attrib else None
            if not include:
                continue
            if tag == "ProjectReference":
                rel = include.replace("\\", "/")
                target = _resolve_target_repo(rel, csproj.parent, repo_paths)
                if target and target != alias:
                    results.append(
                        CrossRepoPackageDep(
                            source_repo=alias,
                            target_repo=target,
                            source_manifest=rel_manifest,
                            kind="dotnet_project_ref",
                        )
                    )
            elif tag == "PackageReference":
                pkg = include.strip()
                target = assembly_to_repo.get(pkg)
                if target and target != alias:
                    results.append(
                        CrossRepoPackageDep(
                            source_repo=alias,
                            target_repo=target,
                            source_manifest=rel_manifest,
                            kind="dotnet_nuget_internal",
                        )
                    )

    return results


def _scan_dotnet(index: _ManifestIndex, alias: str, repo_path: Path) -> list[CrossRepoPackageDep]:
    return _scan_csproj(repo_path, index.repo_paths, alias, csproj_index=index)


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

Scanner = Callable[[_ManifestIndex, str, Path], Iterable[CrossRepoPackageDep | None]]


@dataclass(frozen=True, slots=True)
class Ecosystem:
    name: str
    #: Files whose presence (next to source) marks a directory as a service root.
    markers: tuple[str, ...]
    #: Per-repo cross-repo edge scanner. Maven has none here: a coordinate only
    #: resolves against every repo's reactor at once, so it runs as one
    #: workspace pass after the table.
    scan: Scanner | None = None


ECOSYSTEMS: tuple[Ecosystem, ...] = (
    Ecosystem("npm", (PACKAGE_JSON,), _scan_npm),
    Ecosystem("composer", (COMPOSER_JSON,), _scan_composer),
    Ecosystem("python", ("pyproject.toml", "requirements.txt"), _scan_pyproject),
    Ecosystem("cargo", ("Cargo.toml",), _scan_cargo),
    Ecosystem("go", ("go.mod",), _scan_go_mod),
    Ecosystem("dotnet", (), _scan_dotnet),
    Ecosystem("maven", ("pom.xml",)),
    Ecosystem("gradle", ("build.gradle", "build.gradle.kts")),
    Ecosystem("mix", ("mix.exs",)),
    Ecosystem("container", ("Dockerfile",)),
)

SERVICE_MARKERS: frozenset[str] = frozenset(m for eco in ECOSYSTEMS for m in eco.markers)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _dep_key(dep: CrossRepoPackageDep) -> tuple[str, str, str, str, str]:
    return (dep.source_repo, dep.target_repo, dep.source_manifest, dep.kind, dep.target_package)


def detect_package_dependencies(
    repo_paths: dict[str, Path],
) -> list[CrossRepoPackageDep]:
    """Scan all repos for manifest-based cross-repo dependencies."""
    dependencies, _diagnostics, _total_diagnostics = detect_package_dependencies_with_diagnostics(
        repo_paths
    )
    return dependencies


def detect_package_dependencies_with_diagnostics(
    repo_paths: dict[str, Path],
) -> tuple[
    list[CrossRepoPackageDep],
    list[CrossRepoPackageDiagnostic],
    int,
]:
    """Scan package manifests and retain bounded Maven non-match evidence."""

    results: dict[tuple[str, str, str, str, str], CrossRepoPackageDep] = {}
    resolved_repo_paths = {alias: path.resolve() for alias, path in repo_paths.items()}
    index = _index_manifests(resolved_repo_paths)

    for alias, path in resolved_repo_paths.items():
        for eco in ECOSYSTEMS:
            if eco.scan is None:
                continue
            for dep in eco.scan(index, alias, path):
                if dep is not None:
                    results.setdefault(_dep_key(dep), dep)

    from .maven_dependencies import detect_maven_dependencies

    maven_links, maven_diagnostics = detect_maven_dependencies(
        resolved_repo_paths,
        index.poms_by_repo,
    )
    for link in maven_links:
        dep = CrossRepoPackageDep(
            source_repo=link.source_repo,
            target_repo=link.target_repo,
            source_manifest=link.source_manifest,
            kind="maven_coordinate",
            target_package=link.target_package,
            target_manifest=link.target_manifest,
            requested_version=link.requested_version,
            scope=link.scope,
            resolution_basis=link.resolution_basis,
        )
        results.setdefault(_dep_key(dep), dep)

    dependencies = sorted(results.values(), key=_dep_key)
    diagnostics = sorted(
        (
            CrossRepoPackageDiagnostic(
                repo=item.repo,
                source_manifest=item.manifest,
                code=item.code,
                detail=item.detail,
            )
            for item in maven_diagnostics
        ),
        key=lambda item: (
            item.code == "external_coordinate",
            item.repo,
            item.source_manifest,
            item.code,
            item.detail,
        ),
    )
    total_diagnostics = len(diagnostics)
    return dependencies, diagnostics[:_MAX_PACKAGE_DIAGNOSTICS], total_diagnostics
