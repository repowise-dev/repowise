"""Per-language import-binding extraction.

Public API:

- ``extract_import_bindings`` — language-dispatching entry point used by
  the parser. Returns ``(imported_names, bindings)`` where
  ``imported_names`` is the backward-compatible list of local names and
  ``bindings`` carries alias/source detail.
- Each per-language ``extract_<lang>_bindings`` is also re-exported so
  callers (e.g. tests) can drive a single language directly.

Add a new language by creating ``<lang>.py`` with an
``extract_<lang>_bindings`` function and wiring it into the
``_DISPATCH`` map below.
"""

from __future__ import annotations

from collections.abc import Callable

from tree_sitter import Node

from ...models import NamedBinding
from .cpp import extract_cpp_bindings
from .csharp import extract_csharp_bindings
from .go import extract_go_bindings
from .java import extract_java_bindings
from .kotlin import extract_kotlin_bindings
from .php import extract_php_bindings
from .python import extract_python_bindings
from .ruby import extract_ruby_bindings
from .rust import extract_rust_bindings
from .scala import extract_scala_bindings
from .swift import extract_swift_bindings
from .ts_js import extract_ts_js_bindings


_DISPATCH: dict[str, Callable[[Node, str], tuple[list[str], list[NamedBinding]]]] = {
    "python": extract_python_bindings,
    "typescript": extract_ts_js_bindings,
    "javascript": extract_ts_js_bindings,
    "go": extract_go_bindings,
    "rust": extract_rust_bindings,
    "java": extract_java_bindings,
    "cpp": extract_cpp_bindings,
    "c": extract_cpp_bindings,
    "kotlin": extract_kotlin_bindings,
    "ruby": extract_ruby_bindings,
    "csharp": extract_csharp_bindings,
    "swift": extract_swift_bindings,
    "scala": extract_scala_bindings,
    "php": extract_php_bindings,
}


def extract_import_bindings(
    stmt_node: Node, src: str, lang: str
) -> tuple[list[str], list[NamedBinding]]:
    """Extract imported names and structured bindings from an import statement."""
    extractor = _DISPATCH.get(lang)
    if extractor is None:
        return [], []
    return extractor(stmt_node, src)


__all__ = [
    "extract_cpp_bindings",
    "extract_csharp_bindings",
    "extract_go_bindings",
    "extract_import_bindings",
    "extract_java_bindings",
    "extract_kotlin_bindings",
    "extract_php_bindings",
    "extract_python_bindings",
    "extract_ruby_bindings",
    "extract_rust_bindings",
    "extract_scala_bindings",
    "extract_swift_bindings",
    "extract_ts_js_bindings",
]
