"""Import-only extraction for languages the symbol pipeline does not parse.

Languages with no ``LanguageConfig`` get their import statements extracted
here. Most are ``import_support="partial"``, a dedicated resolver with a
major known gap, usually a dialect the regex cannot read. ``godot_resource``
is the exception at ``"full"``, on the terms ``languages/spec.py`` sets for
that value: its ini dialect has no variant left unread, and a ``res://`` path
is absolute, so resolution is exact rather than a rank-and-guess. Its gaps
are narrow and named in ``specs/godot_resource.py``, not the
44%-of-real-files kind that keeps ``html`` at ``"partial"``.

The parser consults :func:`extract_lightweight_imports` on its
no-``LanguageConfig`` path, so these files keep an empty symbol list — this
tier claims no symbol knowledge — but carry real :class:`~..models.Import`
entries that flow through the standard resolver dispatch.

That empty-symbols contract, not the parsing technique, is what defines the
tier. Most members here have no tree-sitter grammar at all and match with
per-language regexes over the raw text. ``html`` is the exception: repowise
already ships ``tree-sitter-html`` for Vue, so its extractor uses the real
grammar. It stays here because what it produces is the same — imports, no
symbols.
"""

from __future__ import annotations

from collections.abc import Callable

from ..models import FileInfo, Import
from .clojure import extract_clojure_imports
from .dart import extract_dart_imports
from .elixir import extract_elixir_imports
from .erlang import extract_erlang_imports
from .fsharp import extract_fsharp_imports
from .godot import extract_godot_imports
from .haskell import extract_haskell_imports
from .html import extract_html_imports
from .lean import extract_lean_imports
from .qml import extract_qml_imports
from .sql import extract_dbt_imports

ExtractorFn = Callable[[str], list[Import]]

_EXTRACTORS: dict[str, ExtractorFn] = {
    "elixir": extract_elixir_imports,
    "dart": extract_dart_imports,
    "clojure": extract_clojure_imports,
    "haskell": extract_haskell_imports,
    "lean": extract_lean_imports,
    # `import QtQuick` / `import "components"`: module specs and quoted
    # relative references (see qml.py).
    "qml": extract_qml_imports,
    "erlang": extract_erlang_imports,
    "fsharp": extract_fsharp_imports,
    # dbt {{ ref() }} / {{ source() }}, the only import system .sql files
    # have; plain SQL contains neither form, so this is a no-op outside dbt.
    "sql": extract_dbt_imports,
    # <script src> / <link href>. AST-backed rather than regex — see above.
    "html": extract_html_imports,
    # [ext_resource path=...] in .tscn/.tres/.escn, plus project.godot's
    # [autoload] table and run/main_scene. These are how a Godot project
    # reaches its .gd files -- scripts are attached to scenes, not imported.
    "godot_resource": extract_godot_imports,
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
