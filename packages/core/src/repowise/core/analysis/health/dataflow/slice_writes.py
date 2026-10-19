"""Structural proof that a span writes each OUT variable on every path through it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .slice_nodes import _identifiers, _unwrap_container

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap


def _outs_definitely_assigned(
    span: list[Node],
    returns: tuple[str, ...],
    def_lines: dict[str, list[int]],
    lmap: LanguageNodeMap,
) -> bool:
    """True when every OUT variable is written on *every* path through the span.

    A conditionally written OUT is the unsound case line-based liveness cannot
    see: the helper returns the unwritten value on the paths that skip the
    write, and the caller's assignment then clobbers the live one. The proof
    needs branch structure, so it runs structurally here and the span is
    refused whenever the proof does not go through.
    """
    return all(_stmts_assign(span, var, def_lines.get(var, []), lmap) for var in returns)


def _stmts_assign(stmts: list[Node], var: str, defs: list[int], lmap: LanguageNodeMap) -> bool:
    """True when *stmts* writes *var* on every path through them."""
    return any(_stmt_assigns(st, var, defs, lmap) for st in stmts)


def _stmt_assigns(st: Node, var: str, defs: list[int], lmap: LanguageNodeMap) -> bool:
    """True only when *st* is proved to write *var* however it is entered.

    Positive proof only: a statement kind this cannot classify returns False.
    Defaulting to True would let any statement sharing a line with a
    conditional write stand in as the proof.
    """
    lo = st.start_point[0] + 1
    hi = st.end_point[0] + 1
    if not any(lo <= d <= hi for d in defs):
        return False
    if _is_conditional(st, lmap):
        return _conditional_assigns(st, var, defs, lmap)
    if st.type in lmap.block_kinds:
        container = _unwrap_container(st, lmap.block_kinds)
        return _stmts_assign(container.named_children, var, defs, lmap)
    if st.type in lmap.with_kinds or st.type in lmap.statement_wrapper_kinds:
        return _stmts_assign(list(st.named_children), var, defs, lmap)
    return _unconditional_write(st, var, lmap)


def _unconditional_write(st: Node, var: str, lmap: LanguageNodeMap) -> bool:
    """True when *st* contains a write to *var* no branch or loop guards.

    The walk stops at any control container (a loop body may run zero times, a
    branch arm may not be taken, a catch arm may not fire) and at nested
    scopes, so only writes on the statement's own straight-line path count.
    """
    write_kinds = lmap.assignment_kinds | lmap.augmented_assign_kinds | lmap.local_decl_kinds
    if not write_kinds:
        return False
    barriers = (
        lmap.loop_kinds
        | lmap.try_kinds
        | lmap.catch_kinds
        | lmap.switch_kinds
        | lmap.case_kinds
        | lmap.branch_kinds
        | lmap.if_kinds
        | lmap.function_kinds
        | lmap.lambda_kinds
    )
    if st.type in barriers:
        return False
    stack = [st]
    while stack:
        node = stack.pop()
        if node.id != st.id and node.type in barriers:
            continue
        if node.type in write_kinds and _writes_var(node, var):
            return True
        stack.extend(node.children)
    return False


def _writes_var(node: Node, var: str) -> bool:
    """True when *var* is on the binding side of this write node."""
    for field in ("left", "pattern", "declarator", "name"):
        target = node.child_by_field_name(field)
        if target is not None:
            return var in _identifiers(target)
    return var in _identifiers(node)


def _is_conditional(node: Node, lmap: LanguageNodeMap) -> bool:
    return node.type in lmap.if_kinds or (
        node.type in lmap.branch_kinds and node.child_by_field_name("condition") is not None
    )


def _conditional_assigns(node: Node, var: str, defs: list[int], lmap: LanguageNodeMap) -> bool:
    """True when a conditional chain is exhaustive and every arm writes *var*.

    Two chain shapes. Python flattens ``elif``/``else`` into repeated
    ``alternative`` fields on one ``if``, so the terminal else is a sibling of
    the chained arms. The C family nests instead: the single ``alternative`` is
    another ``if``, whose own else ends the chain. Both must be exhaustive.
    """
    consequence = node.child_by_field_name("consequence") or node.child_by_field_name("body")
    if consequence is None:
        return False
    alternatives = list(node.children_by_field_name("alternative"))
    if not alternatives:
        return False  # no else at all: some path skips the write
    if not _branch_assigns(consequence, var, defs, lmap):
        return False
    for alt in alternatives:
        if _is_conditional(alt, lmap):
            # Nested chain (C family): it carries the chain's terminal else.
            if len(alternatives) == 1:
                return _conditional_assigns(alt, var, defs, lmap)
            # Flattened chain (Python's ``elif``): the else is a sibling below.
            arm = alt.child_by_field_name("consequence") or alt.child_by_field_name("body")
            if arm is None or not _branch_assigns(arm, var, defs, lmap):
                return False
        elif not _branch_assigns(alt, var, defs, lmap):
            return False
    return any(not _is_conditional(alt, lmap) for alt in alternatives)


def _branch_assigns(node: Node, var: str, defs: list[int], lmap: LanguageNodeMap) -> bool:
    if _is_conditional(node, lmap):
        return _conditional_assigns(node, var, defs, lmap)
    if node.type in lmap.block_kinds:
        container = _unwrap_container(node, lmap.block_kinds)
        return _stmts_assign(container.named_children, var, defs, lmap)
    return _stmts_assign(list(node.named_children), var, defs, lmap)
