"""Lines of a JSX file that hold only markup: tags, attributes and text, no expression."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import TYPE_CHECKING

from .nloc import _source_lines

if TYPE_CHECKING:
    from tree_sitter import Node

#: Grammars that parse JSX; a ``.ts`` file reads with ``typescript`` and has none.
_JSX_GRAMMARS = frozenset({"tsx", "javascript"})
#: The ``{...}`` slot inside markup; what it holds is code again.
_EXPRESSION_SLOT = "jsx_expression"


def _token_rows(root: Node) -> tuple[set[int], set[int]]:
    """Rows holding a markup token and rows holding a code token, comments skipped."""
    markup: set[int] = set()
    code: set[int] = set()
    stack: list[tuple[Node, bool]] = [(root, False)]
    while stack:
        node, in_markup = stack.pop()
        kind = node.type
        if "comment" in kind:
            continue
        if kind.startswith("jsx_"):
            in_markup = kind != _EXPRESSION_SLOT
        if node.children:
            stack.extend((child, in_markup) for child in node.children)
        elif node.start_byte < node.end_byte:
            rows = range(node.start_point[0], node.end_point[0] + 1)
            (markup if in_markup else code).update(rows)
    return markup, code


def markup_rows(root: Node, grammar_tag: str, source: bytes) -> list[int]:
    """Sorted 0-indexed non-blank rows whose every token is markup; ``[]`` without JSX."""
    if grammar_tag not in _JSX_GRAMMARS or (b"/>" not in source and b"</" not in source):
        return []
    markup, code = _token_rows(root)
    lines = _source_lines(source)
    return sorted(row for row in markup - code if row < len(lines) and lines[row].strip())


def rows_within(rows: list[int], node: Node) -> int:
    """How many of the sorted *rows* fall inside *node*'s line span."""
    return bisect_right(rows, node.end_point[0]) - bisect_left(rows, node.start_point[0])
