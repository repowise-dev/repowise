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

_OPEN_RE = re.compile(r"^[ \t]*open[ \t]+(type[ \t]+)?([A-Z][A-Za-z0-9_.]*)", re.M)


def extract_fsharp_imports(text: str) -> list[Import]:
    imports: list[Import] = []
    seen: set[str] = set()
    for match in _OPEN_RE.finditer(text):
        module = match.group(2)
        # ``open`` binds every public name in the module, which is what the
        # wildcard sentinel says. ``open type`` binds one type's static
        # members, so the dependency is on the path that holds the type.
        names = ["*"]
        if match.group(1):
            head, _, type_name = module.rpartition(".")
            if head:
                module, names = head, [type_name]
            else:
                names = []
        if module in seen:
            continue
        seen.add(module)
        imports.append(
            Import(
                raw_statement=match.group(0).strip(),
                module_path=module,
                imported_names=names,
                is_relative=False,
                resolved_file=None,
            )
        )
    return imports
