"""Rust import resolution."""

from __future__ import annotations

from pathlib import Path

from .context import ResolverContext


def resolve_rust_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a Rust ``use`` path to a repo-relative file."""
    parts = module_path.split("::")
    if not parts:
        return None

    prefix = parts[0]

    # --- crate:: — resolve from the crate root ---
    if prefix == "crate":
        crate_root = _find_rust_crate_root(importer_path, ctx)
        return _probe_rust_path(crate_root, parts[1:], ctx.path_set)

    # --- self:: — resolve from the current module's directory ---
    if prefix == "self":
        importer_dir = str(Path(importer_path).parent.as_posix())
        return _probe_rust_path(importer_dir, parts[1:], ctx.path_set)

    # --- super:: — resolve from the parent directory ---
    if prefix == "super":
        parent_dir = str(Path(importer_path).parent.parent.as_posix())
        return _probe_rust_path(parent_dir, parts[1:], ctx.path_set)

    # --- External crate (no prefix or unknown crate name) ---
    # Check if it might be a local module at the crate root first
    crate_root = _find_rust_crate_root(importer_path, ctx)
    resolved = _probe_rust_path(crate_root, parts, ctx.path_set)
    if resolved is not None:
        return resolved

    # Cargo workspace sibling crate: `use sibling_crate::...`
    from .rust_workspace import get_or_build_cargo_workspace_index

    ws_index = get_or_build_cargo_workspace_index(ctx)
    if ws_index is not None:
        sibling_src = ws_index.lookup(prefix)
        if sibling_src is not None and sibling_src != crate_root:
            resolved = _probe_rust_path(sibling_src, parts[1:], ctx.path_set)
            if resolved is None:
                # Probe the crate root itself (lib.rs / main.rs) when the
                # import has no further path segments.
                for root_file in ("lib.rs", "main.rs"):
                    candidate = f"{sibling_src}/{root_file}"
                    if candidate in ctx.path_set:
                        return candidate
            if resolved is not None:
                return resolved

    # External crate
    return ctx.add_external_node(module_path)


def _find_rust_crate_root(importer_path: str, ctx: ResolverContext) -> str:
    """Find the ``src/`` directory containing the importer (Rust crate root)."""
    parsed_files = ctx.parsed_files or {}
    parts = Path(importer_path).parts
    for i in range(len(parts) - 1, -1, -1):
        candidate_dir = Path(*parts[:i]) if i > 0 else Path(".")
        for root_file in ("lib.rs", "main.rs"):
            root_path = (candidate_dir / root_file).as_posix()
            if root_path in parsed_files:
                return candidate_dir.as_posix()
        if parts[i] == "src" and i > 0:
            return candidate_dir.as_posix()
    return Path(importer_path).parent.as_posix()


def _probe_rust_path(
    base_dir: str,
    path_parts: list[str],
    path_set: set[str],
) -> str | None:
    """Probe for a Rust module path, trying ``.rs`` and ``mod.rs`` variants."""
    if not path_parts:
        return None

    base = Path(base_dir)

    for depth in range(len(path_parts), 0, -1):
        module_parts = path_parts[:depth]
        module_dir = base
        for p in module_parts[:-1]:
            module_dir = module_dir / p

        last = module_parts[-1]
        # Try <dir>/<last>.rs
        candidate = (module_dir / f"{last}.rs").as_posix()
        if candidate in path_set:
            return candidate
        # Try <dir>/<last>/mod.rs
        candidate = (module_dir / last / "mod.rs").as_posix()
        if candidate in path_set:
            return candidate

    return None
