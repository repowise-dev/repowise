"""Scala import resolution."""

from __future__ import annotations

from .context import ResolverContext


def resolve_scala_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a Scala import to a repo-relative file path."""
    parts = module_path.split(".")
    local = parts[-1]

    if local in ("*", "_"):
        return None

    # Try stem lookup on the last name component
    result = ctx.stem_lookup(local.lower())
    if result and result.endswith(".scala"):
        return result

    # Try matching package path as directory structure
    if len(parts) > 1:
        dir_suffix = "/".join(parts[:-1])
        for p in ctx.path_set:
            if p.endswith(".scala") and dir_suffix.lower() in p.lower():
                stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                if stem.lower() == local.lower():
                    return p

    return ctx.add_external_node(module_path)
