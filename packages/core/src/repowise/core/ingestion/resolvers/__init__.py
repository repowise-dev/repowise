"""Per-language import resolution dispatch."""

from __future__ import annotations

from collections.abc import Callable

from .context import ResolverContext
from .cpp import resolve_cpp_import
from .generic import resolve_generic_import
from .go import resolve_go_import
from .python import resolve_python_import
from .rust import resolve_rust_import
from .typescript import resolve_ts_js_import

ResolverFn = Callable[[str, str, ResolverContext], str | None]

_RESOLVERS: dict[str, ResolverFn] = {
    "python": resolve_python_import,
    "typescript": resolve_ts_js_import,
    "javascript": resolve_ts_js_import,
    "go": resolve_go_import,
    "rust": resolve_rust_import,
    "cpp": resolve_cpp_import,
    "c": resolve_cpp_import,
}


def resolve_import(
    module_path: str,
    importer_path: str,
    language: str,
    ctx: ResolverContext,
) -> str | None:
    """Dispatch to the appropriate language resolver, or fall back to generic."""
    if not module_path:
        return None
    resolver = _RESOLVERS.get(language, resolve_generic_import)
    return resolver(module_path, importer_path, ctx)


__all__ = [
    "ResolverContext",
    "resolve_import",
]
