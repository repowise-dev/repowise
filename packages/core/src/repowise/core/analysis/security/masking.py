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


def mask_comments_and_strings(
    source: str,
    *,
    strings: bool = True,
    nested_comments: bool = False,
    raw_backticks: bool = False,
    hash_comments: bool = True,
) -> str:
    """Blank common comments (and strings, unless *strings* is false), keeping offsets.

    Strings are still tracked when they are kept, so a ``#`` or ``//`` inside
    one does not open a comment. Newlines are never blanked. *nested_comments*
    and *raw_backticks* come from :func:`mask_dialect`; *hash_comments* false
    keeps a ``#`` line, which is code in C, Rust, C# or Swift (``#include``).
    """
    out: list[str] = []
    copied = 0

    def blank(start: int, end: int) -> None:
        nonlocal copied
        out.append(source[copied:start])
        out.append(_NOT_NEWLINE.sub(" ", source[start:end]))
        copied = end

    size = len(source)
    i = 0
    state = "code"
    quote = ""
    triple = False
    verbatim = False
    stack: list[dict[str, Any]] = []
    while i < size:
        if state == "line_comment":
            stop = source.find("\n", i)
            stop = size if stop == -1 else stop
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
        if state in {"string", "template"}:
            pattern = (
                _STRING_STOP[quote * (3 if triple else 1)]
                if state == "string"
                else _template_stop(raw_backticks)
            )
            match = pattern.search(source, i)
            # A backslash escapes the character after it, whatever it is.
            stop = size if match is None else min(match.end() + (match.group() == "\\"), size)
            if strings:
                blank(i, stop)
            i = stop
            if match is not None and match.group() != "\\":
                state = "code"
                if match.group() == "${":
                    stack.append({"kind": "template", "depth": 1})
            continue

        if state == "interpolated":
            pattern = _INTERPOLATED_VERBATIM_STOP if verbatim else _INTERPOLATED_STOP
            match = pattern.search(source, i)
            if match is None:
                if strings:
                    blank(i, size)
                break
            matched = match.group()
            pos = match.start()
            if matched == "\\":
                stop = min(match.end() + 1, size)
                if strings:
                    blank(i, stop)
                i = stop
                continue
            if matched == "{":
                if source.startswith("{{", pos):
                    stop = pos + 2
                    if strings:
                        blank(i, stop)
                    i = stop
                    continue
                stop = pos + 1
                if strings:
                    blank(i, stop)
                i = stop
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
                continue
            if matched == "}":
                if source.startswith("}}", pos):
                    stop = pos + 2
                    if strings:
                        blank(i, stop)
                    i = stop
                    continue
                stop = pos + 1
                if strings:
                    blank(i, stop)
                i = stop
                continue
            if matched == quote:
                if triple:
                    if source.startswith(quote * 3, pos):
                        stop = pos + 3
                        if strings:
                            blank(i, stop)
                        i = stop
                        state = "code"
                        continue
                    stop = pos + 1
                    if strings:
                        blank(i, stop)
                    i = stop
                    continue
                if verbatim and source.startswith(quote * 2, pos):
                    stop = pos + 2
                    if strings:
                        blank(i, stop)
                    i = stop
                    continue
                stop = pos + 1
                if strings:
                    blank(i, stop)
                i = stop
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
            if strings:
                blank(i, i + 1)
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
            if strings:
                blank(i, i + 1)
            i += 1
            state = "template"
        elif char in {"'", '"'}:
            quote = char
            triple = source.startswith(quote * 3, i)
            width = 3 if triple else 1
            is_interpolated = False
            verbatim = False
            if char == '"' and i > 0:
                if source.endswith(("$@", "@$"), 0, i):
                    is_interpolated = True
                    verbatim = True
                elif source.endswith("$", 0, i):
                    is_interpolated = True
            if strings:
                blank(i, i + width)
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
