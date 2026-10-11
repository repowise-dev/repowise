"""Where a function's header ends and its body lies, read off its source lines.

Language-agnostic bracket counting over the lines a symbol's span covers, for
consumers that hold a symbol's line range and its file's text but no parse
tree: the skeleton view elides bodies, and refactoring compares two functions'
bodies line for line.
"""

from __future__ import annotations

import re

#: Max lines scanned past a symbol's start to find the end of its signature.
_SIG_SCAN_MAX = 12

#: A body's last line that only closes it (``}``, ``});``, ``end``).
_CLOSER_RE = re.compile(r"\s*(?:[})\];,]+|end)\s*")


def signature_end(lines: list[str], start: int, end: int) -> int:
    """Last 0-indexed line of the signature starting at *start*.

    Bracket-balance scan, language-agnostic: the signature ends on the first
    line where parens/brackets are balanced and the line closes with a body
    opener (``:``/``{``), a terminator (``;``), or the param list itself.
    Allman-style braces (``{`` alone on the next line) are folded in. Falls
    back to the start line when nothing matches within the scan window.
    """
    depth = 0
    last = min(start + _SIG_SCAN_MAX - 1, end)
    for i in range(start, last + 1):
        line = lines[i]
        depth += line.count("(") - line.count(")")
        depth += line.count("[") - line.count("]")
        if depth > 0:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith((":", "{", ";", "=>")):
            return i
        if stripped.startswith(("@", "#[")):
            continue  # annotation/attribute line a symbol's bounds may start on
        # Signature closed without a body opener — check for an Allman brace.
        j = i + 1
        if j <= end and lines[j].strip().startswith("{"):
            return j
        return i
    return start


def body_span(lines: list[str], start: int, end: int) -> tuple[int, int]:
    """First and last 0-indexed line of the body of the callable on lines
    *start*..*end*: below its signature, above a line that only closes it.
    Empty (first > last) for a one-line callable."""
    body_start = signature_end(lines, start, end) + 1
    body_end = end - 1 if end >= body_start and _CLOSER_RE.fullmatch(lines[end]) else end
    return body_start, body_end


__all__ = ["body_span", "signature_end"]
