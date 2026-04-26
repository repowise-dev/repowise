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


def _to_repo_relative(abs_path: Path, repo_path: Path) -> str | None:
    """Return *abs_path* relative to *repo_path* in posix form, or None."""
    try:
        return abs_path.resolve().relative_to(repo_path.resolve()).as_posix()
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

    # Locate the importer's project (if any).
    importer_abs = (ctx.repo_path / importer_path).resolve()
    importer_proj = index.project_for_file(importer_abs)

    candidates = index.files_for_namespace(module_path)

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
        rel = _to_repo_relative(chosen, ctx.repo_path)
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
