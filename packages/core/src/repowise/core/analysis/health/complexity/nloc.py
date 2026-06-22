"""Non-blank / non-comment line counting over tree-sitter nodes and raw bytes.

``_count_nloc`` measures a single node's span; ``_count_file_nloc`` is the
no-tree fallback (used when parsing is unavailable); ``_count_file_nloc_tree``
excludes comment-only lines using the parsed tree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node


def _count_nloc(node: Node, source: bytes) -> int:
    """Return the count of non-blank lines spanned by *node*."""
    start = node.start_point[0]
    end = node.end_point[0]
    if end < start:
        return 0
    try:
        snippet = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    except Exception:
        return end - start + 1
    return sum(1 for line in snippet.splitlines() if line.strip())


def _count_file_nloc(source: bytes) -> int:
    """Count non-blank lines in *source* bytes (plain fallback, no tree)."""
    try:
        text = source.decode("utf-8", errors="replace")
    except Exception:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


def _count_file_nloc_tree(root_node: Node, source: bytes) -> int:
    """Count lines that have at least one non-comment token.

    Lines where all content is inside comment nodes are excluded; lines
    with real code plus a trailing comment still count.
    """
    try:
        lines = source.decode("utf-8", errors="replace").splitlines()
    except Exception:
        return 0
    code_lines: set[int] = set()
    stack = [root_node]
    while stack:
        node = stack.pop()
        if "comment" in node.type:
            continue
        if not node.children and node.start_byte < node.end_byte:
            for line in range(node.start_point[0], node.end_point[0] + 1):
                if line < len(lines) and lines[line].strip():
                    code_lines.add(line)
        else:
            for child in node.children:
                stack.append(child)
    return len(code_lines)
