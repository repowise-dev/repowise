"""Lines of a JSX file that hold only markup: tags, attributes and text, no expression."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import TYPE_CHECKING

from .nloc import _source_lines

if TYPE_CHECKING:
    from tree_sitter import Node

#: Language tags whose grammars parse JSX (``.tsx`` is tagged ``typescript``).
_JSX_LANGUAGES = frozenset({"typescript", "javascript"})
#: The ``{...}`` slot inside markup; what it holds is code again.
_EXPRESSION_SLOT = "jsx_expression"


def markup_rows(root: Node, language: str, source: bytes) -> list[int]:
    """Sorted 0-indexed non-blank rows whose every token is markup; ``[]`` without JSX."""
    if language not in _JSX_LANGUAGES or (b"/>" not in source and b"</" not in source):
        return []
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
            (markup if in_markup else code).update(
                range(node.start_point[0], node.end_point[0] + 1)
            )
    lines = _source_lines(source)
    return sorted(row for row in markup - code if row < len(lines) and lines[row].strip())


def rows_within(rows: list[int], node: Node) -> int:
    """How many of the sorted *rows* fall inside *node*'s line span."""
    return bisect_right(rows, node.end_point[0]) - bisect_left(rows, node.start_point[0])
