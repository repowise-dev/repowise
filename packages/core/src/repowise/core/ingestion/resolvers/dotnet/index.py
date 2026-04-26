"""Repo-scoped .NET project index — built once per resolver run, cached on the context."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from .global_usings import collect_project_global_usings
from .msbuild import MSBuildProject, find_csproj_files, find_directory_build_props, parse_csproj
from .namespace_map import build_namespace_map
from .solution import find_sln_files, parse_sln

if TYPE_CHECKING:
    from ..context import ResolverContext

log = structlog.get_logger(__name__)


@dataclass
class DotNetProjectIndex:
    """Cached view of every .NET project in a single repo."""

    repo_path: Path
    projects: dict[Path, MSBuildProject] = field(default_factory=dict)
    """Keyed by absolute .csproj path."""

    namespace_map: dict[str, list[Path]] = field(default_factory=dict)
    """Maps a fully-qualified namespace to the set of .cs files declaring it."""

    project_globals: dict[Path, set[str]] = field(default_factory=dict)
    """Maps a project's directory → global+implicit using namespaces."""

    file_to_project: dict[Path, Path] = field(default_factory=dict)
    """Maps a .cs file's absolute path → enclosing project's .csproj path."""

    project_refs_by_proj: dict[Path, set[Path]] = field(default_factory=dict)
    """Maps a .csproj path → set of referenced .csproj paths (transitive=False)."""

    package_refs: dict[Path, set[str]] = field(default_factory=dict)
    """Maps a .csproj path → declared NuGet package ids."""

    sln_paths: list[Path] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def project_for_file(self, file_abs: Path) -> MSBuildProject | None:
        """Return the project enclosing *file_abs*, or None."""
        csproj = self.file_to_project.get(file_abs.resolve())
        return self.projects.get(csproj) if csproj else None

    def referenced_projects(self, csproj: Path) -> set[Path]:
        """Return the direct ProjectReference set for *csproj*."""
        return self.project_refs_by_proj.get(csproj, set())

    def files_for_namespace(self, ns: str) -> list[Path]:
        return self.namespace_map.get(ns, [])

    def globals_for_project(self, csproj: Path) -> set[str]:
        proj = self.projects.get(csproj)
        if not proj:
            return set()
        return self.project_globals.get(proj.project_dir, set())

    def package_for(self, csproj: Path, package_id: str) -> bool:
        return package_id in self.package_refs.get(csproj, set())


def _gather_project_files(project_dir: Path) -> list[Path]:
    """Return every .cs file under *project_dir*, ignoring build outputs."""
    skip = {"bin", "obj", ".vs", "node_modules"}
    out: list[Path] = []
    for cs in project_dir.rglob("*.cs"):
        if any(part in skip for part in cs.parts):
            continue
        out.append(cs.resolve())
    return out


def build_index(repo_path: Path) -> DotNetProjectIndex:
    """Walk *repo_path* and construct a fully-populated DotNetProjectIndex."""
    repo_path = repo_path.resolve()
    index = DotNetProjectIndex(repo_path=repo_path)

    # ---- 1. Parse every .csproj ----
    for csproj_path in find_csproj_files(repo_path):
        proj = parse_csproj(csproj_path)
        if proj is None:
            continue
        index.projects[proj.path] = proj
        index.project_refs_by_proj[proj.path] = set(proj.project_references)
        index.package_refs[proj.path] = set(proj.package_references)

    # ---- 2. Walk .sln files (informational; surfaces orphaned .csprojs) ----
    index.sln_paths = find_sln_files(repo_path)
    for sln in index.sln_paths:
        for entry in parse_sln(sln):
            if entry.csproj not in index.projects:
                # Solution references a .csproj we didn't pick up — try parsing it.
                proj = parse_csproj(entry.csproj)
                if proj is not None:
                    index.projects[proj.path] = proj
                    index.project_refs_by_proj.setdefault(proj.path, set()).update(
                        proj.project_references
                    )
                    index.package_refs.setdefault(proj.path, set()).update(proj.package_references)

    # ---- 3. Build namespace map across every .cs file in every project ----
    all_cs_files: list[Path] = []
    for proj in index.projects.values():
        proj_files = _gather_project_files(proj.project_dir)
        all_cs_files.extend(proj_files)
        for f in proj_files:
            index.file_to_project[f] = proj.path
    index.namespace_map = build_namespace_map(all_cs_files)

    # ---- 4. Compute per-project global+implicit usings ----
    for proj in index.projects.values():
        # Heuristic: presence of any AspNetCore PackageReference flags the
        # web SDK's expanded implicit-using set.
        sdk_is_web = any(
            pkg.startswith("Microsoft.AspNetCore") for pkg in proj.package_references
        )
        globals_set = collect_project_global_usings(
            proj.project_dir, proj.implicit_usings, sdk_is_web=sdk_is_web
        )
        # Honour <Using Include="X"/> ItemGroup entries on top of file scans.
        globals_set.update(proj.project_usings)
        index.project_globals[proj.project_dir] = globals_set

    log.info(
        "DotNetProjectIndex built",
        repo=str(repo_path),
        projects=len(index.projects),
        namespaces=len(index.namespace_map),
        sln=len(index.sln_paths),
    )
    return index


# Stash key on the ResolverContext (reuses a generic cache slot to avoid
# adding a typed field for every language plugin).
_INDEX_KEY = "_dotnet_index"


def get_or_build_index(ctx: "ResolverContext") -> DotNetProjectIndex | None:
    """Return the cached DotNetProjectIndex, building it on first access."""
    if not ctx.repo_path:
        return None
    cached = getattr(ctx, _INDEX_KEY, None)
    if cached is not None:
        return cached
    index = build_index(ctx.repo_path)
    setattr(ctx, _INDEX_KEY, index)
    return index
