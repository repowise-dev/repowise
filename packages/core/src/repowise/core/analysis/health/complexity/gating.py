"""Whether a function is switched off by a constant flag in its own file.

A function whose body opens with ``if not FLAG: return ...`` (or is one
``if FLAG:`` block), where ``FLAG`` is a module-level ``False`` / ``0`` /
``None`` that nothing in the file assigns again, runs none of its work. A
finding on that work is real but dormant: nothing to fix while the switch is
off. Python and TypeScript / JavaScript (``const FLAG = false``).

Ceiling: same file only. A flag imported from another module, or one a test
flips with ``monkeypatch`` (the usual way a kill switch is exercised), is read
as the file says. Upgrade path: resolve the imported name through the graph.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

#: Literal kinds that make a module constant falsy, and those that are ``False`` itself.
_FALSY = frozenset({"false", "none", "null", "undefined", "integer", "number"})
_FALSE = frozenset({"false"})
_ECMASCRIPT = frozenset({"typescript", "tsx", "javascript", "jsx", "svelte", "vue"})
#: Plain assignments a guard may follow (``summary = {...}`` before the ``if``).
_LEAD_STATEMENTS = 2
_DECLARATIONS = frozenset({"lexical_declaration", "variable_declaration"})
_ASSIGNMENTS = frozenset({"assignment", "assignment_expression"})
#: Node kinds that do work: an assignment holding one is no plain assignment.
_WORK = frozenset(
    {"call", "call_expression", "new_expression", "await", "await_expression", "yield"}
)
#: A line this long is minified or generated: read as written rather than scanned.
_LONG_LINE = 2000


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _falsy_literal(node: Node | None) -> str | None:
    """``"false"`` for a ``False`` literal, ``"falsy"`` for ``0`` / ``None``, else ``None``."""
    if node is None or node.type not in _FALSY:
        return None
    if node.type in ("integer", "number") and _text(node).strip() != "0":
        return None
    return "false" if node.type in _FALSE else "falsy"


def _declared(root: Node, language: str) -> dict[str, str]:
    """Module-level ``NAME = <falsy literal>`` (Python) or ``const NAME = ...`` (TS/JS)."""
    out: dict[str, str] = {}
    for stmt in root.named_children:
        if stmt.type == "export_statement":
            stmt = stmt.child_by_field_name("declaration") or stmt
        node = stmt.named_children[0] if stmt.named_children else None
        if language == "python" and node is not None and node.type == "assignment":
            pairs = [(node.child_by_field_name("left"), node.child_by_field_name("right"))]
        elif stmt.type == "lexical_declaration" and stmt.children[0].type == "const":
            pairs = [
                (d.child_by_field_name("name"), d.child_by_field_name("value"))
                for d in stmt.named_children
                if d.type == "variable_declarator"
            ]
        else:
            continue
        for name, value in pairs:
            kind = _falsy_literal(value)
            if name is not None and name.type == "identifier" and kind:
                out[_text(name)] = kind
    return out


def _written_again(name: str, source: str) -> bool:
    """Any line besides the declaration that could assign *name*.

    A text scan, deliberately loose: a keyword argument or a local of the same
    name reads as a write, which only keeps a function in the queue.
    """
    if any(len(line) > _LONG_LINE for line in source.splitlines()):
        return True
    word = re.escape(name)
    write = re.compile(
        rf"\b(?:global|nonlocal|for|as)\b[^\n]*\b{word}\b"
        rf"|\b{word}\b[^\n=]*(?<![=!<>])=(?!=)"
        rf"|\b{word}\s*(?::=|\+\+|--)"
    )
    return len(write.findall(source)) > 1


def module_false_constants(root: Node, source: bytes, language: str) -> dict[str, str]:
    """Module constants that are falsy and never assigned again, by name.

    Reads the root's own statements, so it costs no walk of its own.
    """
    if language != "python" and language not in _ECMASCRIPT:
        return {}
    declared = _declared(root, language)
    if not declared:
        return {}
    text = source.decode("utf-8", errors="replace")
    return {name: kind for name, kind in declared.items() if not _written_again(name, text)}


def _unwrap(node: Node | None) -> Node | None:
    while node is not None and node.type == "parenthesized_expression" and node.named_children:
        node = node.named_children[0]
    return node


def _negates(cond: Node, consts: dict[str, str]) -> bool:
    """``not FLAG`` / ``!FLAG`` for any falsy flag; ``FLAG is False`` /
    ``== false`` for a ``False`` one."""
    if cond.type in ("not_operator", "unary_expression"):
        arg = _unwrap(cond.child_by_field_name("argument"))
        operator = cond.child_by_field_name("operator")
        bang = cond.type == "not_operator" or (operator is not None and _text(operator) == "!")
        return bang and arg is not None and arg.type == "identifier" and _text(arg) in consts
    if cond.type in ("comparison_operator", "binary_expression"):
        named = cond.named_children
        ops = {_text(c) for c in cond.children if not c.is_named}
        return (
            len(named) == 2
            and named[0].type == "identifier"
            and consts.get(_text(named[0])) == "false"
            and named[1].type in _FALSE
            and len(ops) == 1
            and ops <= {"is", "==", "==="}
        )
    return False


def _only_returns(block: Node | None) -> bool:
    if block is None:
        return False
    if block.type == "return_statement":
        return True
    body = [c for c in block.named_children if "comment" not in c.type]
    return len(body) == 1 and body[0].type == "return_statement"


def _plain_assignment(stmt: Node) -> bool:
    """An assignment or declaration that calls nothing: ``summary = {...}``."""
    if stmt.type == "expression_statement":
        named = stmt.named_children
        if len(named) != 1 or named[0].type not in _ASSIGNMENTS:
            return False
    elif stmt.type not in _DECLARATIONS:
        return False
    stack = [stmt]
    while stack:
        node = stack.pop()
        if node.type in _WORK:
            return False
        stack.extend(node.named_children)
    return True


def _statements(body: Node) -> list[Node]:
    """The body's statements, a leading docstring left out."""
    named = [c for c in body.named_children if "comment" not in c.type]
    first = named[0] if named else None
    if (
        first is not None
        and first.type == "expression_statement"
        and first.named_children
        and first.named_children[0].type == "string"
    ):
        named = named[1:]
    return named


def is_gated_off(body: Node, consts: dict[str, str]) -> bool:
    """Whether the body returns at once behind, or sits wholly inside, a constant-false flag."""
    if not consts:
        return False
    stmts = _statements(body)
    if len(stmts) == 1 and stmts[0].type == "if_statement":
        cond = _unwrap(stmts[0].child_by_field_name("condition"))
        if (
            cond is not None
            and cond.type == "identifier"
            and _text(cond) in consts
            and stmts[0].child_by_field_name("alternative") is None
        ):
            return True
    for stmt in stmts[: _LEAD_STATEMENTS + 1]:
        if stmt.type == "if_statement":
            cond = _unwrap(stmt.child_by_field_name("condition"))
            return (
                cond is not None
                and _negates(cond, consts)
                and _only_returns(stmt.child_by_field_name("consequence"))
                and stmt.child_by_field_name("alternative") is None
            )
        if not _plain_assignment(stmt):
            return False
    return False
