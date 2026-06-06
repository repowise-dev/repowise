"""Regex import extraction for F#.

Captured form:

    open Foo.Bar
    open type Foo.Bar.Baz

``open`` names a namespace or module — F#'s only textual cross-file
reference. The compile-order dependency spine (fsproj ``<Compile
Include>`` order) is project-file data, not source text, and is emitted
by a dedicated graph pass instead.
"""

from __future__ import annotations

import re

from ..models import Import

_OPEN_RE = re.compile(r"^[ \t]*open[ \t]+(?:type[ \t]+)?([A-Z][A-Za-z0-9_.]*)", re.M)


def extract_fsharp_imports(text: str) -> list[Import]:
    imports: list[Import] = []
    seen: set[str] = set()
    for match in _OPEN_RE.finditer(text):
        module = match.group(1)
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
