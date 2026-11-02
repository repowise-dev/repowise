"""TypeScript / JavaScript import resolution."""

from __future__ import annotations

import posixpath
from collections.abc import Iterable
from pathlib import Path

from .context import ResolverContext
from .lwc import lwc_bundle_dir, resolve_lwc_module
from .subpath_imports import resolve_subpath_import
from .svelte_kit import resolve_lib_alias
from .ts_workspace import resolve_via_workspaces

# Metro (React Native) platform extensions, in sorted order so the candidates
# generated from them are deterministic.
PLATFORM_SEGMENTS: tuple[str, ...] = ("android", "ios", "native", "web")
_PLATFORM_FILE_EXTS: tuple[str, ...] = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx")
_PLATFORM_SUFFIXES: tuple[str, ...] = tuple(
    f".{platform}{ext}" for platform in PLATFORM_SEGMENTS for ext in _PLATFORM_FILE_EXTS
)


def detect_platform_files(paths: Iterable[str]) -> bool:
    """True when any path is a Metro platform file (``Button.ios.tsx``).

    Computed once where the :class:`ResolverContext` is built, like
    ``has_sfc_files``, so a repository without platform files never pays for
    (or is changed by) the platform probing in :func:`resolve_ts_js_import_all`.
    """
    return any(p.endswith(_PLATFORM_SUFFIXES) for p in paths)


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

    # LWC ``c/fooBar``, only from inside an ``lwc/<bundle>/`` folder.
    if lwc_bundle_dir(importer_path) is not None:
        lwc_resolved = resolve_lwc_module(module_path, importer_path, ctx)
        if lwc_resolved is not None:
            return lwc_resolved

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


def resolve_ts_js_import_all(
    module_path: str, importer_path: str, ctx: ResolverContext
) -> tuple[str, ...]:
    """Resolve a TS/JS import to every file it can mean, plain file first.

    Metro resolves ``./Button`` to ``Button.ios.tsx`` on iOS and
    ``Button.android.tsx`` on Android, and ``./haptics`` to ``haptics.native.ts``
    ahead of ``haptics.ts``. The importer depends on all of them, so each gets
    an edge. The result is the single-target resolution (if any) followed by the
    platform files in sorted order, which keeps ``imp.resolved_file`` the plain
    file whenever there is one.

    Only relative specifiers in a repository that has platform files are
    expanded (``ctx.has_platform_files``); everything else is exactly
    :func:`resolve_ts_js_import`.
    """
    single = resolve_ts_js_import(module_path, importer_path, ctx)
    targets: list[str] = [single] if single else []
    if not ctx.has_platform_files or not module_path.startswith("."):
        return tuple(targets)

    base_posix = posixpath.normpath(
        posixpath.join(Path(importer_path).parent.as_posix(), module_path)
    )
    platform_targets: set[str] = set()
    for platform in PLATFORM_SEGMENTS:
        for ext in _PLATFORM_FILE_EXTS:
            for stem in (base_posix, f"{base_posix}/index"):
                candidate = f"{stem}.{platform}{ext}"
                if candidate in ctx.path_set:
                    platform_targets.add(candidate)
    platform_targets.discard(single or "")
    targets.extend(sorted(platform_targets))
    return tuple(targets)
