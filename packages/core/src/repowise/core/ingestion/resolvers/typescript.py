"""TypeScript / JavaScript import resolution."""

from __future__ import annotations

import posixpath
from pathlib import Path

from .context import ResolverContext
from .ts_workspace import resolve_via_workspaces


def resolve_ts_js_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a TypeScript or JavaScript import to a repo-relative file path."""
    importer_dir = Path(importer_path).parent

    if module_path.startswith("."):
        # Join + normalize via posixpath so ``../../helper/html`` collapses
        # against the importer's directory. ``pathlib`` doesn't normalize
        # ``..`` segments unless the path exists on disk; without this
        # step, every cross-directory relative import silently fails to
        # resolve and reads as an external dep.
        base_posix = posixpath.normpath(
            posixpath.join(importer_dir.as_posix(), module_path)
        )
        exts: tuple[str, ...] = (
            ".ts",
            ".tsx",
            ".mts",
            ".cts",
            ".js",
            ".jsx",
            "/index.ts",
            "/index.mts",
            "/index.cts",
            "/index.js",
        )
        if ctx.has_sfc_files:
            exts = exts + (".vue", ".svelte", ".astro")
        for ext in exts:
            candidate = base_posix + ext
            if candidate in ctx.path_set:
                return candidate
        # Legacy fallback: ``.with_suffix``-style replacement only kicks
        # in when the specifier already carries a fake extension that
        # users intended to be stripped (``./foo.js`` resolving to
        # ``./foo.ts`` under TS rewrite rules). Guarded so it never
        # clobbers a real multi-dot stem (``foo.config``, ``site.meta``).
        stem_dot = base_posix.rfind(".")
        if stem_dot > base_posix.rfind("/"):
            real_stem = base_posix[:stem_dot]
            for ext in (".ts", ".tsx", ".mts", ".cts"):
                candidate = real_stem + ext
                if candidate in ctx.path_set:
                    return candidate
        return None

    # Non-relative: try tsconfig path-alias resolution first.
    if ctx.tsconfig_resolver is not None:
        importer_abs = str(ctx.repo_path / importer_path) if ctx.repo_path else importer_path
        alias_resolved = ctx.tsconfig_resolver.resolve(module_path, importer_abs)
        if alias_resolved is not None:
            return alias_resolved

    # Workspace package (npm/yarn/pnpm ``workspaces``): turn ``@myorg/foo``
    # into the sibling package's entry point rather than an external node.
    workspace_resolved = resolve_via_workspaces(module_path, ctx)
    if workspace_resolved is not None:
        return workspace_resolved

    # Fallback: external npm package.
    return ctx.add_external_node(module_path)
