"""Comment and string masks for C and C++ source, for the edit set's prose test.

A blanked character is a comment (or, in the full mask, a string or character
literal); offsets and newlines are kept. A literal never runs past its line,
since C strings cannot without a backslash, so an apostrophe in ``#error
don't`` or a digit separator (``1'000``) never swallows the code after it. A
quote right after a letter or digit opens nothing, except after an encoding
prefix (``L"..."``, ``u8"..."``). A ``//`` comment ending in a backslash goes
on to the next line, as the preprocessor splices it. A file holding a C++ raw
string (``R"(...)"``) gets no mask: its delimiters are not read, and with no
mask every hit stays code, as before.
"""

from __future__ import annotations

import re

_RAW_STRING = re.compile(r'(?<![\w])(?:u8|[uUL])?R"[^\s()\\]{0,16}\(')
# A word a literal may follow with no space: an encoding prefix, or a keyword
# that ends where the literal starts (``return"x"``, ``case'a'``).
_PREFIX = re.compile(r"(?<![\w])(?:u8|[uUL]|return|case|else|sizeof|throw|co_return|co_yield)$")
# What a line leaves open for the next: nothing, a block comment, or a ``//``
# comment or a string spliced on by a trailing backslash.
_CODE, _BLOCK, _LINE, _STRING = 0, 1, 2, 3


def c_family_masks(text: str) -> tuple[str | None, str | None]:
    """``(full, comments)``: *text* with comments and literals, or comments only, blanked."""
    if _RAW_STRING.search(text):
        return None, None
    full: list[str] = []
    comments: list[str] = []
    state = _CODE
    for line in text.split("\n"):
        f, c, state = _mask_line(line, state)
        full.append(f)
        comments.append(c)
    return "\n".join(full), "\n".join(comments)


def _mask_line(line: str, state: int) -> tuple[str, str, int]:
    full = list(line)
    comments = list(line)
    if state == _LINE:
        _blank_both(full, comments, 0, len(line))
        return "".join(full), "".join(comments), _spliced(line)
    i, n = 0, len(line)
    if state == _STRING:
        i, state = _string_tail(line, full)
    while i < n:
        if state == _BLOCK:
            end = line.find("*/", i)
            stop = n if end == -1 else end + 2
            _blank_both(full, comments, i, stop)
            state = _BLOCK if end == -1 else _CODE
            i = stop
        elif line.startswith("//", i):
            _blank_both(full, comments, i, n)
            return "".join(full), "".join(comments), _spliced(line)
        elif line.startswith("/*", i):
            _blank_both(full, comments, i, i + 2)
            state = _BLOCK
            i += 2
        elif line[i] in "\"'" and _opens(line, i):
            i, state = _literal(line, i, full)
        else:
            i += 1
    return "".join(full), "".join(comments), state


def _string_tail(line: str, full: list[str]) -> tuple[int, int]:
    """Blank the rest of a string spliced onto *line*: ``(offset past it, state)``."""
    stop = _close(line, -1, '"')
    if stop is None:
        _blank(full, 0, len(line))
        return len(line), _STRING if line.endswith("\\") else _CODE
    _blank(full, 0, stop)
    return stop, _CODE


def _literal(line: str, start: int, full: list[str]) -> tuple[int, int]:
    """Blank the literal opened at *start*: ``(offset past it, state)``. A string
    left open runs to the line's end, and on when a backslash splices the next
    line; a character literal left open is no literal."""
    quote = line[start]
    stop = _close(line, start, quote)
    if stop is not None:
        _blank(full, start, stop)
        return stop, _CODE
    if quote == "'":
        return start + 1, _CODE
    _blank(full, start, len(line))
    return len(line), _STRING if line.endswith("\\") else _CODE


def _spliced(line: str) -> int:
    """The state a ``//`` comment hands the next line."""
    return _LINE if line.endswith("\\") else _CODE


def _opens(line: str, i: int) -> bool:
    """Whether the quote at *i* opens a literal: not inside a word or number,
    unless the word is an encoding prefix."""
    before = line[i - 1] if i else ""
    if not (before.isalnum() or before == "_"):
        return True
    return bool(_PREFIX.search(line, 0, i))


def _close(line: str, start: int, quote: str) -> int | None:
    """Offset past the unescaped *quote* after *start*, or ``None``."""
    i = start + 1
    while i < len(line):
        if line[i] == "\\":
            i += 2
        elif line[i] == quote:
            return i + 1
        else:
            i += 1
    return None


def _blank_both(full: list[str], comments: list[str], start: int, stop: int) -> None:
    _blank(full, start, stop)
    _blank(comments, start, stop)


def _blank(chars: list[str], start: int, stop: int) -> None:
    for k in range(start, min(stop, len(chars))):
        chars[k] = " "
