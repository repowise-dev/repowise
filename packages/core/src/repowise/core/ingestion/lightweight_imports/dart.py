"""Regex import extraction for Dart.

Captured forms:

    import 'package:name/path.dart';        — package URI
    import 'relative/path.dart' as x;       — importer-relative
    import 'dart:async';                    — SDK library (dropped at resolve)
    export 'src/api.dart';                  — re-export edge (barrel)
    part 'impl.dart';                       — strong intra-library edge
    part of 'lib.dart';   /  part of my.lib — back-edge to the owning library

``part``/``part of`` are emitted as plain imports: the edge direction
(fragment ↔ library, both ways across the two files' own statements) is
exactly the coupling the graph wants.
"""

from __future__ import annotations

import re

from ..models import Import

_URI_DIRECTIVE_RE = re.compile(
    r"^[ \t]*(import|export|part)[ \t]+['\"]([^'\"]+)['\"]",
    re.M,
)
# `part of` with either a URI or a dotted library name.
_PART_OF_RE = re.compile(
    r"^[ \t]*part[ \t]+of[ \t]+(?:['\"]([^'\"]+)['\"]|([A-Za-z_][A-Za-z0-9_.]*))",
    re.M,
)


def extract_dart_imports(text: str) -> list[Import]:
    imports: list[Import] = []
    seen: set[str] = set()

    for match in _PART_OF_RE.finditer(text):
        uri, library_name = match.group(1), match.group(2)
        module = uri if uri else f"library:{library_name}"
        if module in seen:
            continue
        seen.add(module)
        imports.append(
            Import(
                raw_statement=match.group(0).strip(),
                module_path=module,
                imported_names=[],
                is_relative=bool(uri),
                resolved_file=None,
            )
        )

    for match in _URI_DIRECTIVE_RE.finditer(text):
        directive, uri = match.group(1), match.group(2)
        # `part of '…'` already handled above; the URI regex would re-match
        # a plain `part` head, never the two-word form, so no overlap.
        if uri in seen:
            continue
        seen.add(uri)
        imports.append(
            Import(
                raw_statement=match.group(0).strip(),
                module_path=uri,
                imported_names=[],
                is_relative=not uri.startswith(("package:", "dart:")),
                resolved_file=None,
                is_reexport=directive == "export",
            )
        )
    return imports
