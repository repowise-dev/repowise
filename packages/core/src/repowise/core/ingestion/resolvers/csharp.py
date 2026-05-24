"""C# import resolution.

Resolution algorithm (in priority order):

1. Build (and cache) a ``DotNetProjectIndex`` for the repo. This parses
   every ``.csproj`` and ``.sln``, builds a namespace → file map, and
   collects implicit + global usings per project.

2. Locate the project enclosing the importer file. If unknown, fall back
   to the legacy stem-match resolver — that handles the loose collection
   of .cs files repos that have no .csproj at all.

3. Look up the ``using`` namespace in the namespace map:
       a. Prefer files inside the same project.
       b. Then files inside any directly-referenced project (ProjectReference).
       c. Otherwise pick the first match anywhere in the repo (rare —
          a .cs file outside any project).

4. If no match is found and the namespace prefix matches a declared
   ``<PackageReference>``, register an external NuGet node.

5. Final fallback: register a generic external node so the using is
   visible in the graph even if unresolvable.

The legacy stem-match path is preserved so repos without .csproj files
keep working.
"""

from __future__ import annotations

from pathlib import Path

from .context import ResolverContext
from .dotnet import get_or_build_index


def _to_repo_relative(abs_path: Path, repo_root_resolved: Path) -> str | None:
    """Return *abs_path* relative to a pre-resolved repo root in posix form.

    ``abs_path`` is expected to already be resolved — every value we pass
    in here comes out of ``DotNetProjectIndex.file_to_project`` /
    ``namespace_map``, both of which are keyed by resolved absolute paths.
    Re-resolving on every call (as the original implementation did) costs
    a stat-per-path-component on Windows and dominates ``graph.imports``
    wall-clock on large C# monorepos.
    """
    try:
        return abs_path.relative_to(repo_root_resolved).as_posix()
    except ValueError:
        return None


def _legacy_stem_resolve(
    module_path: str, ctx: ResolverContext
) -> str | None:
    """Original 26-line resolver — used when no project index is available."""
    parts = module_path.split(".")
    local = parts[-1]
    result = ctx.stem_lookup(local.lower())
    if result and result.endswith(".cs"):
        return result
    if len(parts) > 1:
        dir_suffix = "/".join(parts)
        for p in ctx.path_set:
            if p.endswith(".cs") and dir_suffix.lower() in p.lower():
                return p
    return None


_REPO_ROOT_RESOLVED_ATTR = "_repo_root_resolved"
_IMPORTER_RESOLVED_ATTR = "_importer_resolved_cache"


def _repo_root_resolved(index: object, repo_path: Path) -> Path:
    """Return ``repo_path.resolve()`` from a cache on the index.

    A C# monorepo can fire tens of thousands of ``resolve_csharp_import``
    calls; resolving the repo root each time accounts for a measurable
    chunk of ``graph.imports`` wall-clock on Windows.
    """
    cached = getattr(index, _REPO_ROOT_RESOLVED_ATTR, None)
    if cached is not None:
        return cached
    resolved = repo_path.resolve()
    setattr(index, _REPO_ROOT_RESOLVED_ATTR, resolved)
    return resolved


def _resolve_importer(index: object, repo_path: Path, importer_path: str) -> Path:
    """Cache importer-path → resolved absolute path on the index.

    Each .cs file has multiple ``using`` directives and each one triggers
    a resolve of the same importer. Memoising collapses that to one
    resolve per importer across the whole indexing run.
    """
    cache: dict[str, Path] | None = getattr(index, _IMPORTER_RESOLVED_ATTR, None)
    if cache is None:
        cache = {}
        setattr(index, _IMPORTER_RESOLVED_ATTR, cache)
    cached = cache.get(importer_path)
    if cached is not None:
        return cached
    resolved = (repo_path / importer_path).resolve()
    cache[importer_path] = resolved
    return resolved


def _matches_package_prefix(module_path: str, packages: set[str]) -> bool:
    """True if *module_path* equals or is a child namespace of any package id."""
    for pkg in packages:
        if module_path == pkg or module_path.startswith(pkg + "."):
            return True
    return False


def resolve_csharp_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    """Resolve a C# using directive to a repo-relative file path or external key."""
    index = get_or_build_index(ctx)
    if index is None or not ctx.repo_path:
        # No repo path — fall back to stem-match only.
        legacy = _legacy_stem_resolve(module_path, ctx)
        return legacy if legacy else ctx.add_external_node(module_path)

    # Locate the importer's project (if any). Both lookups below previously
    # ran ``.resolve()`` per call — on Windows that's a stat per path
    # component. The index resolved every .cs file once at build time, so
    # we cache the importer's resolved path on the index keyed by the
    # raw repo-relative string and reuse it across all of this file's
    # imports.
    importer_abs = _resolve_importer(index, ctx.repo_path, importer_path)
    importer_csproj = index.file_to_project.get(importer_abs)
    importer_proj = index.projects.get(importer_csproj) if importer_csproj else None

    candidates = index.files_for_namespace(module_path)
    repo_root_resolved = _repo_root_resolved(index, ctx.repo_path)

    if candidates:
        # Rank: same project, then referenced projects, then anywhere.
        same_project: list[Path] = []
        referenced: list[Path] = []
        other: list[Path] = []

        if importer_proj is not None:
            ref_csprojs = index.referenced_projects(importer_proj.path)
            for cand in candidates:
                cand_proj_path = index.file_to_project.get(cand)
                if cand_proj_path == importer_proj.path:
                    same_project.append(cand)
                elif cand_proj_path in ref_csprojs:
                    referenced.append(cand)
                else:
                    other.append(cand)
            ordered = same_project or referenced or other
        else:
            ordered = candidates

        chosen = ordered[0]
        rel = _to_repo_relative(chosen, repo_root_resolved)
        if rel and rel in ctx.path_set:
            return rel

    # No file declares this namespace — could be NuGet or a sibling project's
    # public API surface. If a package reference matches, mark external NuGet.
    if importer_proj is not None:
        pkgs = index.package_refs.get(importer_proj.path, set())
        if _matches_package_prefix(module_path, pkgs):
            return ctx.add_external_node(f"nuget:{module_path}")

    # Last resort: legacy stem-match (catches repos with no .csproj).
    legacy = _legacy_stem_resolve(module_path, ctx)
    if legacy:
        return legacy

    return ctx.add_external_node(module_path)
