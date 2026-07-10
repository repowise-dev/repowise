"""The CCN / cognitive / max-nesting engine.

``_walk_function_body`` is the recursive per-function walk: it accumulates
McCabe cyclomatic complexity, cognitive complexity, max nesting
depth, ``bumps`` (independent heavy branches), and a side-channel of compound
boolean conditions. The helpers above it classify branch / loop / case / catch
nodes and pull condition subtrees out for the boolean-operator tally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .languages import LanguageNodeMap
from .models import ConditionComplexity

if TYPE_CHECKING:
    from tree_sitter import Node


def _is_boolean_operator(node: Node, lmap: LanguageNodeMap) -> bool:
    """True if this node represents a logical ``&&`` / ``||`` operator."""
    if node.type in lmap.boolean_operator_kinds:
        return True
    if node.type in lmap.boolean_operator_text_kinds:
        # The operator child carries the literal token text.
        for child in node.children:
            if child.text is None:
                continue
            tok = child.text
            if tok in (b"&&", b"||", b"and", b"or"):
                return True
    return False


_BODY_FIELD_NAMES = (
    "body",
    "consequence",
    "alternative",
    "else_clause",
    "block",
)

# The ``if``-shaped node types that can appear as an ``else if`` / ``elif``
# chain continuation. Ternaries / ``if_element`` collection-``if`` are
# deliberately excluded — only statement-level if chains flatten.
_ELSE_IF_NODE_KINDS = frozenset(
    {"if_statement", "if_expression", "elif_clause", "elsif", "if_let_expression"}
)

# Branch nodes that are a decision point (count toward CCN) but do NOT open a
# nesting level: a comprehension filter (``[x for x in xs if a]``) and a match
# ``case ... if guard:`` both parse as ``if_clause`` and sit inline, not as a
# nested block; Scala's ``guard`` (for-comprehension filter / match-case guard)
# is the same inline shape, and Ruby's one-line modifier forms (``return x if
# cond`` / ``x += 1 until done`` / ``x rescue nil``) are too. Treating them as
# flat keeps ``max_nesting`` / ``cognitive`` honest — they were only added to
# ``branch_kinds`` / ``loop_kinds`` / ``catch_kinds`` for the CCN count.
_FLAT_BRANCH_KINDS = frozenset(
    {
        "if_clause",
        "guard",
        "if_modifier",
        "unless_modifier",
        "while_modifier",
        "until_modifier",
        "rescue_modifier",
    }
)


def _is_elif_continuation(node: Node) -> bool:
    """True when *node* is an ``else if`` / ``elif`` chain continuation.

    Such an arm is visually flat guard-clause dispatch but the grammar nests
    each arm under the previous ``else`` / ``alternative`` slot. We recognise
    the continuation so the walker charges it a branch point (CCN) without
    opening a fresh nesting level — mirroring the flat-``switch``/``match``
    special-case. The AST shape differs per grammar:

    - Python: a dedicated ``elif_clause`` node (always a continuation).
    - Ruby: a dedicated ``elsif`` node (always a continuation).
    - TypeScript / Rust / C++: the else-if is wrapped in an ``else_clause``.
    - Java / Go / C# / Dart / Kotlin: the else-if node is the sibling that
      immediately follows the parent ``if``'s ``else`` token.
    """
    if node.type not in _ELSE_IF_NODE_KINDS:
        return False
    if node.type in ("elif_clause", "elsif"):
        return True
    parent = node.parent
    if parent is None:
        return False
    if parent.type == "else_clause":
        return True
    prev = node.prev_sibling
    return prev is not None and prev.type == "else"


def _count_boolean_ops_in_condition(node: Node, lmap: LanguageNodeMap) -> int:
    """Count ``&&`` / ``||`` / ``and`` / ``or`` operators in a condition.

    Walks the subtree rooted at *node* but does not descend into nested
    function bodies (lambdas / closures used as condition values are
    rare and would skew the count).
    """
    if node is None:
        return 0
    count = 0
    stack: list[Node] = [node]
    while stack:
        cur = stack.pop()
        if cur is not node and (cur.type in lmap.function_kinds or cur.type in lmap.lambda_kinds):
            # Lambdas / arrow functions used as condition values (sort keys,
            # predicates) are a separate scope; their boolean operators are not
            # part of the enclosing branch's own decision logic.
            continue
        if _is_boolean_operator(cur, lmap):
            count += 1
        for child in cur.children:
            stack.append(child)
    return count


def _enclosing_construct(node: Node, lmap: LanguageNodeMap) -> str:
    if node.type in lmap.loop_kinds:
        return "for" if "for" in node.type else "while"
    if node.type in lmap.case_kinds:
        return "case"
    if node.type in lmap.catch_kinds:
        return "catch"
    if node.type in lmap.branch_kinds:
        if "ternary" in node.type or "conditional" in node.type:
            return "ternary"
        return "if"
    return "if"


def _condition_subtrees(node: Node) -> list[Node]:
    """Best-effort: pull the *condition* parts out of a branch/loop node.

    Prefers the tree-sitter ``condition`` named field where exposed
    (Python, TS, Java, Rust, Go all use it for most branch shapes).
    Falls back to all direct children except recognised body fields and
    syntactic punctuation.
    """
    cond = node.child_by_field_name("condition")
    if cond is not None:
        return [cond]
    # Switch case value (TS, Java)
    value = node.child_by_field_name("value")
    if value is not None and "case" in node.type:
        return [value]
    # Fallback: direct children minus bodies / blocks.
    body_nodes: set[int] = set()
    for fname in _BODY_FIELD_NAMES:
        child = node.child_by_field_name(fname)
        if child is not None:
            body_nodes.add(child.id)
    out: list[Node] = []
    for child in node.children:
        if child.id in body_nodes:
            continue
        if not child.is_named:
            continue
        if child.type in ("block", "compound_statement", "statement_block"):
            continue
        out.append(child)
    return out


def _collect_case_children(node: Node, lmap: LanguageNodeMap) -> list[Node]:
    """Collect all case/arm nodes from a switch/match node.

    In Rust, ``match_expression`` contains a ``match_block`` which in
    turn holds the ``match_arm`` nodes. Other languages may place cases
    directly under the switch node.  This helper handles both layouts.
    """
    cases: list[Node] = []
    for child in node.children:
        if child.type in lmap.case_kinds:
            cases.append(child)
        else:
            # Descend one level into intermediate wrapper nodes (e.g.
            # ``match_block``) that are not themselves control flow.
            for grandchild in child.children:
                if grandchild.type in lmap.case_kinds:
                    cases.append(grandchild)
    return cases


def _is_flat_match(node: Node, lmap: LanguageNodeMap) -> bool:
    """Return True if *node* is a match/switch with only simple arms.

    A "flat" match has arms whose bodies are single expressions without
    nested control flow (no ``if``, ``match``, ``for``, ``while``,
    ``loop``, ``if_let``, ``while_let`` expressions, and no ``block``
    with multiple statements).  Flat matches contribute 1 CCN point for
    the match keyword itself but do NOT count each arm individually.
    """
    complex_types = lmap.branch_kinds | lmap.loop_kinds | lmap.switch_kinds
    cases = _collect_case_children(node, lmap)
    if not cases:
        return False
    return all(not _subtree_contains_complex(arm, complex_types) for arm in cases)


def _subtree_contains_complex(arm_node: Node, complex_types: frozenset[str]) -> bool:
    """Return True if *arm_node*'s subtree contains complex control flow.

    A ``block`` with more than one statement is also considered complex.
    """
    stack: list[Node] = list(arm_node.children)
    while stack:
        cur = stack.pop()
        if cur.type in complex_types:
            return True
        # A block with multiple named children (statements) is complex.
        if cur.type == "block":
            named_children = [c for c in cur.children if c.is_named]
            if len(named_children) > 1:
                return True
        for child in cur.children:
            stack.append(child)
    return False


def _walk_function_body(
    body_node: Node,
    lmap: LanguageNodeMap,
) -> tuple[int, int, int, int, list[ConditionComplexity]]:
    """Recursive AST walk. Returns (ccn, max_nesting, cognitive, bumps,
    complex_conditions).

    Starts CCN at 1 (the entry path). Nested function bodies are
    skipped — they will (or already did) produce their own
    ``FunctionComplexity``.

    ``bumps`` counts how many *direct* children of the function body
    contain nested control flow that reaches a depth of ≥ 2. A function
    with several heavy independent branches is "bumpy".

    ``complex_conditions`` is an additive side-channel — collected for
    every branch/loop/case construct encountered. The CCN / cognitive
    accumulation logic is unchanged.
    """

    ccn = 1
    max_nesting = 0
    cognitive = 0
    bumps = 0
    conditions: list[ConditionComplexity] = []

    # Track match_expression nodes identified as "flat" so their arms
    # are not individually counted as branch points.
    flat_match_ids: set[int] = set()

    def _recurse(node: Node, depth: int) -> None:
        nonlocal ccn, max_nesting, cognitive

        # Don't descend into nested function bodies — they're walked
        # separately at the top level. Lambdas / arrow functions DO
        # contribute to the enclosing function's complexity.
        if node.type in lmap.function_kinds:
            return

        nesting_increment = 0
        ccn_increment = 0

        # Check if this is a case/arm inside a flat match — skip it.
        # In Rust the parent chain is match_arm → match_block → match_expression,
        # so we check both parent and grandparent.
        _parent = node.parent
        is_flat_match_arm = False
        if (
            node.type in lmap.case_kinds
            and _parent is not None
            and (
                _parent.id in flat_match_ids
                or (_parent.parent is not None and _parent.parent.id in flat_match_ids)
            )
        ):
            is_flat_match_arm = True

        # ``node.is_named`` guards grammars (Ruby) whose keyword tokens share
        # the node-type name of their parent node (an ``if`` node contains an
        # unnamed ``if`` token of type ``"if"``): only the named node is the
        # control-flow construct — without the guard every Ruby branch/loop
        # would double-count.
        if is_flat_match_arm:
            # Flat match arms: no CCN increment, no nesting increment.
            pass
        elif node.is_named and (
            node.type in lmap.branch_kinds
            or node.type in lmap.loop_kinds
            or node.type in lmap.case_kinds
            or node.type in lmap.catch_kinds
        ):
            ccn_increment = 1
            # An ``else if`` / ``elif`` chain arm and a comprehension /
            # case-guard filter are flat: charge the extra branch (ccn) but do
            # not open a new nesting level for them. This parallels the
            # flat-``switch``/``match`` special-case above.
            if node.type not in _FLAT_BRANCH_KINDS and not _is_elif_continuation(node):
                nesting_increment = 1
            # Side-channel: count compound boolean ops in this
            # construct's condition. Does not affect ccn/cognitive
            # (boolean operators are still tallied independently by
            # the regular recursion below).
            op_count = 0
            for sub in _condition_subtrees(node):
                op_count += _count_boolean_ops_in_condition(sub, lmap)
            if op_count > 0:
                conditions.append(
                    ConditionComplexity(
                        line=node.start_point[0] + 1,
                        operator_count=op_count,
                        enclosing_construct=_enclosing_construct(node, lmap),
                    )
                )
        elif node.is_named and node.type in lmap.try_kinds:
            # TRY opens a nesting level but does not branch on its own.
            nesting_increment = 1
        elif node.is_named and node.type in lmap.switch_kinds:
            # Detect flat match: all arms are simple single-expression arms.
            if _is_flat_match(node, lmap):
                flat_match_ids.add(node.id)
                # Flat match: count 1 CCN point for the match itself,
                # open a nesting level, but arms won't be counted.
                ccn_increment = 1
            # Switch opens nesting; each case contributes its own +1.
            nesting_increment = 1
        elif _is_boolean_operator(node, lmap):
            ccn_increment = 1

        ccn += ccn_increment
        new_depth = depth + nesting_increment
        if nesting_increment:
            # Cognitive complexity: each nesting node adds (1 + depth).
            cognitive += 1 + depth
        elif ccn_increment:
            # Flat +1 for boolean operators (no nesting impact).
            cognitive += 1

        if new_depth > max_nesting:
            max_nesting = new_depth

        for child in node.children:
            _recurse(child, new_depth)

    for child in body_node.children:
        # Per-child peak depth: temporarily swap max_nesting out so we
        # can read just this child's contribution, then restore.
        outer_max = max_nesting
        max_nesting = 0
        _recurse(child, 0)
        child_peak = max_nesting
        max_nesting = max(outer_max, child_peak)
        if child_peak >= 2:
            bumps += 1

    return ccn, max_nesting, cognitive, bumps, conditions
