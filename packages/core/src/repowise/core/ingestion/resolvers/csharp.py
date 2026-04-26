"""C# import resolution."""

from __future__ import annotations

from .context import ResolverContext


def resolve_csharp_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a C# using directive to a repo-relative file path."""
    parts = module_path.split(".")
    local = parts[-1]

    # Try stem lookup on the last namespace component
    result = ctx.stem_lookup(local.lower())
    if result and result.endswith(".cs"):
        return result

    # Try matching namespace components as directory path
    if len(parts) > 1:
        dir_suffix = "/".join(parts)
        for p in ctx.path_set:
            if p.endswith(".cs") and dir_suffix.lower() in p.lower():
                return p

    return ctx.add_external_node(module_path)
