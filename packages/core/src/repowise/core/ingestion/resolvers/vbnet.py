"""VB.NET import resolution.

``Imports`` directives name a namespace, or occasionally a type. Resolution
runs through the same ``DotNetProjectIndex`` C# uses, so a .vbproj (or a
mixed .sln) gives project-scoped answers:

1. Look the namespace up in the project index, ranked same project first,
   then a directly-referenced project, then anywhere in the repo. VB.NET
   namespaces are recorded under the project's ``<RootNamespace>`` as well
   as their source-literal form.
2. Fall back to the type map for the ``Imports MyApp.Models.Cart`` form,
   which names a type rather than a namespace.
3. If a ``<PackageReference>`` covers the namespace, register a NuGet node.
4. Stem and dotted-path-suffix match, but only for a file no project owns:
   that is all a loose collection of .vb files can offer, and inside a
   project the namespace map has already answered authoritatively.
5. Otherwise a generic external node so the reference stays visible in the
   graph (System.* and other BCL namespaces land here).
"""

from __future__ import annotations

from .context import ResolverContext
from .csharp import (
    _matches_package_prefix,
    _repo_root_resolved,
    _resolve_importer,
    _to_repo_relative,
)
from .dotnet import get_or_build_index


def _legacy_stem_resolve(module_path: str, ctx: ResolverContext) -> str | None:
    """Stem then dotted-path-suffix match, for repos with no project file."""
    parts = module_path.split(".")
    local = parts[-1]
    result = ctx.stem_lookup(local.lower())
    if result and result.endswith(".vb"):
        return result
    if len(parts) > 1:
        dir_suffix = "/".join(parts)
        for p in ctx.sorted_paths:
            if p.endswith(".vb") and dir_suffix.lower() in p.lower():
                return p
    return None


def resolve_vbnet_import(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    """Resolve a VB.NET ``Imports`` target to a repo-relative path or external key."""
    # ``Imports Global.MyApp.Data`` pins the lookup to the root of the
    # namespace tree; the prefix is not part of the name.
    if module_path.startswith("Global."):
        module_path = module_path[len("Global.") :]

    index = get_or_build_index(ctx)
    if index is None or not ctx.repo_path:
        legacy = _legacy_stem_resolve(module_path, ctx)
        return legacy if legacy else ctx.add_external_node(module_path)

    importer_abs = _resolve_importer(index, ctx.repo_path, importer_path)
    importer_vbproj = index.file_to_project.get(importer_abs)
    repo_root_resolved = _repo_root_resolved(index, ctx.repo_path)

    ordered = index.rank_namespace_candidates(module_path, importer_vbproj)
    if not ordered and "." in module_path:
        # ``Imports MyApp.Models.Cart`` names a type. Only trust the type map
        # when the repo also declares the enclosing namespace, or a BCL name
        # like System.Text.Json would bind to any local class called Json.
        parent_ns, type_name = module_path.rsplit(".", 1)
        if parent_ns in index.namespace_map:
            ordered = index.rank_type_candidates(type_name, importer_abs)
    for cand in ordered:
        rel = _to_repo_relative(cand, repo_root_resolved)
        if rel and rel in ctx.path_set:
            return rel

    if importer_vbproj is not None:
        pkgs = index.package_refs.get(importer_vbproj, set())
        if _matches_package_prefix(module_path, pkgs):
            return ctx.add_external_node(f"nuget:{module_path}")
        # A file inside a known project has an authoritative answer already:
        # the namespace map said no. Guessing by file stem past that turns a
        # namespace another project cannot see into a wrong edge.
        return ctx.add_external_node(module_path)

    legacy = _legacy_stem_resolve(module_path, ctx)
    if legacy:
        return legacy

    return ctx.add_external_node(module_path)
