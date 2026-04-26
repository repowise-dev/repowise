"""PHP import resolution."""

from __future__ import annotations

from .context import ResolverContext
from .php_composer import resolve_via_psr4


def resolve_php_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a PHP use declaration to a repo-relative file path."""
    # composer.json autoload.psr-4 is the authoritative mapping in real
    # Laravel/Symfony/etc. apps; consult before stem fallback so non-conventional
    # prefix maps (``"App\\": "src/"``) resolve correctly.
    psr4_match = resolve_via_psr4(module_path, ctx)
    if psr4_match is not None:
        return psr4_match

    # Convert namespace separators to path separators
    path_form = module_path.replace("\\", "/")
    parts = path_form.split("/")
    local = parts[-1]

    # Try stem lookup on the class name
    result = ctx.stem_lookup(local.lower())
    if result and result.endswith(".php"):
        return result

    # Try PSR-4 style: namespace path maps to directory
    php_name = f"{local}.php"
    for p in ctx.path_set:
        if p.endswith(php_name):
            return p

    return ctx.add_external_node(module_path)
