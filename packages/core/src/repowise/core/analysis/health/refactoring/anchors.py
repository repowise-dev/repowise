"""Text anchors for an Extract Method span, so an agent finds it by its text.

Line numbers go stale with the first edit: a staged plan applied by line
number, stage after stage, moves every later span. An anchor is the exact
text the span starts with and the text it ends with, so a string-matching
edit finds the span wherever it has moved to; the line numbers stay as a hint.

``first`` is the span's first line, ``last`` its last, each without its line
ending (a CRLF file's ``\\r`` dropped) and with its indentation as written.
When that line also occurs elsewhere in the function, the anchor grows by the
next line in (``first``) or the line before (``last``) until it occurs exactly
once in the function, within :data:`MAX_ANCHOR_LINES` lines that hold a word
(a run of closing brackets or a blank line does not count); lines are joined
with ``\\n``. Occurrences are counted as substrings, the way a
string-matching edit sees them. A span that no snippet that short anchors
gets none, and its plan is refused (``extract_method``): a stage served with
line numbers alone is the one that gets scripted over the file.
"""

from __future__ import annotations

from typing import Any

#: The longest anchor, in lines holding a word. A span whose ends repeat for
#: longer is a copied block, which a text match cannot tell apart from its twin.
MAX_ANCHOR_LINES = 4


def function_lines(fn_node: Any) -> tuple[int, list[str]] | None:
    """The function's whole lines (its first line from column 0) and the line
    number of the first, or None without the parsed source."""
    root = fn_node
    while root.parent is not None:
        root = root.parent
    src = root.text
    if not src:
        return None
    lo = src.rfind(b"\n", 0, fn_node.start_byte) + 1
    hi = src.find(b"\n", fn_node.end_byte)
    region = src[lo : len(src) if hi < 0 else hi].decode("utf-8", errors="replace")
    return fn_node.start_point[0] + 1, [line.removesuffix("\r") for line in region.split("\n")]


def occurrences(text: str, snippet: str) -> int:
    """How many times *snippet* occurs in *text*, overlaps included."""
    count, at = 0, text.find(snippet)
    while at >= 0:
        count += 1
        at = text.find(snippet, at + 1)
    return count


def _grown(rows: list[str]) -> list[list[str]]:
    """*rows*' leading runs, one line longer each, while at most
    :data:`MAX_ANCHOR_LINES` of the lines hold a word."""
    out, words = [], 0
    for k, row in enumerate(rows, 1):
        words += any(ch.isalnum() for ch in row)
        if words > MAX_ANCHOR_LINES:
            break
        out.append(rows[:k])
    return out


def _unique(body: str, snippets: list[str]) -> str | None:
    return next((s for s in snippets if s.strip() and occurrences(body, s) == 1), None)


def span_anchors(lines: tuple[int, list[str]], start: int, end: int) -> dict[str, str] | None:
    """``{"first", "last"}`` for lines *start*..*end* of the function *lines*
    (:func:`function_lines`), or None when either end has no unique anchor."""
    base, rows = lines
    s, e = start - base, end - base
    if not 0 <= s <= e < len(rows):
        return None
    body = "\n".join(rows)
    span = rows[s : e + 1]
    first = _unique(body, ["\n".join(run) for run in _grown(span)])
    # The last anchor grows upward: grow the reversed span, then restore the order.
    last = _unique(body, ["\n".join(reversed(run)) for run in _grown(span[::-1])])
    if first is None or last is None:
        return None
    return {"first": first, "last": last}


def anchor_plan(plan: dict[str, Any], fn_node: Any) -> bool:
    """Give *plan*'s span and every stage's their ``anchors``, in place.
    False, with *plan* left as it was, when any of them cannot be anchored."""
    lines = function_lines(fn_node) if fn_node is not None else None
    targets = [plan, *(plan.get("stages") or [])]
    found = []
    for target in targets:
        span = target.get("span") or {}
        anchors = span_anchors(lines, span["start"], span["end"]) if lines and span else None
        if anchors is None:
            return False
        found.append(anchors)
    for target, anchors in zip(targets, found, strict=True):
        target["anchors"] = anchors
    return True


__all__ = ["MAX_ANCHOR_LINES", "anchor_plan", "function_lines", "occurrences", "span_anchors"]
