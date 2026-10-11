"""Whether a function is a flat record builder, whose complexity no helper splits.

A function that fills one value field by field (rows added, keys set, a field
copied when present, a query parameter set when given) earns its CCN from the
guard on each field and the conditionals in each value. The guards are
independent of each other, so lifting a run of them into a helper moves part
of the list without making either half simpler to read.

The shape, read off the tree in any language the slicer serves: every
statement writes the same one name (a call on it, ``x.f = v`` / ``x[k] = v``,
``delete x.f``) or binds a local, and the only control flow is a guard around
such statements (an ``if`` chain, a ``try``, a loop over rows) or a guard that
returns. A second written name, a dropped call to anything else, a loop in a
loop, a callback (whose body is logic the statement only registers), a
``break`` / ``continue`` or a ``switch`` is logic, and the function is not flat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..complexity.nloc import is_string_stmt
from ..dataflow.effects import binding_target, bound_names
from ..dataflow.span import is_comment

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap

_EXPR_STMT = "expression_statement"
_EMPTY_KINDS = frozenset({"pass_statement", "empty_statement"})
_ELSE_KINDS = frozenset({"else_clause", "elif_clause"})
_FINALLY = "finally_clause"
_ACCESS_FIELDS = ("object", "value", "operand", "argument", "array", "function")
# Fewer writes than this is a short function with a few guards, not a builder.
_MIN_WRITES = 3


@dataclass(slots=True)
class _Census:
    lmap: LanguageNodeMap
    roots: set[str] = field(default_factory=set)
    writes: int = 0
    bound: set[str] = field(default_factory=set)
    in_loop: bool = False

    def bind(self, names: list[str]) -> bool:
        """Count a local; one bound twice is state the guards update, not a field."""
        if not names or self.bound.intersection(names):
            return False
        self.bound.update(names)
        return True


def declarative_flat(fn_node: Node | None, lmap: LanguageNodeMap) -> bool:
    """True when *fn_node*'s body is a flat record builder (module docstring)."""
    body = fn_node.child_by_field_name("body") if fn_node is not None else None
    if body is None or body.type not in lmap.block_kinds:
        return False
    census = _Census(lmap)
    stmts = _statements(body)
    if stmts and stmts[-1].type in lmap.return_kinds:
        stmts = stmts[:-1]
    if not all(_flat(s, census, top=True) for s in stmts):
        return False
    writes = census.writes
    return len(census.roots) == 1 and writes >= _MIN_WRITES and writes > len(census.bound)


def _statements(block: Node) -> list[Node]:
    """*block*'s statements, comments and a leading docstring left out."""
    out = [c for c in block.named_children if not is_comment(c)]
    return out[1:] if out and is_string_stmt(out[0]) else out


def _flat(stmt: Node, census: _Census, *, top: bool = False) -> bool:
    lmap = census.lmap
    kind = stmt.type
    if kind in _EMPTY_KINDS:
        return True
    if kind in lmap.function_kinds:
        # A local helper (``def add(label, value)``): calls to it are the writes.
        return top
    if kind in lmap.block_kinds:
        return all(_flat(s, census) for s in _statements(stmt))
    if kind in lmap.if_kinds or kind in _ELSE_KINDS:
        return _flat_branches(stmt, census)
    if kind in lmap.try_kinds or kind in lmap.catch_kinds or kind == _FINALLY:
        # Only the clauses' blocks: a handler's exception type is no statement.
        parts = lmap.block_kinds | lmap.catch_kinds | _ELSE_KINDS | {_FINALLY}
        return all(_flat(c, census) for c in stmt.named_children if c.type in parts)
    if kind in lmap.loop_kinds:
        # One row per element; a loop inside it walks a structure.
        loop_body = stmt.child_by_field_name("body")
        if census.in_loop or loop_body is None:
            return False
        census.in_loop = True
        flat = _flat(loop_body, census)
        census.in_loop = False
        return flat
    if kind in lmap.local_decl_kinds:
        if _holds_callback(stmt, lmap):
            return False
        targets = [t for c in stmt.named_children if (t := binding_target(c, lmap)) is not None]
        return census.bind([n for t in targets for n in bound_names(t)])
    return _flat_expression(stmt, census)


def _flat_branches(stmt: Node, census: _Census) -> bool:
    """Every arm of an ``if`` chain is flat, or only returns."""
    condition = stmt.child_by_field_name("condition")
    arms = [c for c in stmt.named_children if c != condition and not is_comment(c)]
    return all(_guard_exit(arm, census.lmap) or _flat(arm, census) for arm in arms)


def _guard_exit(arm: Node, lmap: LanguageNodeMap) -> bool:
    exits = lmap.return_kinds | lmap.raise_kinds
    if arm.type in exits:
        return True
    stmts = _statements(arm) if arm.type in lmap.block_kinds else []
    return len(stmts) == 1 and stmts[0].type in exits


def _flat_expression(stmt: Node, census: _Census) -> bool:
    """A write to the census's one name, or a local bound to a value."""
    lmap = census.lmap
    if stmt.type != _EXPR_STMT and stmt.type not in lmap.expr_stmt_kinds:
        return False
    if stmt.named_child_count != 1 or _holds_callback(stmt, lmap):
        return False
    expr = stmt.named_children[0]
    target = binding_target(expr, lmap)
    if target is not None and target.type in ("identifier", "pattern_list", "tuple_pattern"):
        return census.bind(bound_names(target))
    written = target if target is not None else _written(expr, lmap)
    root = _root(written) if written is not None else None
    if root is None:
        return False
    census.roots.add(root)
    census.writes += 1
    return True


def _holds_callback(stmt: Node, lmap: LanguageNodeMap) -> bool:
    """Whether a function or lambda sits in *stmt*: its body is logic the
    statement only registers (``useEffect(() => ...)``, ``cmd.action(...)``)."""
    kinds = lmap.function_kinds | lmap.lambda_kinds
    stack = list(stmt.named_children)
    while stack:
        node = stack.pop()
        if node.type in kinds:
            return True
        stack.extend(node.named_children)
    return False


def _written(expr: Node, lmap: LanguageNodeMap) -> Node | None:
    """What a statement-position call or ``delete`` writes through."""
    if expr.type in lmap.call_kinds:
        return expr.child_by_field_name("function")
    if expr.type == "unary_expression" and expr.children and expr.children[0].type == "delete":
        return expr.child_by_field_name("argument")
    return None


def _root(node: Node) -> str | None:
    """The plain name an access chain starts from; ``None`` for a call result."""
    for _ in range(64):
        if node.type == "identifier":
            return node.text.decode("utf-8", "replace") if node.text else None
        nxt = next(
            (c for f in _ACCESS_FIELDS if (c := node.child_by_field_name(f)) is not None), None
        )
        if nxt is None or node.type in ("call", "call_expression"):
            return None
        node = nxt
    return None


__all__ = ["declarative_flat"]
