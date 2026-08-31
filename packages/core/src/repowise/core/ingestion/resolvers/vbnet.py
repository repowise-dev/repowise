"""VB.NET import resolution.

``Imports`` directives name namespaces (rarely a dotted type). Resolution:

1. Stem match on the last segment (``Imports MyApp.Models`` → the file whose
   namespace/class-stem matches, or whose *path* suffix matches the dotted
   tail), mirroring the C# legacy stem resolver — the shape that works for
   repos with and without .vbproj files.
2. Path-suffix match on the dotted form (``MyApp.Models`` → any ``.vb``
   whose path ends ``/MyApp/Models.vb``).
3. Otherwise register a generic external node so the reference stays visible
   in the graph (System.* and other BCL namespaces land here).
"""

from __future__ import annotations

from .context import ResolverContext


def _legacy_stem_resolve(module_path: str, ctx: ResolverContext) -> str | None:
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
    """Resolve a VB.NET ``Imports`` namespace to a repo-relative file path or external key."""
    legacy = _legacy_stem_resolve(module_path, ctx)
    if legacy:
        return legacy
    return ctx.add_external_node(module_path)
