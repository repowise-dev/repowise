"""TypeScript / JavaScript import resolution."""

from __future__ import annotations

import posixpath
from collections.abc import Iterable
from pathlib import Path

from ..languages.js_config_values import string_literals, value_spans
from .context import ResolverContext
from .lwc import lwc_bundle_dir, resolve_lwc_module
from .nuxt import resolve_nuxt_alias
from .subpath_imports import resolve_subpath_import
from .svelte_kit import resolve_lib_alias
from .ts_workspace import _vitest_glob_to_regex, resolve_via_workspaces

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


def _without_query(module_path: str, importer_path: str, ctx: ResolverContext) -> str:
    """The specifier without a bundler query: ``./kit.js?raw`` is ``./kit.js``.

    Vite takes ``?raw``, ``?url``, ``?inline`` or ``?worker`` (and webpack a
    resource query) after a module path to load the same file another way, as
    text, a URL or a worker; the file imported is still the one before the
    ``?``, as the Astro and HTML resolvers already read it. A test that imports
    the script it runs as text then pairs with that script. A ``#`` stays: it
    starts a Node subpath import.

    Webpack's inline loaders come before the path, each ending in ``!``
    (``raw-loader!./kit.js``, ``!!style-loader!css-loader?modules!./a.css``):
    the file is what follows the last ``!``, and is cut out first because a
    loader may carry a query of its own. A ``!`` is also legal in a file
    name, so a specifier that names a file in the repository as written is
    kept whole, whether relative (``./components/Oops!.tsx``) or through an
    alias, subpath import or workspace package (``@/components/Oops!.tsx``).
    A loader given by a relative path (``./my-loader!./kit.js``) names no
    file as written, and is cut.
    """
    _, bang, resource = module_path.rpartition("!")
    if bang and resource:
        whole = _strip_query(module_path)
        if _resolve_in_repo(whole, importer_path, ctx) is None:
            module_path = resource
    return _strip_query(module_path)


def _strip_query(module_path: str) -> str:
    head, sep, _ = module_path.partition("?")
    return head if sep and head else module_path


def _resolve_relative(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a relative specifier (``./x``, ``../x``) to a file in the repository."""
    importer_dir = Path(importer_path).parent
    # Join + normalize via posixpath so ``../../helper/html`` collapses
    # against the importer's directory. ``pathlib`` doesn't normalize
    # ``..`` segments unless the path exists on disk; without this
    # step, every cross-directory relative import silently fails to
    # resolve and reads as an external dep.
    base_posix = posixpath.normpath(posixpath.join(importer_dir.as_posix(), module_path))
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


def resolve_ts_js_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a TypeScript or JavaScript import to a repo-relative file path."""
    module_path = _without_query(module_path, importer_path, ctx)
    resolved = _resolve_in_repo(module_path, importer_path, ctx)
    if resolved is not None or module_path.startswith("."):
        return resolved

    # Fallback: external npm package.
    return ctx.add_external_node(module_path)


def _resolve_in_repo(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """The file in the repository a specifier names, or ``None`` when it names none."""
    if module_path.startswith("."):
        return _resolve_relative(module_path, importer_path, ctx)

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

    # Nuxt's ``~/`` / ``@/`` aliases, declared only in the generated and
    # gitignored ``.nuxt/tsconfig.json``. After tsconfig, so paths a repo does
    # check in keep the last word.
    nuxt_resolved = resolve_nuxt_alias(module_path, importer_path, ctx)
    if nuxt_resolved is not None:
        return nuxt_resolved

    # Workspace package (npm/yarn/pnpm ``workspaces``): turn ``@myorg/foo``
    # into the sibling package's entry point rather than an external node.
    return resolve_via_workspaces(module_path, ctx)


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
    module_path = _without_query(module_path, importer_path, ctx)
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


_GLOB_CHARS = frozenset("*?[{")


def resolve_ts_js_glob(
    patterns: tuple[str, ...], importer_path: str, ctx: ResolverContext
) -> tuple[str, ...]:
    """Every indexed file a Vite ``import.meta.glob`` call loads, sorted.

    Follows Vite's reading of each pattern: ``./`` and ``../`` from the
    importer's directory, ``**`` anywhere in the repository, anything else
    through the same aliases an import takes (tsconfig ``paths``, Nuxt ``~/``,
    SvelteKit ``$lib``, ``#`` subpath imports, workspace packages). A ``!``
    pattern removes what it matches. Vite never loads the importer itself.

    Only files in ``ctx.path_set`` match, so excluded and unindexed files get
    no edge. A ``/``-rooted pattern is read against the Vite root: the nearest
    ``vite.config.*`` directory (a literal ``root:`` in it honoured), else the
    nearest ``package.json`` directory, else the repository root.
    """
    matched: set[str] = set()
    removed: set[str] = set()
    for pattern in patterns:
        negated = pattern.startswith("!")
        files = _glob_files(pattern[1:] if negated else pattern, importer_path, ctx)
        (removed if negated else matched).update(files)
    matched -= removed
    matched.discard(importer_path)
    return tuple(sorted(matched))


def _glob_files(pattern: str, importer_path: str, ctx: ResolverContext) -> set[str]:
    if pattern.startswith("**"):
        return _regex_matches(pattern, ctx)
    if pattern.startswith(("./", "../")):
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(importer_path), pattern))
        return set() if joined.startswith("../") else _regex_matches(joined, ctx)
    if pattern.startswith("/"):
        # Vite reads a leading ``/`` against its own root: the directory of
        # the nearest ``vite.config.*``, else of the nearest ``package.json``,
        # else the repository root. A literal ``root:`` in the config moves it.
        joined = posixpath.normpath(posixpath.join(_vite_root(importer_path, ctx), pattern[1:]))
        return _regex_matches(joined, ctx)
    if not pattern:
        return set()
    return _alias_glob_files(pattern, importer_path, ctx)


_VITE_CONFIG_NAMES: tuple[str, ...] = (
    "vite.config.ts",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.mts",
    "vite.config.cjs",
    "vite.config.cts",
)


def _vite_root(importer_path: str, ctx: ResolverContext) -> str:
    """The directory a ``/``-rooted Vite glob is relative to, posix and repo-relative.

    Vite's root is the nearest ``vite.config.*``'s directory (a literal
    ``root:`` in it overrides, still relative to the config), else the nearest
    ``package.json``'s, else the repository root. A config is found on disk as
    well as in the index: the traverser does not index every config file.
    """
    config_dir, config_rel = _nearest_config(_VITE_CONFIG_NAMES, importer_path, ctx)
    if not config_rel:
        package_dir, package_rel = _nearest_config(("package.json",), importer_path, ctx)
        return package_dir if package_rel else ""
    root = _literal_root_value(config_rel, ctx)
    if not root:
        return config_dir
    if root.startswith("/"):
        return posixpath.normpath(root)
    return posixpath.normpath(posixpath.join(config_dir, root))


def _literal_root_value(config_rel: str, ctx: ResolverContext) -> str:
    """The literal ``root:`` string in a Vite config, or "" when absent."""
    text = _config_text(config_rel, ctx)
    if not text:
        return ""
    spans = value_spans(text, ("root",))
    if not spans:
        return ""
    literals = string_literals(spans[0])
    if not literals:
        return ""
    return literals[0].strip()


def _nearest_config(
    names: tuple[str, ...], importer_path: str, ctx: ResolverContext
) -> tuple[str, str]:
    """The nearest of *names* at or above the importer's directory.

    Returns ``(directory, repo-relative path)``; ``("", "")`` when absent.
    The indexed set is checked first, then the disk, so a config the
    traverser skipped is still found.
    """
    directory = posixpath.dirname(importer_path)
    while True:
        for name in names:
            rel = posixpath.join(directory, name)
            if rel in ctx.path_set:
                return directory, rel
            if ctx.repo_path is not None and (ctx.repo_path / rel).is_file():
                return directory, rel
        if not directory:
            return "", ""
        directory = posixpath.dirname(directory)


def _config_text(config_rel: str, ctx: ResolverContext) -> bytes | None:
    if ctx.source_map is not None:
        text = ctx.source_map.get(config_rel)
        if text is not None:
            return text
    if ctx.repo_path is not None:
        path = ctx.repo_path / config_rel
        try:
            return path.read_bytes()
        except OSError:
            return None
    return None


def _regex_matches(repo_glob: str, ctx: ResolverContext) -> set[str]:
    regex = _vitest_glob_to_regex(repo_glob)
    return {path for path in ctx.path_set if regex.match(path)}


def _alias_glob_files(pattern: str, importer_path: str, ctx: ResolverContext) -> set[str]:
    """Files an alias-rooted glob (``@/pages/*.vue``) matches.

    The alias part is the static directory before the first wildcard. A file
    matches when its path ends in a part the wildcard tail matches and the
    importer, writing ``<static dir>/<that part>``, resolves to it, so every
    alias an import understands applies, with nothing re-implemented here.
    """
    first_wild = min((i for i, c in enumerate(pattern) if c in _GLOB_CHARS), default=-1)
    if first_wild < 0:
        hit = _resolve_in_repo(pattern, importer_path, ctx)
        return {hit} if hit else set()
    static, slash, _ = pattern[:first_wild].rpartition("/")
    if not slash:
        return set()
    tail = pattern[len(static) + 1 :]
    tail_regex = _vitest_glob_to_regex(tail)
    # A tail without ``**`` spans a fixed number of segments, so each path has
    # one candidate split; with ``**`` every split is tried.
    fixed = None if "**" in tail else tail.count("/") + 1
    hits: set[str] = set()
    for path in ctx.path_set:
        parts = path.split("/")
        starts = range(1, len(parts)) if fixed is None else (len(parts) - fixed,)
        for start in starts:
            if start < 1:
                continue
            rest = "/".join(parts[start:])
            if tail_regex.match(rest) and (
                _resolve_in_repo(f"{static}/{rest}", importer_path, ctx) == path
            ):
                hits.add(path)
                break
    return hits
