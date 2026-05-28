"""Per-language synthetic-symbol passes.

Some frameworks rely on source generators or annotation processors that
emit symbols at compile time. Those generated symbols don't appear in
the AST of the user's source file, but they ARE referenced by name from
other code (XAML bindings, Lombok-injected constructors, Java record
accessors, Kotlin data-class ``copy``/``componentN``, MapStruct ``Impl``
classes, ...). Without representing them in the symbol table, every
binding to such a name looks like an unresolved reference and the
user-visible symbol that "backs" it looks orphaned.

This package dispatches per-language to source-specific providers. Each
provider takes ``(root, src, file_info)`` and returns extra ``Symbol``
records the parser appends to its main symbol list. Adding a new
generator's support is one new module plus one entry in
``_SYNTHETIC_EXTRACTORS``.

Public surface — preserved for back-compat:
    extract_synthetic_symbols(root, src, file_info) -> list[Symbol]
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ...models import FileInfo, Symbol
from .cpp_macros import cpp_macro_synthetic_symbols
from .csharp_mvvm import csharp_synthetic_symbols
from .java_records import java_record_synthetic_symbols
from .jvm_codegen import jvm_codegen_synthetic_symbols
from .kotlin_jvm import kotlin_synthetic_symbols
from .lombok import lombok_synthetic_symbols

if TYPE_CHECKING:
    from tree_sitter import Node


# A "provider" returns the synthesised symbols for one file. Multiple
# providers may run for a single language — the dispatcher concatenates
# them and dedupes by symbol id at the parser level.
_Provider = Callable[["Node", str, FileInfo], list[Symbol]]

_SYNTHETIC_PROVIDERS: dict[str, list[_Provider]] = {
    "csharp": [csharp_synthetic_symbols],
    "java": [
        lombok_synthetic_symbols,
        java_record_synthetic_symbols,
        jvm_codegen_synthetic_symbols,
    ],
    "kotlin": [kotlin_synthetic_symbols],
    "cpp": [cpp_macro_synthetic_symbols],
    "c": [cpp_macro_synthetic_symbols],
}


def extract_synthetic_symbols(
    root: "Node", src: str, file_info: FileInfo
) -> list[Symbol]:
    """Dispatch to the language-appropriate synthetic-symbol providers.

    Returns an empty list for languages with no registered provider.
    """
    providers = _SYNTHETIC_PROVIDERS.get(file_info.language)
    if not providers:
        return []
    out: list[Symbol] = []
    for provider in providers:
        out.extend(provider(root, src, file_info))
    return out
