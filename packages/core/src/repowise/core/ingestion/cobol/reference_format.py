"""Fixed reference format: sequence area, indicator column and continuation lines.

Every function keeps the line count, so a line in the result is the same line
in the file.
"""

from __future__ import annotations

import re

# Columns 1-6 are the sequence area, 7 the indicator, 8-72 the program text.
_TEXT_START, _TEXT_END = 7, 72
_COMMENT_INDICATORS = frozenset("*/$Dd")
# Compiler options before IDENTIFICATION DIVISION (``PROCESS SQL``, ``CBL ...``).
_COMPILER_DIRECTIVE = re.compile(r"\s*(PROCESS|CBL)\b", re.IGNORECASE)
# The same when the card starts in column 1: the keyword must end at whitespace
# or the end of the line, so a data name such as ``CBL-REC`` stays a data name.
_COMPILER_DIRECTIVE_COLUMN_1 = re.compile(r"\s*(PROCESS|CBL)(?=\s|$)", re.IGNORECASE)
_FREE_FORMAT = re.compile(r">>\s*SOURCE\s+(FORMAT\s+(IS\s+)?)?FREE|SOURCEFORMAT\W+FREE", re.I)


def is_free_format(text: str) -> bool:
    """A ``>>SOURCE FORMAT FREE`` (or Micro Focus ``SOURCEFORMAT"FREE"``) directive."""
    return _FREE_FORMAT.search(text) is not None


def normalize_fixed_format(text: str) -> str:
    """Blank what the compiler ignores and close literals split by continuation lines.

    Comment and debug lines become blank, the sequence area and columns 73+
    become spaces, and a literal continued on a ``-`` line is closed at column
    72 with its continuation blanked: the grammar has no continuation rule.
    """
    out: list[str] = []
    open_quote = ""  # the quote of a literal still open at the end of the previous code line
    seen_division = False
    for raw in text.split("\n"):
        line, eol = (raw[:-1], "\r") if raw.endswith("\r") else (raw, "")
        indicator = line[6:7]
        body = line[_TEXT_START:_TEXT_END]
        if indicator in _COMMENT_INDICATORS or not body.strip():
            out.append(eol)
            continue
        if not seen_division and (
            _COMPILER_DIRECTIVE.match(body) or _COMPILER_DIRECTIVE_COLUMN_1.match(line)
        ):
            # A compiler-option card may also start in column 1, where its text
            # is outside the 8-72 program area (#4475).
            out.append(eol)
            continue
        seen_division = True
        if indicator == "-" and open_quote:
            body, open_quote = _continued_literal(body, open_quote)
        else:
            open_quote = _open_quote_at_end(body)
        if open_quote and indicator != "-":
            # Close the literal at column 72 so this line parses on its own.
            body = body.ljust(_TEXT_END - _TEXT_START)[:-1] + open_quote
        out.append(" " * _TEXT_START + body + eol)
    return "\n".join(out)


def _open_quote_at_end(body: str) -> str:
    """The quote character of a literal left open at the end of *body*, or ""."""
    quote = ""
    for ch in body:
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
    return quote


def _continued_literal(body: str, quote: str) -> tuple[str, str]:
    """Blank a continuation line's share of a literal; return it and the quote still open."""
    start = body.find(quote)
    if start < 0:
        return " " * len(body), quote
    end = body.find(quote, start + 1)
    if end < 0:
        return " " * len(body), quote
    rest = body[end + 1 :]
    still_open = _open_quote_at_end(rest)
    if still_open:
        rest = rest.ljust(_TEXT_END - _TEXT_START - end - 1)[:-1] + still_open
    return " " * (end + 1) + rest, still_open
