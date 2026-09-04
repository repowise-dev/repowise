"""Regex import extraction for QML.

Captured forms:

    import QtQuick 2.15
    import QtQuick.Controls 2.15
    import org.kde.kirigami as K
    import "components"
    import "js/app.js" as AppScript
    import "../shared"

Two shapes with different resolution targets:

1. **Module imports**: unquoted dotted identifiers (`QtQuick`,
   `QtQuick.Controls`, `org.kde.kirigami`). These name a QML module, which
   is a *directory* holding a ``qmldir`` manifest (or the Qt builtins).
   The resolver maps dotted module names to local `qmldir`-declared
   modules; anything unresolved is external.
2. **Directory / script imports**: quoted strings (`"components"`,
   `"js/app.js"`, `"../shared"`). These are relative references resolved
   against the importing file's directory, the same shape as the HTML
   asset resolver handles. ``import "js/app.js" as S`` is a file dependency
   with the extension present; `"components"` and `"../shared"` reference
   a directory whose ``qmldir`` declares the module.

Comments are stripped before matching. QML uses ``//`` line comments and
``/* ... */`` block comments, and a quoted import inside a comment must
never mint an edge.
"""

from __future__ import annotations

import re

from ..models import Import

# A dotted QML module name: identifier segments joined by dots.
_MODULE = r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*"

# `import <Module.Identifier> [<version>] [as Alias]`
_IMPORT_MODULE_RE = re.compile(r"^import[ \t]+(" + _MODULE + r")", re.M)
# `import "<relative/path>" [as Alias]`
_IMPORT_PATH_RE = re.compile(r'^import[ \t]+"([^"]+)"', re.M)


def _strip_comments(text: str) -> str:
    """Blank out QML comments so they can't produce false import edges.

    Removes ``//`` line comments and ``/* ... */`` block comments
    (QML's block comments do not nest), replacing their characters with
    spaces while preserving newlines so ^-anchored matching still sees
    real statements at their original positions.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_block = False
    while i < n:
        pair = text[i : i + 2]
        if in_block:
            if pair == "*/":
                in_block = False
                out.append("  ")
                i += 2
            else:
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
        elif pair == "/*":
            in_block = True
            out.append("  ")
            i += 2
        elif pair == "//":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def extract_qml_imports(text: str) -> list[Import]:
    text = _strip_comments(text)
    imports: list[Import] = []
    seen: set[str] = set()

    for match in _IMPORT_MODULE_RE.finditer(text):
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

    for match in _IMPORT_PATH_RE.finditer(text):
        rel = match.group(1).strip()
        if rel in seen:
            continue
        seen.add(rel)
        imports.append(
            Import(
                raw_statement=match.group(0).strip(),
                # Keep the quoted form: the resolver distinguishes a
                # relative reference from a module import by the opening
                # quote.
                module_path=f'"{rel}"',
                imported_names=[],
                is_relative=True,
                resolved_file=None,
            )
        )

    return imports
