"""What an Extract Method span looks like from the outside: its statements, the
comment that heads it, and whether running it touches anything it does not own.

Read by the helper-naming step only, after a span has been chosen, so the cost
is one walk of one function per emitted plan. The tree is the one the slicer
already holds (``FunctionAnalysis.fn_node``); nothing is re-parsed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .slice import _all_blocks

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap

# Write targets that reach through a name instead of rebinding it: member,
# subscript and pointer access across the grammars the slicer has a dialect for.
_REACH_KINDS = frozenset(
    {
        "attribute",
        "member_expression",
        "field_expression",
        "selector_expression",
        "field_access",
        "subscript",
        "subscript_expression",
        "index_expression",
        "array_access",
        "pointer_expression",
    }
)
# Fields that lead from an access or call to the name it starts from.
_BASE_FIELDS = ("function", "object", "value", "operand", "argument", "array")
# Statements that act outside the span whatever they hold.
_EFFECT_STMT_KINDS = frozenset(
    {"go_statement", "defer_statement", "global_statement", "nonlocal_statement"}
)
_EXPR_STMT = "expression_statement"


def span_statements(
    fn_node: Node | None, start: int, end: int, lmap: LanguageNodeMap
) -> list[Node]:
    """The block statements of *fn_node* spanning lines *start*..*end*, or ``[]``
    when no block cuts there (the span the slicer produced always does)."""
    if fn_node is None:
        return []
    scope_kinds = lmap.function_kinds | lmap.lambda_kinds
    for block, _loop in _all_blocks(fn_node, lmap.block_kinds, scope_kinds, lmap.loop_kinds):
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
    code = [c for c in block.named_children if not _is_comment(c)]
    inside = [s for s in stmts if not _is_comment(s)]
    return holder if code and inside and code[0] == inside[0] and code[-1] == inside[-1] else None


def section_heads(stmts: list[Node]) -> list[tuple[Node, list[str]]]:
    """Each code statement of the span with the comment lines directly above it.

    The first one's comment run may open the span or sit just before it; either
    way a run must touch its statement (no blank line between), since a comment
    separated by a gap heads something else.
    """
    return [(s, _comment_above(s)) for s in stmts if not _is_comment(s)]


def _comment_above(anchor: Node) -> list[str]:
    run: list[Node] = []
    node, top = anchor.prev_named_sibling, anchor.start_point[0]
    while node is not None and _is_comment(node) and node.end_point[0] + 1 == top:
        before = node.prev_sibling
        if before is not None and before.end_point[0] == node.start_point[0]:
            break  # a trailing comment on the line above annotates that line
        run.append(node)
        node, top = node.prev_named_sibling, node.start_point[0]
    lines: list[str] = []
    for comment in reversed(run):
        lines.extend((comment.text or b"").decode("utf-8", "replace").splitlines())
    return lines


def has_outside_effects(stmts: list[Node], locals_: frozenset[str], lmap: LanguageNodeMap) -> bool:
    """Whether running *stmts* changes or reads state the span does not own.

    True for a call in statement position (its result is dropped, so it ran for
    what it does) and for a write through a member, subscript or pointer,
    unless the name either starts from is one the span itself binds
    (*locals_*: ``parts.append(x)`` on a list the span built). Also true for a
    ``with`` block (a resource the span opens) and for the statements in
    ``_EFFECT_STMT_KINDS``. Nested functions and lambdas are skipped: their
    bodies run when called, not here. Aliasing is not followed, so a local
    bound to a parameter's object and then mutated reads as pure.
    """
    scope_kinds = lmap.function_kinds | lmap.lambda_kinds
    assign_kinds = lmap.assignment_kinds | lmap.augmented_assign_kinds
    expr_kinds = lmap.expr_stmt_kinds | {_EXPR_STMT}
    stack = list(stmts)
    while stack:
        node = stack.pop()
        t = node.type
        if t in scope_kinds:
            continue
        if t in lmap.with_kinds or t in _EFFECT_STMT_KINDS:
            return True
        if t in expr_kinds and _drops_a_call(node, locals_, lmap):
            return True
        if t in assign_kinds and _writes_through(node, locals_):
            return True
        stack.extend(node.named_children)
    return False


def _drops_a_call(stmt: Node, locals_: frozenset[str], lmap: LanguageNodeMap) -> bool:
    inner = stmt.named_children[0] if stmt.named_children else None
    while inner is not None and "await" in inner.type and inner.named_children:
        inner = inner.named_children[0]
    if inner is None:
        return False
    if inner.type.endswith("macro_invocation"):
        return True
    return inner.type in lmap.call_kinds and _base_name(inner) not in locals_


def _writes_through(assign: Node, locals_: frozenset[str]) -> bool:
    left = assign.child_by_field_name("left")
    if left is None:
        return False
    targets = [left] if left.type in _REACH_KINDS else left.named_children
    return any(t.type in _REACH_KINDS and _base_name(t) not in locals_ for t in targets)


def _base_name(node: Node) -> str | None:
    """The name an access or call chain starts from (``a`` in ``a.b[c].d()``),
    ``None`` when it starts from an expression rather than a name."""
    for _ in range(64):
        if not node.named_children:
            return (node.text or b"").decode("utf-8", "replace")
        child = next(
            (c for f in _BASE_FIELDS if (c := node.child_by_field_name(f)) is not None),
            node.named_children[0],
        )
        node = child
    return None


def _is_comment(node: Node) -> bool:
    return "comment" in node.type
