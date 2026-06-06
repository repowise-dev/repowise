"""Regex-tier import extraction for languages without a tree-sitter grammar.

Languages whose ``LanguageSpec.import_support`` is ``"partial"`` via the
lightweight-resolver mechanism get their import statements extracted here
with per-language regexes over the raw source text — no AST. The parser
consults :func:`extract_lightweight_imports` on its no-grammar path, so
these files keep an empty symbol list (the regex tier claims no symbol
knowledge) but carry real :class:`~..models.Import` entries that flow
through the standard resolver dispatch.
"""

from __future__ import annotations

from collections.abc import Callable

from ..models import FileInfo, Import
from .clojure import extract_clojure_imports
from .dart import extract_dart_imports
from .elixir import extract_elixir_imports
from .erlang import extract_erlang_imports
from .fsharp import extract_fsharp_imports
from .haskell import extract_haskell_imports

ExtractorFn = Callable[[str], list[Import]]

_EXTRACTORS: dict[str, ExtractorFn] = {
    "elixir": extract_elixir_imports,
    "dart": extract_dart_imports,
    "clojure": extract_clojure_imports,
    "haskell": extract_haskell_imports,
    "erlang": extract_erlang_imports,
    "fsharp": extract_fsharp_imports,
}

LIGHTWEIGHT_IMPORT_LANGUAGES = frozenset(_EXTRACTORS)


def extract_lightweight_imports(file_info: FileInfo, source: bytes) -> list[Import]:
    """Return regex-extracted imports for *file_info*, or [] for other languages."""
    extractor = _EXTRACTORS.get(file_info.language)
    if extractor is None:
        return []
    text = source.decode("utf-8", errors="replace")
    return extractor(text)


__all__ = [
    "LIGHTWEIGHT_IMPORT_LANGUAGES",
    "extract_lightweight_imports",
]
