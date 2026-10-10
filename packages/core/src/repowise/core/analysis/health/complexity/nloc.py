"""Non-blank / non-comment line counting over tree-sitter nodes and raw bytes.

``_count_nloc`` measures a single node's span; ``_count_file_nloc`` is the
no-tree fallback (used when parsing is unavailable); ``CodeLineIndex`` answers
file, function and class NLOC from the walker's single descent of the tree.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node


# A file's decoded lines are needed once per function, per class and per body
# during a single walk; decoding + splitting the whole file on every call is
# quadratic on the hot index path. Callers all pass the same ``source`` bytes
# object within one file, so a one-entry identity cache hoists the decode to
# once per file without changing any signatures.
_LINES_CACHE_SOURCE: bytes | None = None
_LINES_CACHE_LINES: list[str] = []


def _source_lines(source: bytes) -> list[str]:
    """Decoded, newline-split view of *source*, cached per source object."""
    global _LINES_CACHE_SOURCE, _LINES_CACHE_LINES
    if source is _LINES_CACHE_SOURCE:
        return _LINES_CACHE_LINES
    lines = source.decode("utf-8", errors="replace").splitlines()
    _LINES_CACHE_SOURCE = source
    _LINES_CACHE_LINES = lines
    return lines


def is_string_stmt(node: Node) -> bool:
    """True for a bare string-literal statement (a docstring wherever it sits)."""
    if node.type != "expression_statement":
        return False
    named = [c for c in node.children if c.is_named]
    return len(named) == 1 and "string" in named[0].type


def is_docstring_stmt(node: Node) -> bool:
    """True for a docstring: a bare string statement opening a function/class.

    A Python docstring is an ``expression_statement`` whose sole child is a
    string AND which is the first statement of its enclosing body. Gating on
    the leading position keeps a mid-body bare string (a rare no-op, but real
    source) counted, while the documentation block is excluded from NLOC.
    """
    if not is_string_stmt(node):
        return False
    parent = node.parent
    if parent is None:
        return False
    first_stmt = next((c for c in parent.children if c.is_named), None)
    # tree-sitter re-wraps nodes on each access, so identity is compared by id.
    return first_stmt is not None and first_stmt.id == node.id


def _code_line_numbers(node: Node, lines: list[str], *, drop_docstrings: bool) -> set[int]:
    """Line indexes in *node*'s subtree that carry a non-comment token.

    Lines whose only content sits inside comment nodes are excluded; a line
    with real code plus a trailing comment still counts. When *drop_docstrings*
    is set, lines belonging to a bare string-literal statement (a docstring)
    are excluded too.
    """
    code_lines: set[int] = set()
    stack: list[Node] = [node]
    while stack:
        cur = stack.pop()
        if "comment" in cur.type:
            continue
        if drop_docstrings and is_docstring_stmt(cur):
            continue
        if not cur.children and cur.start_byte < cur.end_byte:
            for line in range(cur.start_point[0], cur.end_point[0] + 1):
                if line < len(lines) and lines[line].strip():
                    code_lines.add(line)
        else:
            for child in cur.children:
                stack.append(child)
    return code_lines


def _count_nloc(node: Node, source: bytes) -> int:
    """Return the count of code lines spanned by *node*.

    Blank, comment-only and docstring-only lines are excluded, so a function
    or class NLOC measures substance the same way file-level NLOC does
    (``CodeLineIndex.file_nloc``), rather than counting documentation as code.
    """
    start = node.start_point[0]
    end = node.end_point[0]
    if end < start:
        return 0
    return len(_code_line_numbers(node, _source_lines(source), drop_docstrings=True))


def _count_file_nloc(source: bytes) -> int:
    """Count non-blank lines in *source* bytes (plain fallback, no tree)."""
    try:
        text = source.decode("utf-8", errors="replace")
    except Exception:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


class CodeLineIndex:
    """Every NLOC a walk asks for, answered from one descent of the file.

    ``file_scan`` feeds it each code leaf once; function and class NLOC are then
    prefix-sum lookups instead of a fresh subtree walk per node, which re-read
    a nested function's leaves once per enclosing scope. ``file_nloc`` drops
    comment-only lines but keeps module and class docstrings, as file-level
    NLOC always has.

    ``count`` matches ``_count_nloc`` exactly. A node's leaves are the code
    leaves inside its byte span, so the lines strictly between its first and
    last covered line hold no leaf from outside it and come from the prefix
    sum; those two lines are covered by the node's own first and last leaf. A
    node inside a comment or docstring, whose file-wide exclusion is not the
    node's own, falls back to the subtree walk.
    """

    __slots__ = (
        "_code_prefix",
        "_excluded_ends",
        "_excluded_starts",
        "_leaf_end_rows",
        "_leaf_start_rows",
        "_leaf_starts",
        "_nonblank",
        "file_nloc",
    )

    def __init__(
        self,
        nonblank: list[bool],
        file_rows: set[int],
        code_rows: set[int],
        leaves: list[tuple[int, int, int]],
        excluded: list[tuple[int, int]],
    ) -> None:
        # *leaves* (start byte, start row, end row) and *excluded* (start byte,
        # end byte) arrive in the scan's right-to-left pre-order. Both are
        # disjoint spans, so reversing sorts them by start byte.
        leaves.reverse()
        excluded.reverse()
        self.file_nloc = len(file_rows)
        self._nonblank = nonblank
        self._leaf_starts = [leaf[0] for leaf in leaves]
        self._leaf_start_rows = [leaf[1] for leaf in leaves]
        self._leaf_end_rows = [leaf[2] for leaf in leaves]
        self._excluded_starts = [span[0] for span in excluded]
        self._excluded_ends = [span[1] for span in excluded]
        prefix = [0] * (len(nonblank) + 1)
        running = 0
        for row in range(len(nonblank)):
            if row in code_rows:
                running += 1
            prefix[row + 1] = running
        self._code_prefix = prefix

    def _code_rows_before(self, row: int) -> int:
        prefix = self._code_prefix
        return prefix[row] if row < len(prefix) else prefix[-1]

    def _is_code_row(self, row: int) -> int:
        return 1 if row < len(self._nonblank) and self._nonblank[row] else 0

    def count(self, node: Node, source: bytes) -> int:
        """``_count_nloc(node, source)`` without walking *node*'s subtree."""
        start_byte = node.start_byte
        end_byte = node.end_byte
        k = bisect_right(self._excluded_starts, start_byte) - 1
        if k >= 0 and self._excluded_ends[k] >= end_byte:
            return _count_nloc(node, source)
        if node.end_point[0] < node.start_point[0]:
            return 0
        first_leaf = bisect_left(self._leaf_starts, start_byte)
        end_leaf = bisect_left(self._leaf_starts, end_byte)
        if first_leaf >= end_leaf:
            return 0
        first = self._leaf_start_rows[first_leaf]
        last = self._leaf_end_rows[end_leaf - 1]
        count = self._is_code_row(first)
        if last > first:
            count += self._is_code_row(last)
            count += self._code_rows_before(last) - self._code_rows_before(first + 1)
        return count
