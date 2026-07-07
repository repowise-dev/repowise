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

# A dotted module path. Each component starts with a Unicode letter or
# underscore ("[^\W\d]" is a word char that is not a digit) and continues with
# word characters or a prime ("'"), matching Lean/Mathlib identifiers that use
# Unicode. Components are joined by dots, and a trailing dot is never captured
# because each dot must be followed by another component.
_MODULE = r"[^\W\d][\w']*(?:\.[^\W\d][\w']*)*"

_IMPORT_RE = re.compile(
    r"^(?:(?:public|private|meta)[ \t]+)?import[ \t]+(?:all[ \t]+)?"
    r"(" + _MODULE + r")"
    r"|^open[ \t]+(?:scoped[ \t]+)?(" + _MODULE + r")",
    re.M,
)


def _strip_comments(text: str) -> str:
    """Blank out Lean comments so they can't produce false import edges.

    Removes ``--`` line comments and nested ``/- ... -/`` block comments,
    replacing their characters with spaces (newlines kept). Preserving the
    line and column layout means the ``^``-anchored import regex still sees
    real statements at their original positions.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    depth = 0  # nesting depth of /- -/ block comments
    while i < n:
        pair = text[i : i + 2]
        if depth > 0:
            if pair == "/-":
                depth += 1
                out.append("  ")
                i += 2
            elif pair == "-/":
                depth -= 1
                out.append("  ")
                i += 2
            else:
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
        elif pair == "/-":
            depth += 1
            out.append("  ")
            i += 2
        elif pair == "--":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def extract_lean_imports(text: str) -> list[Import]:
    text = _strip_comments(text)
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
