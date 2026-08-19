"""TypeScript / JavaScript import resolution."""

from __future__ import annotations

import posixpath
from pathlib import Path

from .context import ResolverContext
from .subpath_imports import resolve_subpath_import
from .svelte_kit import resolve_lib_alias
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
        if base_posix in ctx.path_set:
            return base_posix
        exts: tuple[str, ...] = (
            ".ts",
            ".tsx",
            ".mts",
            ".cts",
            ".js",
            ".jsx",
            "/index.ts",
            "/index.tsx",
            "/index.mts",
            "/index.cts",
            "/index.js",
            "/index.jsx",
        )
        if ctx.has_sfc_files:
            # Both the bare file (``./Foo`` -> ``Foo.vue``) and the directory
            # index (``./components/TodoList`` -> ``.../TodoList/index.vue``).
            # The index forms were missing here while ``/index.ts`` and
            # ``/index.js`` were present, so a directory-index component
            # resolved to nothing and read as unreachable — the same gap
            # ``subpath_imports`` already handles via ``/index.svelte``.
            exts = (
                *exts,
                ".vue",
                ".svelte",
                ".astro",
                "/index.vue",
                "/index.svelte",
                "/index.astro",
            )
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

    # Node.js subpath imports (``#lib/...`` via package.json "imports").
    subpath_resolved = resolve_subpath_import(module_path, importer_path, ctx)
    if subpath_resolved is not None:
        return subpath_resolved

    # SvelteKit's $lib alias. Checked before tsconfig because the tsconfig
    # that declares it (.svelte-kit/tsconfig.json) is generated and gitignored,
    # so it is virtually never present in an indexed checkout.
    lib_resolved = resolve_lib_alias(module_path, importer_path, ctx)
    if lib_resolved is not None:
        return lib_resolved

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
