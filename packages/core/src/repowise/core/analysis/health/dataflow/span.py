"""Where an Extract Method span sits in its function's tree: its statements, the
comments that head them, and the ``with`` block it may be the body of.

Read by the helper-naming step only, after a span has been chosen, so the cost
is one walk of one function per emitted plan. The tree is the one the slicer
already holds (``FunctionAnalysis.fn_node``); nothing is re-parsed. Whether the
span acts outside itself is :mod:`effects`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .slice import all_blocks

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap


def span_statements(
    fn_node: Node | None, start: int, end: int, lmap: LanguageNodeMap
) -> list[Node]:
    """The block statements of *fn_node* spanning lines *start*..*end*, or ``[]``
    when no block cuts there (the span the slicer produced always does)."""
    if fn_node is None:
        return []
    scope_kinds = lmap.function_kinds | lmap.lambda_kinds
    for block, _loop in all_blocks(fn_node, lmap.block_kinds, scope_kinds, lmap.loop_kinds):
        stmts = block.named_children
        first = next((i for i, s in enumerate(stmts) if s.start_point[0] + 1 == start), None)
        if first is None:
            continue
        for last in range(first, len(stmts)):
            if stmts[last].end_point[0] + 1 == end:
                return stmts[first : last + 1]
    return []


def enclosing_with(stmts: list[Node], lmap: LanguageNodeMap) -> Node | None:
    """The ``with`` statement whose whole body *stmts* is, else ``None``: a span
    lifted out of ``with timed(...):`` is the block that header labels."""
    if not stmts:
        return None
    block = stmts[0].parent
    holder = block.parent if block is not None else None
    if holder is None or holder.type not in lmap.with_kinds:
        return None
    code = [c for c in block.named_children if not is_comment(c)]
    inside = [s for s in stmts if not is_comment(s)]
    return holder if code and inside and code[0] == inside[0] and code[-1] == inside[-1] else None


def section_heads(stmts: list[Node]) -> list[tuple[Node, list[str]]]:
    """Each code statement of the span with the comment lines directly above it.

    The first one's comment run may open the span or sit just before it; either
    way a run must touch its statement (no blank line between), since a comment
    separated by a gap heads something else.
    """
    return [(s, _comment_above(s)) for s in stmts if not is_comment(s)]


def _comment_above(anchor: Node) -> list[str]:
    run: list[Node] = []
    node, top = anchor.prev_named_sibling, anchor.start_point[0]
    while node is not None and is_comment(node) and node.end_point[0] + 1 == top:
        before = node.prev_sibling
        if before is not None and before.end_point[0] == node.start_point[0]:
            break  # a trailing comment on the line above annotates that line
        run.append(node)
        node, top = node.prev_named_sibling, node.start_point[0]
    lines: list[str] = []
    for comment in reversed(run):
        lines.extend((comment.text or b"").decode("utf-8", "replace").splitlines())
    return lines


def is_comment(node: Node) -> bool:
    return "comment" in node.type
