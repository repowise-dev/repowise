"""PHP import resolution."""

from __future__ import annotations

import posixpath

from .context import ResolverContext
from .php_composer import (
    basename_index,
    claims_namespace,
    get_or_build_psr4_map,
    in_classmap,
    resolve_via_psr4,
)


def resolve_php_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a PHP use declaration or require/include to a repo-relative path."""
    # File-based require/include: ``require 'lib/helpers.php'`` /
    # ``require __DIR__ . '/inc/db.php'`` (the captured literal keeps the
    # leading slash from the concatenation — importer-relative either way).
    # Probe importer-relative first, then repo-root-relative; no fuzzy
    # fallback — a literal path that matches nothing is external.
    if module_path.endswith(".php"):
        literal = module_path.replace("\\", "/").lstrip("/")
        importer_dir = posixpath.dirname(importer_path)
        candidate = posixpath.normpath(posixpath.join(importer_dir, literal))
        if candidate in ctx.path_set:
            return candidate
        root_candidate = posixpath.normpath(literal)
        if root_candidate in ctx.path_set:
            return root_candidate
        return ctx.add_external_node(module_path)

    # composer.json autoload.psr-4 is the authoritative mapping in real
    # Laravel/Symfony/etc. apps; consult before stem fallback so non-conventional
    # prefix maps (``"App\\": "src/"``) resolve correctly.
    psr4_match = resolve_via_psr4(module_path, ctx)
    if psr4_match is not None:
        return psr4_match
    # A namespaced class no autoload prefix covers is a dependency's (it lives
    # in vendor/); a name match would bind ``Illuminate\Support\Facades\Auth``
    # to an unrelated local ``config/auth.php``. Only a classmapped file may
    # still claim it.
    unclaimed = (
        "\\" in module_path.strip("\\")
        and bool(get_or_build_psr4_map(ctx))
        and not claims_namespace(module_path, ctx)
    )

    local = module_path.replace("\\", "/").rsplit("/", 1)[-1]
    result = ctx.stem_lookup(local.lower())
    if not (result and result.endswith(".php")):
        # The stem map may rank a same-named non-PHP file first.
        result = next(iter(basename_index(ctx).get(f"{local}.php", ())), None)
    if result and (not unclaimed or in_classmap(result, ctx)):
        return result
    return ctx.add_external_node(module_path)
