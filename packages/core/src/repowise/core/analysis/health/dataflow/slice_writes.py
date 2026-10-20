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


# Scalar literal node types across the supported grammars, read off node
# kinds rather than text (cf. ``complexity/dispatch.py`` and the per-language
# ``perf/dialects`` scalar sets: csharp/rust ``integer_literal``, cpp/kotlin
# ``number_literal``, …). Deliberately scalars only: ``[]`` / ``{}`` are left
# alone, conservatively still returned.
_CONSTANT_LITERAL_KINDS = frozenset(
    {
        "true",
        "false",
        "none",
        "null",
        "nil",
        "undefined",
        "null_literal",
        "boolean_literal",
        "integer",
        "integer_literal",
        "float",
        "float_literal",
        "real_literal",
        "floating_point_literal",
        "number",
        "number_literal",
        "string",
        "concatenated_string",
        "string_literal",
        "interpreted_string_literal",
        "raw_string_literal",
        "character_literal",
        "rune_literal",
        "decimal_integer_literal",
        "hex_integer_literal",
        "octal_integer_literal",
        "binary_integer_literal",
        "decimal_floating_point_literal",
        "hex_floating_point_literal",
        "int_literal",
        "imaginary_literal",
    }
)
# tree-sitter-kotlin spells ``true`` / ``false`` / ``null`` as bare
# ``identifier`` nodes; matching on kind alone would accept every name.
_LITERAL_IDENTIFIER_TEXTS = frozenset({"true", "false", "null"})
_UNARY_KINDS = frozenset({"unary_expression", "unary_operator"})
_PAREN_KINDS = frozenset({"parenthesized_expression"})
_DECLARATOR_KINDS = frozenset(
    {"variable_declarator", "init_declarator", "var_spec", "const_spec"}
)


def _is_literal_value(node: Node | None) -> bool:
    """True when *node* is a scalar literal (``false``, ``0``, ``null``, ...).

    Unwraps parentheses and a leading ``-`` / ``+`` (``-3`` is still a numeric
    literal); anything else computed (calls, binary expressions, interpolated
    template strings) is not one.
    """
    cur = node
    while cur is not None and cur.type in _PAREN_KINDS and cur.named_children:
        cur = cur.named_children[0]
    if cur is None:
        return False
    if cur.type in _UNARY_KINDS:
        operator = cur.child_by_field_name("operator")
        op_text = (
            (operator.text or b"").decode("utf-8", "replace").strip()
            if operator is not None
            else ""
        )
        if op_text not in ("-", "+"):
            # ``!x`` / ``~x`` / ``not x`` compute; named children may still
            # hold a literal operand, which must not count.
            return False
        operand = cur.child_by_field_name("argument") or cur.child_by_field_name("operand")
        if operand is None:
            named = cur.named_children
            operand = named[-1] if named else None
        return _is_literal_value(operand)
    if cur.type in _CONSTANT_LITERAL_KINDS:
        return True
    if cur.type == "identifier":
        text = (cur.text or b"").decode("utf-8", "replace")
        return text in _LITERAL_IDENTIFIER_TEXTS
    if cur.type == "template_string":
        return not any(c.type == "template_substitution" for c in cur.children)
    return False


def _declarator_rhs(decl: Node) -> Node | None:
    """Initializer on a single-name declarator, or None.

    Most grammars expose a ``value`` / ``right`` field. C#'s
    ``variable_declarator`` leaves the RHS as a field-less named child beside
    the ``name`` (mirroring ``dataflow/dialects/csharp.py``).
    """
    value = decl.child_by_field_name("value") or decl.child_by_field_name("right")
    if value is not None:
        return value
    name = decl.child_by_field_name("name")
    rest = [c for c in decl.named_children if c != name]
    return rest[0] if len(rest) == 1 else None


def _trailing_value_node(last: Node, var: str, lmap: LanguageNodeMap) -> Node | None:
    """The single RHS value *last* gives *var*, or None when it is not exactly
    one plain declaration / assignment of it (multi-target declarators and
    ``+=`` updates have no single value to read)."""
    effective = last
    if effective.type == "expression_statement" and len(effective.named_children) == 1:
        effective = effective.named_children[0]
    if effective.type in lmap.local_decl_kinds:
        decls = [c for c in effective.named_children if _writes_var(c, var)]
        if len(decls) != 1:
            return None
        writer = decls[0]
        # C#: ``local_declaration_statement`` → ``variable_declaration`` (no
        # value field) → ``variable_declarator`` (holds the initializer).
        if writer.type not in _DECLARATOR_KINDS:
            nested = [
                c
                for c in writer.named_children
                if c.type in _DECLARATOR_KINDS and _writes_var(c, var)
            ]
            if len(nested) > 1:
                return None
            if len(nested) == 1:
                writer = nested[0]
        value = (
            _declarator_rhs(writer)
            if writer.type in _DECLARATOR_KINDS
            else writer.child_by_field_name("value") or writer.child_by_field_name("right")
        )
        if value is None:
            # Go's ``short_var_declaration`` carries the values on the
            # statement itself (``left`` / ``right`` expression lists).
            value = effective.child_by_field_name("value") or effective.child_by_field_name("right")
        if value is not None and value.type == "expression_list" and len(value.named_children) == 1:
            left = effective.child_by_field_name("left")
            if left is None or len(left.named_children) == 1:
                value = value.named_children[0]
        return value
    if effective.type in _DECLARATOR_KINDS:
        if not _writes_var(effective, var):
            return None
        return _declarator_rhs(effective)
    if effective.type in lmap.augmented_assign_kinds:
        return None
    if effective.type not in lmap.assignment_kinds:
        return None
    if not _writes_var(effective, var):
        return None
    value = effective.child_by_field_name("value") or effective.child_by_field_name("right")
    if value is not None and value.type == "expression_list" and len(value.named_children) == 1:
        left = effective.child_by_field_name("left")
        if left is not None and left.type == "expression_list" and len(left.named_children) != 1:
            return None
        value = value.named_children[0]
    return value


def _trailing_constant_output(
    span: list[Node],
    s: int,
    e: int,
    returns: tuple[str, ...],
    def_lines: dict[str, list[int]],
    lmap: LanguageNodeMap,
) -> bool:
    """True when the span's single OUT is defined only by a trailing statement
    giving it a literal (``boolean allEscape = false;``).

    The helper would then only ever return that constant and the call site
    would read ``boolean allEscape = extractedHelper(...)`` for what was
    ``= false``. The trimmed span is itself a candidate, so callers drop this
    one and the void helper wins; decision points moved are unchanged (the
    trailing declaration carries none).

    Conservative: a computed RHS (``boolean ok = check(x);``) and a literal
    followed by another in-span write (``= false;`` then ``if (x) {..= true;}``)
    both keep the return.
    """
    if len(returns) != 1 or not span:
        return False
    var = returns[0]
    last = span[-1]
    lo = last.start_point[0] + 1
    hi = last.end_point[0] + 1
    own = [d for d in def_lines.get(var, []) if s <= d <= e]
    if not own or any(d < lo or d > hi for d in own):
        return False
    for other, lines in def_lines.items():
        if other != var and any(lo <= d <= hi for d in lines):
            return False
    return _is_literal_value(_trailing_value_node(last, var, lmap))


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
