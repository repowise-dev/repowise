"""Ruby import resolution."""

from __future__ import annotations

from pathlib import PurePosixPath

from .context import ResolverContext


def resolve_ruby_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a Ruby require/require_relative to a repo-relative file path."""
    # require_relative uses paths relative to the current file
    if module_path.startswith("."):
        importer_dir = PurePosixPath(importer_path).parent
        candidate = (importer_dir / module_path).as_posix()
        # Try with .rb extension
        for suffix in (".rb", ""):
            full = f"{candidate}{suffix}"
            if full in ctx.path_set:
                return full

    # Try stem lookup
    stem = PurePosixPath(module_path).stem.lower().replace("-", "_")
    result = ctx.stem_lookup(stem)
    if result and result.endswith(".rb"):
        return result

    # Try matching the path directly
    rb_name = f"{module_path}.rb"
    for p in ctx.path_set:
        if p.endswith(rb_name) or PurePosixPath(p).name == PurePosixPath(rb_name).name:
            return p

    return ctx.add_external_node(module_path)
