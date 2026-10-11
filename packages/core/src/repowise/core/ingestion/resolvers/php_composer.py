"""PSR-4 lookup for PHP import resolution, built from the shared composer reader.

Reading ``composer.json`` itself is :mod:`..composer`'s job; this module only
turns the manifests into the autoload map and answers class-name lookups.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

from ..composer import repo_composer_manifests

if TYPE_CHECKING:
    from .context import ResolverContext


def get_or_build_psr4_map(ctx: ResolverContext) -> dict[str, list[str]]:
    """``{namespace_prefix: [repo-relative dir, ...]}`` over every first-party manifest.

    The root manifest comes first, so its directories are probed first when a
    nested package declares the same prefix.
    """
    cached = getattr(ctx, "_php_psr4_map", None)
    if cached is not None:
        return cached
    psr4: dict[str, list[str]] = {}
    for manifest in repo_composer_manifests(ctx):
        for prefix, dirs in manifest.psr4:
            known = psr4.setdefault(prefix, [])
            known.extend(d for d in dirs if d not in known)
    ctx._php_psr4_map = psr4  # type: ignore[attr-defined]
    return psr4


def _claims(ctx: ResolverContext) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
    """Namespace prefixes the repo autoloads (None: every namespace), and classmap dirs.

    A ``""`` PSR-4 or PSR-0 prefix is composer's catch-all, so it claims every
    namespace. Classmap directories claim whatever classes their files declare,
    which no prefix can express, so they are returned for a path check instead.
    """
    cached = getattr(ctx, "_php_claims", None)
    if cached is None:
        prefixes: list[str] | None = []
        classmap: list[str] = []
        for manifest in repo_composer_manifests(ctx):
            classmap.extend(manifest.classmap)
            for prefix, _dirs in (*manifest.psr4, *manifest.psr0):
                if not prefix:
                    prefixes = None
                elif prefixes is not None:
                    prefixes.append(prefix)
        cached = (None if prefixes is None else tuple(prefixes), tuple(classmap))
        ctx._php_claims = cached  # type: ignore[attr-defined]
    return cached


def claims_namespace(module_path: str, ctx: ResolverContext) -> bool:
    """Whether a first-party autoload prefix covers *module_path*."""
    prefixes, _classmap = _claims(ctx)
    if prefixes is None:
        return True
    fqn = module_path.replace("/", "\\")
    return any(fqn.startswith(prefix) for prefix in prefixes)


def in_classmap(path: str, ctx: ResolverContext) -> bool:
    """Whether *path* sits under a directory some manifest classmaps."""
    _prefixes, classmap = _claims(ctx)
    return any(not d or path == d or path.startswith(f"{d}/") for d in classmap)


def basename_index(ctx: ResolverContext) -> dict[str, list[str]]:
    """``{file basename: [paths, sorted]}`` for the PHP files in the index, cached on *ctx*."""
    cached = getattr(ctx, "_php_basename_index", None)
    if cached is None:
        cached = {}
        for p in ctx.sorted_paths:
            if p.endswith(".php"):
                cached.setdefault(posixpath.basename(p), []).append(p)
        ctx._php_basename_index = cached  # type: ignore[attr-defined]
    return cached


def resolve_via_psr4(module_path: str, ctx: ResolverContext) -> str | None:
    """Try PSR-4 prefix matching from composer.json. Returns repo-relative
    path or None.

    *module_path* is the FQN as written in PHP (``Foo\\Bar\\Baz`` form). The
    answer does not depend on the importing file, so it is memoized on *ctx*.
    """
    psr4 = get_or_build_psr4_map(ctx)
    if not psr4:
        return None
    memo: dict[str, str | None] | None = getattr(ctx, "_php_psr4_memo", None)
    if memo is None:
        memo = {}
        ctx._php_psr4_memo = memo  # type: ignore[attr-defined]
    elif module_path in memo:
        return memo[module_path]
    fqn = module_path.replace("/", "\\")
    # Longest prefix first, then shorter ones, as composer's own class loader
    # does: ``Illuminate\\Support\\`` may list four directories and still leave
    # ``Str`` to the ``Illuminate\\`` entry. Composer prefixes end in ``\\``.
    by_prefix = [
        [f"{base_dir}/{tail}.php" if base_dir else f"{tail}.php" for base_dir in psr4[prefix]]
        for prefix in sorted((p for p in psr4 if p and fqn.startswith(p)), key=len, reverse=True)
        if (tail := fqn[len(prefix) :].replace("\\", "/"))
    ]
    found = next((c for group in by_prefix for c in group if c in ctx.path_set), None)
    if found is None and by_prefix:
        # Tolerate paths whose first segments differ (some repos vendor the
        # root differently), for the longest prefix only: a shorter one yields
        # short tails that end too many unrelated paths.
        index = basename_index(ctx)
        found = next(
            (
                p
                for candidate in by_prefix[0]
                for p in index.get(posixpath.basename(candidate), ())
                if p.endswith(f"/{candidate}")
            ),
            None,
        )
    memo[module_path] = found
    return found
