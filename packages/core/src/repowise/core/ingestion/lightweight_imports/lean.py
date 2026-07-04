"""Regex import extraction for Lean 4.

Captured forms:

    import Foo.Bar
    import all Foo.Bar
    public import Foo.Bar
    private import Foo.Bar
    meta import Foo.Bar
    open Foo.Bar
    open scoped Foo
    open Foo.Bar Baz.Qux

For ``open`` statements, only the first dotted namespace is captured. The file
graph only needs a module target, matching the minimal one-edge-per-statement
shape used by the Haskell extractor.
"""

from __future__ import annotations

import re

from ..models import Import

_IMPORT_RE = re.compile(
    r"^(?:(?:public|private|meta)[ \t]+)?import[ \t]+(?:all[ \t]+)?"
    r"([A-Za-z_][A-Za-z0-9_'.]*)"
    r"|^open[ \t]+(?:scoped[ \t]+)?([A-Za-z_][A-Za-z0-9_'.]*)",
    re.M,
)


def extract_lean_imports(text: str) -> list[Import]:
    imports: list[Import] = []
    seen: set[str] = set()
    for match in _IMPORT_RE.finditer(text):
        module = match.group(1) or match.group(2)
        if module in seen:
            continue
        seen.add(module)
        imports.append(
            Import(
                raw_statement=match.group(0).strip(),
                module_path=module,
                imported_names=[],
                is_relative=False,
                resolved_file=None,
            )
        )
    return imports
