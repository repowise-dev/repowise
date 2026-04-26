"""Per-language heritage (inheritance/interface/trait) extraction.

Public API:

- ``extract_heritage`` — language-dispatching entry point invoked by the
  parser.
- ``HERITAGE_EXTRACTORS`` — language → extractor function map. Exposed
  for tests and tooling that need direct access.
- ``heritage_node_types_for`` — returns the AST node types that can
  carry heritage info for a given language (driven by ``LanguageSpec``).

Each language lives in its own module under this package; add a new
language by creating ``<lang>.py`` with an ``_extract_<lang>_heritage``
function and registering it in ``HERITAGE_EXTRACTORS`` below.
"""

from __future__ import annotations

from collections.abc import Callable

from ...languages.registry import REGISTRY as _LANG_REGISTRY
from ...models import HeritageRelation
from ..helpers import node_text
from .cpp import _extract_cpp_heritage
from .csharp import _extract_csharp_heritage
from .go import _extract_go_heritage
from .java import _extract_java_heritage
from .kotlin import _extract_kotlin_heritage
from .php import _extract_php_heritage
from .python import _extract_python_heritage
from .ruby import _extract_ruby_heritage
from .rust import _extract_rust_heritage
from .scala import _extract_scala_heritage
from .swift import _extract_swift_heritage
from .ts_js import _extract_ts_js_heritage


def heritage_node_types_for(lang: str) -> frozenset[str]:
    """Return the set of AST node types that can carry heritage info for *lang*."""
    spec = _LANG_REGISTRY.get(lang)
    return spec.heritage_node_types if spec else frozenset()


HERITAGE_EXTRACTORS: dict[str, Callable[..., None]] = {
    "python": _extract_python_heritage,
    "typescript": _extract_ts_js_heritage,
    "javascript": _extract_ts_js_heritage,
    "java": _extract_java_heritage,
    "go": _extract_go_heritage,
    "rust": _extract_rust_heritage,
    "cpp": _extract_cpp_heritage,
    "c": lambda *_: None,
    "kotlin": _extract_kotlin_heritage,
    "ruby": _extract_ruby_heritage,
    "csharp": _extract_csharp_heritage,
    "swift": _extract_swift_heritage,
    "scala": _extract_scala_heritage,
    "php": _extract_php_heritage,
}


def extract_heritage(
    tree: object,
    query: object,
    config: object,
    file_info: object,
    src: str,
    *,
    run_query: Callable,
) -> list[HeritageRelation]:
    """Extract inheritance/implementation relationships from class definitions.

    Walks the same @symbol.def captures used by _extract_symbols, extracting
    superclass/interface/trait information from the definition AST nodes.
    """
    if query is None:
        return []

    lang = file_info.language  # type: ignore[attr-defined]
    heritage_types = heritage_node_types_for(lang)
    if not heritage_types:
        return []

    from ...language_data import get_builtin_parents

    _parent_builtins = get_builtin_parents(lang)

    relations: list[HeritageRelation] = []
    seen: set[tuple[int, str]] = set()

    for capture_dict in run_query(query, tree.root_node):  # type: ignore[attr-defined]
        def_nodes = capture_dict.get("symbol.def", [])
        name_nodes = capture_dict.get("symbol.name", [])

        if not def_nodes or not name_nodes:
            continue

        def_node = def_nodes[0]
        if def_node.type not in heritage_types:
            continue

        name = node_text(name_nodes[0], src)
        if not name:
            continue

        line = def_node.start_point[0] + 1
        dedup_key = (line, name)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        extractor = HERITAGE_EXTRACTORS.get(lang)
        if extractor:
            extractor(def_node, name, line, src, relations)

    if _parent_builtins:
        relations = [r for r in relations if r.parent_name not in _parent_builtins]

    return relations


__all__ = ["HERITAGE_EXTRACTORS", "extract_heritage", "heritage_node_types_for"]
