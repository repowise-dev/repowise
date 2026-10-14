"""Where an Extract Method span sits in its function's tree: its statements,
the comments that head them, and whether a statement is an exiting guard.

The tree is the one the slicer already holds (``FunctionAnalysis.fn_node``);
nothing is re-parsed.
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


def exiting_guard(st: Node, lmap: LanguageNodeMap, exits: frozenset[str]) -> Node | None:
    """The condition of *st* when it is a guard: an ``if`` with no ``else`` /
    ``elif`` arm whose last statement is one of *exits* (the consequence
    itself when it is one, TS / JS ``if (!x) throw e;``); None otherwise."""
    if st.type not in lmap.if_kinds or st.child_by_field_name("alternative") is not None:
        return None
    if any(c.type in ("else_clause", "elif_clause") for c in st.children):
        return None
    body = st.child_by_field_name("consequence")
    if body is None:
        return None
    stmts = [c for c in body.named_children if not is_comment(c)]
    last = body if body.type in exits or not stmts else stmts[-1]
    return st.child_by_field_name("condition") if last.type in exits else None


def is_comment(node: Node) -> bool:
    return "comment" in node.type
