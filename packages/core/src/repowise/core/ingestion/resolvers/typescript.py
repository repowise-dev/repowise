"""TypeScript / JavaScript import resolution."""

from __future__ import annotations

from pathlib import Path

from .context import ResolverContext


def resolve_ts_js_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a TypeScript or JavaScript import to a repo-relative file path."""
    importer_dir = Path(importer_path).parent

    if module_path.startswith("."):
        base = importer_dir / module_path
        for ext in (".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js"):
            candidate = Path(str(base) + ext).as_posix()
            if candidate in ctx.path_set:
                return candidate
            candidate = (
                base.with_suffix(ext).as_posix()
                if not ext.startswith("/")
                else (base / "index.ts").as_posix()
            )
            if candidate in ctx.path_set:
                return candidate
        return None

    # Non-relative: try tsconfig path-alias resolution first.
    if ctx.tsconfig_resolver is not None:
        importer_abs = str(ctx.repo_path / importer_path) if ctx.repo_path else importer_path
        alias_resolved = ctx.tsconfig_resolver.resolve(module_path, importer_abs)
        if alias_resolved is not None:
            return alias_resolved

    # Fallback: external npm package.
    return ctx.add_external_node(module_path)
