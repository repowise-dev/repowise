"""Shared tree-sitter AST helpers used across the walker passes.

Name/text extraction, function-entry naming (including lambdas assigned to
a variable or passed as a callback), top-level function-node collection, and
parameter counting. These are the cross-pass primitives; the metric passes
(cyclomatic, assertions, error-handling, perf, class-analysis) build on them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .languages import LanguageNodeMap

if TYPE_CHECKING:
    from tree_sitter import Node

# Suffix shared by tree-sitter identifier node types (``identifier`` /
# ``field_identifier`` / ``property_identifier`` / ``type_identifier`` ...).
# Used by both the assertion-callee scan and the class self-member probe.
_IDENTIFIER_SUFFIX = "identifier"

# Leaf node types that carry a declared name at the bottom of a C/C++
# ``declarator`` chain.
_DECLARATOR_NAME_KINDS = frozenset(
    {"identifier", "field_identifier", "type_identifier", "qualified_identifier"}
)


def _find_name(node: Node) -> str:
    """Best-effort: return the text of the first identifier child."""
    # Search a couple of common field names first.
    for field_name in ("name", "identifier"):
        child = node.child_by_field_name(field_name)
        if child is not None and child.text is not None:
            return child.text.decode("utf-8", errors="replace")
    # C / C++: the function name is not a direct child but nested inside a
    # ``declarator`` chain (``function_definition → function_declarator →
    # field_identifier``). Languages with a ``name`` field never reach here.
    decl = node.child_by_field_name("declarator")
    hops = 0
    while decl is not None and hops < 6:
        if decl.type in _DECLARATOR_NAME_KINDS and decl.text is not None:
            return decl.text.decode("utf-8", errors="replace")
        decl = decl.child_by_field_name("declarator") or decl.child_by_field_name("name")
        hops += 1
    for child in node.children:
        if (
            child.type in ("identifier", "property_identifier", "field_identifier")
            and child.text is not None
        ):
            return child.text.decode("utf-8", errors="replace")
    return "<anonymous>"


def _node_text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _find_assigned_lambda_name(node: Node) -> str | None:
    parent = node.parent
    while parent is not None:
        if parent.type == "variable_declarator":
            name = parent.child_by_field_name("name")
            if name is not None and name.text is not None:
                return _node_text(name)
        if parent.type in {"assignment_expression", "assignment_pattern"}:
            left = parent.child_by_field_name("left")
            if left is None:
                left = next((child for child in parent.children if child is not node), None)
            if left is not None and left.text is not None:
                return _node_text(left)
        if parent.type not in {"parenthesized_expression", "as_expression", "satisfies_expression"}:
            return None
        parent = parent.parent
    return None


def _find_call_callback_callee(node: Node) -> str | None:
    parent = node.parent
    while parent is not None and parent.type in {
        "parenthesized_expression",
        "as_expression",
        "satisfies_expression",
    }:
        parent = parent.parent
    if parent is None or parent.type != "arguments":
        return None
    call = parent.parent
    if call is None or call.type != "call_expression":
        return None
    callee = call.child_by_field_name("function")
    if callee is None or callee.text is None:
        return None
    return " ".join(_node_text(callee).split())


_TEST_SUITE_CALLBACK_CALLEES = frozenset({"describe", "context", "suite"})


def _is_test_suite_callback(node: Node, lmap: LanguageNodeMap) -> bool:
    if node.type not in lmap.lambda_kinds:
        return False
    callee_text = _find_call_callback_callee(node)
    if callee_text is None:
        return False
    return any(part in _TEST_SUITE_CALLBACK_CALLEES for part in callee_text.split("."))


def _find_function_entry_name(node: Node, lmap: LanguageNodeMap) -> str:
    if node.type not in lmap.lambda_kinds:
        return _find_name(node)
    assigned = _find_assigned_lambda_name(node)
    if assigned:
        return assigned
    if callee := _find_call_callback_callee(node):
        return f"{callee} callback"
    return f"<anonymous@{node.start_point[0] + 1}>"


def _collect_function_nodes(root: Node, lmap: LanguageNodeMap) -> list[Node]:
    """All function / method definition nodes in the file.

    Iterative pre-order traversal. We descend into class / module
    bodies but do **not** recurse below a function or lambda. Lambdas found
    before any function boundary are module-level executable units (for
    example route callbacks) and get their own entry; lambdas inside an
    already-collected function still roll up into that function.
    """
    out: list[Node] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type in lmap.lambda_kinds and _is_test_suite_callback(node, lmap):
            stack.extend(node.children)
            continue
        if node.type in lmap.function_kinds or node.type in lmap.lambda_kinds:
            out.append(node)
            continue
        for child in node.children:
            stack.append(child)
    return out


def _count_parameters(fn_node: Node) -> int:
    """Best-effort parameter-list size for *fn_node*.

    Looks at tree-sitter ``parameters`` / ``parameter_list`` / ``parameters_list`` fields and counts non-punctuation
    children. Returns 0 when no parameter list is found.
    """
    params = fn_node.child_by_field_name("parameters")
    if params is None:
        for child in fn_node.children:
            if child.type in ("parameters", "parameter_list", "formal_parameters"):
                params = child
                break
    if params is None:
        return 0
    count = 0
    for child in params.children:
        if child.type in ("(", ")", ",", "self", "cls", ":", "*", "**"):
            continue
        if child.is_named:
            count += 1
    return count
