"""Live source reads behind every symbol slice get_answer serves.

One bounded disk read per file, and the two views taken of it: a symbol's body
at its indexed bounds, and its full declaration line as written.
"""

from __future__ import annotations

import re
from pathlib import Path

from repowise.server.mcp_server.tool_answer.config import (
    _MATCHED_SYMBOL_SOURCE_LINES,
    _MAX_RICH_SIG_LINES,
)


def _read_repo_text(repo_root: Path | None, file_path: str) -> str | None:
    """Read a repo file's live text, refusing paths outside the root.

    The single disk read shared by the bounds gate and the signature/body
    slices below, so a hydrated file is read once rather than once per helper.
    """
    if repo_root is None:
        return None
    try:
        abs_path = (repo_root / file_path).resolve()
        abs_path.relative_to(repo_root.resolve())
        return abs_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _read_symbol_source(
    repo_root: Path | None,
    file_path: str,
    start_line: int,
    end_line: int,
    max_lines: int = _MATCHED_SYMBOL_SOURCE_LINES,
    *,
    text: str | None = None,
) -> str | None:
    """Return the literal source body for a symbol, bounded to max_lines.

    The bounded source is the key ingredient for question-matched symbols.
    The LLM was already getting the file-level summary and a truncated
    docstring; what it was missing was the actual code. With 40 lines of
    the method body in front of it, the synthesis step can answer "how
    does X work" without hedging back to "you should inspect the source".

    ``text`` lets a caller that already read the file (the hydrator reads it
    once for the bounds gate) pass the live source in, so a hydrated file is
    read once instead of once per symbol.
    """
    if start_line < 1:
        return None
    if text is None:
        text = _read_repo_text(repo_root, file_path)
    if text is None:
        return None
    lines = text.splitlines()
    if start_line > len(lines):
        return None
    hi = end_line if end_line and end_line >= start_line else start_line + max_lines
    hi = min(hi, start_line + max_lines, len(lines))
    body = "\n".join(lines[start_line - 1 : hi])
    return body


# Where a signature stops when it does not end in a Python colon: after the
# closing paren of the parameter list (with an optional return annotation), at a
# trailing brace, or at a semicolon (an abstract/interface member).
_SIG_TERMINATOR_RE = re.compile(r"\)\s*(?:->[^:{]*)?\s*[:{]|\{\s*$|;\s*$")


def _read_signature_from_source(
    repo_root: Path | None, file_path: str, start_line: int, *, text: str | None = None
) -> str | None:
    """Read the symbol's actual signature line from disk.

    Returns the def/class line (or its multi-line continuation) verbatim from
    the source file. Captures everything WikiSymbol.signature strips:
      * base classes for `class Foo(Bar, Baz):`
      * decorators (one line above the def)
      * full type annotations across line continuations

    ``text`` reuses the caller's already-read source (see _read_symbol_source).
    None on any failure — caller falls back to the stored signature.
    """
    if text is None:
        text = _read_repo_text(repo_root, file_path)
    if text is None:
        return None
    lines = text.splitlines()
    if not lines or start_line < 1 or start_line > len(lines):
        return None
    # Walk forward up to _MAX_RICH_SIG_LINES until we close the parenthesis
    # group (Python signatures often span multiple lines for type hints).
    sig_lines: list[str] = []
    paren_depth = 0
    for i in range(start_line - 1, min(start_line - 1 + _MAX_RICH_SIG_LINES, len(lines))):
        line = lines[i]
        sig_lines.append(line.strip())
        paren_depth += line.count("(") - line.count(")")
        stripped = line.rstrip()
        # "ends with a colon" alone leaves a one-line body (``def go(self): pass``)
        # and every brace language (``func f() error {``, ``render() {``) with no
        # terminator at all, so the signature absorbs the lines after it.
        if paren_depth <= 0 and (
            stripped.endswith(":") or _SIG_TERMINATOR_RE.search(stripped)
        ):
            break
    if not sig_lines:
        return None
    return " ".join(sig_lines)
