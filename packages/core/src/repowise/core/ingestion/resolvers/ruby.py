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

    # Rails / Zeitwerk autoloading: ``require 'app/services/foo'`` style
    # paths can also be resolved by walking the rails index's
    # ``namespace_to_file`` map. Most Rails constant references are
    # require-less and surface through ``ctx.rails_lookup`` from the call
    # resolver / heritage extractor, not this function.
    from .ruby_rails import get_or_build_rails_index

    rails_index = get_or_build_rails_index(ctx)
    if rails_index is not None and not module_path.startswith("."):
        # Strip leading autoload-root prefixes (``app/services/foo`` →
        # ``foo`` lookup against namespace_to_file).
        normalised = module_path
        for root in rails_index.autoload_roots:
            prefix = f"{root}/"
            if normalised.startswith(prefix):
                normalised = normalised[len(prefix) :]
                break
        hit = rails_index.namespace_to_file.get(normalised)
        if hit:
            return hit

    return ctx.add_external_node(module_path)
