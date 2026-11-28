"""Comment and string masking that keeps offsets, and git-compatible line splitting."""

from __future__ import annotations

import re
from typing import Any

# Where each masking state can change. Every other character keeps the state,
# so the scan jumps from one of these to the next instead of walking each one.
_CODE_STOP = re.compile(r"[{}/#'\"`]")
_TEMPLATE_STOP = re.compile(r"\$\{|`|\\")
_STRING_STOP = {
    marker: re.compile(re.escape(marker) + r"|\\") for marker in ("'", '"', "'''", '"""')
}
_NOT_NEWLINE = re.compile(r"[^\n]")
_BLOCK_STOP = re.compile(r"/\*|\*/")
_RAW_BACKTICK_STOP = re.compile("`")
_INTERPOLATED_STOP = re.compile(r'[{}"\\]')
_INTERPOLATED_VERBATIM_STOP = re.compile(r'[{}]|"')
_VERBATIM_STRING_STOP = re.compile(r'""|"')
_RUST_CHAR_LITERAL = re.compile(r"'(?:[^'\\\n]|\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F_]+\}|.))'")
# Languages whose block comments nest, and Go, whose backtick string is raw: a
# backslash there is a character, not an escape.
_NESTED_COMMENT_SUFFIXES = (".kt", ".kts", ".scala", ".sc", ".swift", ".rs", ".dart")
_RAW_BACKTICK_SUFFIXES = (".go",)


def mask_dialect(file_path: str) -> dict[str, bool]:
    """The :func:`mask_comments_and_strings` options *file_path*'s language needs."""
    lowered = file_path.lower()
    return {
        "nested_comments": lowered.endswith(_NESTED_COMMENT_SUFFIXES),
        "raw_backticks": lowered.endswith(_RAW_BACKTICK_SUFFIXES),
        "verbatim_strings": lowered.endswith(".cs"),
        "lifetimes": lowered.endswith(".rs"),
    }


def _block_comment_end(source: str, start: int, nested: bool) -> int:
    """Offset just past the ``*/`` closing the block comment opened before *start*."""
    if not nested:
        stop = source.find("*/", start)
        return len(source) if stop == -1 else stop + 2
    stop, depth = start, 1
    while depth and (m := _BLOCK_STOP.search(source, stop)):
        depth += 1 if m.group() == "/*" else -1
        stop = m.end()
    return len(source) if depth else stop


def _template_stop(raw_backticks: bool) -> re.Pattern[str]:
    return _RAW_BACKTICK_STOP if raw_backticks else _TEMPLATE_STOP


def _line_comment_end(source: str, start: int) -> int:
    """Offset of the newline ending the line comment at *start*, or the end of *source*."""
    stop = source.find("\n", start)
    return len(source) if stop == -1 else stop


def _verbatim_string_end(source: str, start: int) -> tuple[int, bool]:
    """Offset past the next ``""`` or ``"`` in a C# verbatim string, and whether it closed."""
    match = _VERBATIM_STRING_STOP.search(source, start)
    if match is None:
        return len(source), False
    return match.end(), match.group() == '"'


def _quoted_end(source: str, start: int, pattern: re.Pattern[str]) -> tuple[int, str | None]:
    """Offset past the next stop *pattern* finds, and the closing token
    (``None`` for an escape or the end of *source*)."""
    match = pattern.search(source, start)
    if match is None:
        return len(source), None
    # A backslash escapes the character after it, whatever it is.
    if match.group() == "\\":
        return min(match.end() + 1, len(source)), None
    return match.end(), match.group()


def _interpolated_end(
    source: str, start: int, quote: str, triple: bool, verbatim: bool
) -> tuple[int, str]:
    """Offset past the next stop in an interpolated string, and its action:
    ``"open"`` a hole, ``"close"`` the string, ``"more"`` stays, ``"end"`` ran out."""
    pattern = _INTERPOLATED_VERBATIM_STOP if verbatim else _INTERPOLATED_STOP
    match = pattern.search(source, start)
    if match is None:
        return len(source), "end"
    matched, pos = match.group(), match.start()
    if matched == "\\":
        return min(match.end() + 1, len(source)), "more"
    if matched in "{}":
        if source.startswith(matched * 2, pos):
            return pos + 2, "more"
        return pos + 1, "open" if matched == "{" else "more"
    # The quote itself: doubled in a verbatim string, or one of three.
    if triple:
        if source.startswith(quote * 3, pos):
            return pos + 3, "close"
        return pos + 1, "more"
    if verbatim and source.startswith(quote * 2, pos):
        return pos + 2, "more"
    return pos + 1, "close"


def _string_prefix(source: str, start: int, verbatim_strings: bool) -> tuple[bool, bool]:
    """Whether the ``"`` at *start* opens an interpolated string, and a verbatim one."""
    if start == 0:
        return False, False
    if source.endswith(("$@", "@$"), 0, start):
        return True, True
    if source.endswith("$", 0, start):
        return True, False
    return False, verbatim_strings and source.endswith("@", 0, start)


def mask_comments_and_strings(
    source: str,
    *,
    strings: bool = True,
    nested_comments: bool = False,
    raw_backticks: bool = False,
    hash_comments: bool = True,
    verbatim_strings: bool = False,
    lifetimes: bool = False,
) -> str:
    """Blank common comments (and strings, unless *strings* is false), keeping offsets.

    Strings are still tracked when they are kept, so a ``#`` or ``//`` inside
    one does not open a comment. Newlines are never blanked. *nested_comments*,
    *raw_backticks*, *verbatim_strings* and *lifetimes* come from
    :func:`mask_dialect`; *hash_comments* false
    keeps a ``#`` line, which is code in C, Rust, C# or Swift (``#include``).
    """
    out: list[str] = []
    copied = 0

    def blank(start: int, end: int) -> None:
        nonlocal copied
        out.append(source[copied:start])
        out.append(_NOT_NEWLINE.sub(" ", source[start:end]))
        copied = end

    def blank_string(start: int, end: int) -> None:
        if strings:
            blank(start, end)

    size = len(source)
    i = 0
    state = "code"
    quote = ""
    triple = False
    verbatim = False
    stack: list[dict[str, Any]] = []
    while i < size:
        if state == "line_comment":
            stop = _line_comment_end(source, i)
            blank(i, stop)
            i = stop + 1
            state = "code"
            continue
        if state == "block_comment":
            stop = _block_comment_end(source, i, nested_comments)
            blank(i, stop)
            i = stop
            state = "code"
            continue
        if state == "string" and verbatim and quote == '"':
            stop, closed = _verbatim_string_end(source, i)
            blank_string(i, stop)
            i = stop
            if closed:
                state = "code"
                verbatim = False
            continue
        if state in {"string", "template"}:
            pattern = (
                _STRING_STOP[quote * (3 if triple else 1)]
                if state == "string"
                else _template_stop(raw_backticks)
            )
            stop, closer = _quoted_end(source, i, pattern)
            blank_string(i, stop)
            i = stop
            if closer is not None:
                state = "code"
                if closer == "${":
                    stack.append({"kind": "template", "depth": 1})
            continue
        if state == "interpolated":
            stop, action = _interpolated_end(source, i, quote, triple, verbatim)
            blank_string(i, stop)
            if action == "end":
                break
            i = stop
            if action == "open":
                stack.append(
                    {
                        "kind": "interpolated",
                        "depth": 1,
                        "quote": quote,
                        "triple": triple,
                        "verbatim": verbatim,
                    }
                )
                state = "code"
            elif action == "close":
                state = "code"
            continue

        match = _CODE_STOP.search(source, i)
        if match is None:
            break
        i = match.start()
        char = source[i]
        if stack and char == "{":
            stack[-1]["depth"] += 1
            i += 1
        elif stack and char == "}":
            blank_string(i, i + 1)
            stack[-1]["depth"] -= 1
            i += 1
            if stack[-1]["depth"] == 0:
                frame = stack.pop()
                state = frame["kind"]
                quote = frame.get("quote", "")
                triple = frame.get("triple", False)
                verbatim = frame.get("verbatim", False)
        elif (hash_comments and char == "#") or source.startswith("//", i):
            width = 1 if char == "#" else 2
            blank(i, i + width)
            i += width
            state = "line_comment"
        elif source.startswith("/*", i):
            blank(i, i + 2)
            i += 2
            state = "block_comment"
        elif char == "`":
            blank_string(i, i + 1)
            i += 1
            state = "template"
        elif lifetimes and char == "'":
            char_lit = _RUST_CHAR_LITERAL.match(source, i)
            if char_lit is not None:
                blank_string(i, char_lit.end())
                i = char_lit.end()
            else:
                # Rust lifetime ('a, 'static, '_) or loop label ('outer:).
                i += 1
        elif char in {"'", '"'}:
            quote = char
            triple = source.startswith(quote * 3, i)
            width = 3 if triple else 1
            is_interpolated, verbatim = (
                _string_prefix(source, i, verbatim_strings) if char == '"' else (False, False)
            )
            blank_string(i, i + width)
            i += width
            state = "interpolated" if is_interpolated else "string"
        else:
            i += 1
    out.append(source[copied:])
    return "".join(out)


def source_lines(source: str) -> list[str]:
    """*source* split where git splits it, so line numbers agree with a diff.

    ``str.splitlines`` also breaks on form feeds and Unicode separators, which
    numbered every later finding past git's line for it.
    """
    return [line.removesuffix("\r") for line in source.split("\n")]
