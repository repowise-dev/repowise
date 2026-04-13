"""Swift import resolution."""

from __future__ import annotations

from pathlib import PurePosixPath

from .context import ResolverContext


def resolve_swift_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a Swift import to a repo-relative file path."""
    parts = module_path.split(".")
    local = parts[-1]

    # Try stem lookup
    result = ctx.stem_lookup(local.lower())
    if result and result.endswith(".swift"):
        return result

    # Look for directory matching module name
    for p in ctx.path_set:
        if p.endswith(".swift"):
            parent_name = PurePosixPath(p).parent.name.lower()
            if parent_name == module_path.lower():
                return p

    return ctx.add_external_node(module_path)
