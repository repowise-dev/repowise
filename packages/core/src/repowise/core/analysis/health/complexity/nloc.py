"""Non-blank / non-comment line counting over tree-sitter nodes and raw bytes.

``_count_nloc`` measures a single node's span; ``_count_file_nloc`` is the
no-tree fallback (used when parsing is unavailable); ``_count_file_nloc_tree``
excludes comment-only lines using the parsed tree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node


def _is_docstring_stmt(node: Node) -> bool:
    """True for a bare string-literal statement (a docstring / directive).

    A Python docstring is an ``expression_statement`` whose sole child is a
    string. Such a statement carries no logic; its lines are documentation,
    not code, so they are excluded from a function's / class's NLOC.
    """
    if node.type != "expression_statement":
        return False
    named = [c for c in node.children if c.is_named]
    return len(named) == 1 and "string" in named[0].type


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
        if drop_docstrings and _is_docstring_stmt(cur):
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
    (``_count_file_nloc_tree``), rather than counting documentation as code.
    """
    start = node.start_point[0]
    end = node.end_point[0]
    if end < start:
        return 0
    try:
        lines = source.decode("utf-8", errors="replace").splitlines()
    except Exception:
        return end - start + 1
    return len(_code_line_numbers(node, lines, drop_docstrings=True))


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
    # File-level NLOC keeps counting module/class docstrings (only comment-only
    # lines are dropped), so ``drop_docstrings`` stays off here.
    return len(_code_line_numbers(root_node, lines, drop_docstrings=False))
