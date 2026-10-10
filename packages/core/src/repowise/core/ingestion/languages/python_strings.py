"""Module-path strings in source text, and the part of a file that runs.

Python names code by dotted string as often as by ``import`` (entry-point
tables, plugin registries, lazy command tables). Both the dynamic-import hints
(graph edges) and dead-code analysis (finding filters) read those strings, so
the "only strings that run" filter and the "module defines this name" check
live here once.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import PurePosixPath

from .registry import REGISTRY


def is_python(path: str) -> bool:
    return REGISTRY.from_extension(PurePosixPath(path).suffix) == "python"


def defines_top_level(blob: bytes, name: str) -> bool:
    """Whether a Python module defines *name* at top level (def, class, assignment).

    A re-export (``from x import Name``) or a tuple target is not seen, so such
    a member stays reported.
    """
    word = re.escape(name.encode("ascii"))
    pattern = (
        rb"^(?:(?:async[ \t]+)?def|class)[ \t]+" + word + rb"\b"
        rb"|^" + word + rb"[ \t]*(?::[^=\n]*)?=(?!=)"
    )
    return re.search(pattern, blob, re.MULTILINE) is not None


#: Tokens after which a string starts an expression statement (a docstring).
_STATEMENT_START = frozenset(
    {tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING}
)
_TRIVIA = frozenset({tokenize.NL, tokenize.COMMENT})
_COMMENT_LINE_RE = re.compile(rb"^[ \t]*#.*$", re.MULTILINE)


def live_text(path: str, blob: bytes) -> bytes:
    """*blob* without comments and, for Python, without docstrings.

    Python keeps only its string literals that are not a statement on their
    own; other files drop only ``#`` comment lines. Python that does not tokenize
    is kept whole, as before this filter.
    """
    if not is_python(path):
        return _COMMENT_LINE_RE.sub(b"", blob)
    try:
        tokens = [
            t for t in tokenize.tokenize(io.BytesIO(blob).readline) if t.type not in _TRIVIA
        ]
    except (tokenize.TokenError, SyntaxError, ValueError):
        return blob
    return b"\n".join(
        tok.string.encode("utf-8")
        for i, tok in enumerate(tokens)
        if tok.type == tokenize.STRING and not _is_statement(tokens, i)
    )


def _is_statement(tokens: list[tokenize.TokenInfo], i: int) -> bool:
    """Whether ``tokens[i]`` is a statement on its own (a docstring)."""
    # ENDMARKER always closes the stream, so ``i + 1`` exists.
    if tokens[i - 1].type not in _STATEMENT_START:
        return False
    return tokens[i + 1].type in (tokenize.NEWLINE, tokenize.ENDMARKER)
