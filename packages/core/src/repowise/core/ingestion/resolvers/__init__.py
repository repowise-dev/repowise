"""Per-language import resolution dispatch."""

from __future__ import annotations

from collections.abc import Callable

from .context import ResolverContext
from .cpp import resolve_cpp_import
from .csharp import resolve_csharp_import
from .generic import resolve_generic_import
from .go import resolve_go_import
from .kotlin import resolve_kotlin_import
from .php import resolve_php_import
from .python import resolve_python_import
from .ruby import resolve_ruby_import
from .rust import resolve_rust_import
from .scala import resolve_scala_import
from .swift import resolve_swift_import
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
    "kotlin": resolve_kotlin_import,
    "ruby": resolve_ruby_import,
    "csharp": resolve_csharp_import,
    "swift": resolve_swift_import,
    "scala": resolve_scala_import,
    "php": resolve_php_import,
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
