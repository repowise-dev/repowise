"""Text anchors for an Extract Method span, so an agent finds it by its text.

Line numbers go stale with the first edit: a staged plan applied by line
number, stage after stage, moves every later span. An anchor is the exact
text the span starts with and the text it ends with, so a string-matching
edit finds the span wherever it has moved to; the line numbers stay as a hint.

``first`` is the span's first line, ``last`` its last, with indentation as
written and no trailing line ending. When that line also occurs elsewhere,
the anchor grows by the next line in (``first``) or the line before
(``last``), within :data:`MAX_ANCHOR_LINES` lines that hold a word (a run of
closing brackets or a blank line does not count). A multi-line anchor keeps
the file's own line endings, so it matches the bytes on disk, CRLF included.
Occurrences are counted as substrings, the way a string-matching edit sees
them.

An anchor unique in the whole file is preferred (``unique_in: "file"``); one
unique only inside the function (``"function"``) is kept, and the plan says
the edit must stay inside its host. A span with no anchor unique even in the
function gets none, and its plan is refused (``extract_method``): a stage
served with line numbers alone is the one that gets scripted over the file.
"""

from __future__ import annotations

from typing import Any, Literal, NamedTuple, get_args

#: The longest anchor, in lines holding a word. A span whose ends repeat for
#: longer is a copied block, which a text match cannot tell apart from its twin.
MAX_ANCHOR_LINES = 4

AnchorScope = Literal["file", "function"]
#: Where an anchor is unique (``unique_in``), mirrored in ``packages/types``.
ANCHOR_SCOPES: tuple[str, ...] = get_args(AnchorScope)


class Source(NamedTuple):
    """One file's text as written and its lines, each still ending in ``\\r``
    in a CRLF file, so joining them with ``\\n`` gives the text back."""

    text: str
    rows: list[str]


class Scope(NamedTuple):
    """The function a span lives in: its file, first and last line, and text."""

    source: Source
    start: int
    end: int
    text: str


def file_source(fn_node: Any) -> Source | None:
    """The file *fn_node* was parsed from, or None without its source."""
    root = fn_node
    while root.parent is not None:
        root = root.parent
    src = root.text
    if not src:
        return None
    # The root starts at the first token: put back the blank lines above it so
    # rows line up with line numbers (what it skipped is whitespace only).
    row, column = root.start_point
    text = "\n" * row + " " * column + src.decode("utf-8", errors="replace")
    return Source(text, text.split("\n"))


def function_scope(fn_node: Any, source: Source | None) -> Scope | None:
    """The function's whole lines (its first from column 0) in *source*, or
    None without a parsed function or its source."""
    if fn_node is None or source is None:
        return None
    start, end = fn_node.start_point[0] + 1, fn_node.end_point[0] + 1
    return Scope(source, start, end, "\n".join(source.rows[start - 1 : end]))


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


def _snippet(run: list[str]) -> str:
    # The rows keep a CRLF file's ``\r``; only the snippet's own end drops it.
    return "\n".join(run).removesuffix("\r")


def _pick(scope: Scope, snippets: list[str]) -> tuple[str, str] | None:
    """The shortest snippet unique in the file, else in the function, with
    where it is unique; None when none is."""
    snippets = [s for s in snippets if s.strip()]
    for where, text in (("file", scope.source.text), ("function", scope.text)):
        found = next((s for s in snippets if occurrences(text, s) == 1), None)
        if found is not None:
            return found, where
    return None


def span_anchors(scope: Scope, start: int, end: int) -> dict[str, str] | None:
    """``{"first", "last", "unique_in"}`` for lines *start*..*end* of *scope*,
    or None when either end has no anchor unique in the function."""
    if not scope.start <= start <= end <= scope.end:
        return None
    span = scope.source.rows[start - 1 : end]
    first = _pick(scope, [_snippet(run) for run in _grown(span)])
    # The last anchor grows upward: grow the reversed span, then restore the order.
    last = _pick(scope, [_snippet(run[::-1]) for run in _grown(span[::-1])])
    if first is None or last is None:
        return None
    unique_in = "file" if first[1] == last[1] == "file" else "function"
    return {"first": first[0], "last": last[0], "unique_in": unique_in}


def anchor_plan(plan: dict[str, Any], scope: Scope | None) -> bool:
    """Give *plan*'s span and every stage's their ``anchors``, in place.
    False, with *plan* left as it was, when any of them cannot be anchored."""
    targets = [plan, *(plan.get("stages") or [])]
    found = []
    for target in targets:
        span = target.get("span") or {}
        anchors = span_anchors(scope, span["start"], span["end"]) if scope and span else None
        if anchors is None:
            return False
        found.append(anchors)
    for target, anchors in zip(targets, found, strict=True):
        target["anchors"] = anchors
    return True


__all__ = [
    "ANCHOR_SCOPES",
    "MAX_ANCHOR_LINES",
    "AnchorScope",
    "Scope",
    "Source",
    "anchor_plan",
    "file_source",
    "function_scope",
    "occurrences",
    "span_anchors",
]
