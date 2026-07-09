"""Non-blank / non-comment line counting over tree-sitter nodes and raw bytes.

``_count_nloc`` measures a single node's span; ``_count_file_nloc`` is the
no-tree fallback (used when parsing is unavailable); ``_count_file_nloc_tree``
excludes comment-only lines using the parsed tree.
"""

from __future__ import annotations

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


def _is_docstring_stmt(node: Node) -> bool:
    """True for a docstring: a bare string statement opening a function/class.

    A Python docstring is an ``expression_statement`` whose sole child is a
    string AND which is the first statement of its enclosing body. Gating on
    the leading position keeps a mid-body bare string (a rare no-op, but real
    source) counted, while the documentation block is excluded from NLOC.
    """
    if node.type != "expression_statement":
        return False
    named = [c for c in node.children if c.is_named]
    if len(named) != 1 or "string" not in named[0].type:
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
    return len(_code_line_numbers(node, _source_lines(source), drop_docstrings=True))


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
    # File-level NLOC keeps counting module/class docstrings (only comment-only
    # lines are dropped), so ``drop_docstrings`` stays off here.
    return len(_code_line_numbers(root_node, _source_lines(source), drop_docstrings=False))
