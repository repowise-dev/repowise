"""Rust ``unwrap`` / ``expect`` / panic-macro verdicts for ``error_handling``.

An ``.unwrap()`` is a latent crash only when the value can actually be ``None``
or ``Err``. Two shapes provably cannot, so they are no finding:

- a guarded unwrap: the receiver was checked in an enclosing ``if`` or a
  preceding ``&&`` operand (``x.is_some() && x.unwrap()``,
  ``!v.is_empty() && v.last().unwrap()``);
- a ``write!`` / ``writeln!`` into a local ``String``: ``fmt::Write`` for
  ``String`` never returns ``Err``.

A few more are true but idiomatic invariant assertions. They stay findings,
labelled with the idiom so the biomarker can say so and not score them:
``.lock().unwrap()`` (poison propagation), ``.join().unwrap()`` (re-raise a
thread's panic), ``expect("literal message")`` and ``unreachable!``.

Purely syntactic. A guard does not count once the guarded block rebinds or
assigns the receiver before the unwrap. Ceiling: an early-return guard
(``if v.is_empty() { return; }``) stays a finding, and a mutation through a
method call between the check and the unwrap (``v.clear()``) is not seen.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node

# Adapters that keep the Some/Ok-ness the guard checked.
_GUARD_ADAPTERS = frozenset({"as_ref", "as_mut", "as_deref", "as_deref_mut", "map"})
# Guards that prove the receiver itself is Some / Ok.
_PRESENT_GUARDS = (".is_some()", ".is_ok()")
# Accessors that are Some exactly when the collection is non-empty.
_NONEMPTY_ACCESSORS = (".last()", ".first()", ".last_mut()", ".first_mut()", ".pop()")
_NONEMPTY_GUARDS = ("!{v}.is_empty()", "{v}.len()>0", "{v}.len()!=0", "{v}.len()>=1")

_STRING_WRITE_MACROS = frozenset({"write", "writeln"})
_STRING_CTORS = ("String::new(", "String::with_capacity(", "String::from(")
_STRING_TYPES = frozenset({"String", "&mutString"})

# Zero-argument receiver call -> idiom. ``read``/``write`` take no argument
# only on ``RwLock``; ``io::Read::read(buf)`` and ``io::Write::write(buf)`` do.
_SYNC_RECEIVER_IDIOMS = {
    "lock": "lock_poison",
    "read": "lock_poison",
    "write": "lock_poison",
    "join": "thread_join",
}
_INVARIANT_MACROS = frozenset({"unreachable"})

_SCOPE_KINDS = frozenset({"function_item", "closure_expression"})
# Nodes whose ``pattern`` field binds names, and pattern nodes that bind any
# identifier they hold directly.
_PATTERN_FIELD_OWNERS = frozenset({"let_declaration", "parameter", "for_expression", "let_condition"})
_PATTERN_KINDS = frozenset(
    {
        "closure_parameters",
        "tuple_pattern",
        "tuple_struct_pattern",
        "slice_pattern",
        "or_pattern",
        "ref_pattern",
        "mut_pattern",
        "reference_pattern",
        "captured_pattern",
        "field_pattern",
    }
)
_ROOT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _text(node: Node | None) -> str:
    return (node.text or b"").decode("utf-8", errors="replace") if node is not None else ""


def _norm(node: Node | None) -> str:
    return "".join(_text(node).split())


def _same(a: Node | None, b: Node) -> bool:
    return a is not None and (a.start_byte, a.end_byte) == (b.start_byte, b.end_byte)


def method_call_parts(node: Node) -> tuple[str, Node | None, Node | None]:
    """``(method name, receiver, arguments)`` of ``recv.method(args)``, else ``("", None, None)``."""
    fn = node.child_by_field_name("function") if node.type == "call_expression" else None
    if fn is None or fn.type != "field_expression":
        return "", None, None
    method = _text(fn.child_by_field_name("field"))
    return method, fn.child_by_field_name("value"), node.child_by_field_name("arguments")


def _peel(node: Node) -> Node | None:
    """The expression inside one Some/Ok-preserving layer of *node*, else ``None``."""
    method, receiver, _ = method_call_parts(node)
    if method:
        return receiver if method in _GUARD_ADAPTERS else None
    is_deref = node.type == "unary_expression" and node.children[0].type == "*"
    if is_deref or node.type in ("parenthesized_expression", "reference_expression"):
        return node.named_children[-1] if node.named_child_count else None
    return None


def _base_receiver(node: Node) -> Node:
    """*node* with Some/Ok-preserving adapters, parentheses and ``*``/``&`` peeled."""
    inner = _peel(node)
    while inner is not None:
        node, inner = inner, _peel(inner)
    return node


def _guard_keys(receiver: Node) -> frozenset[str]:
    base = _norm(_base_receiver(receiver))
    keys = {base + guard for guard in _PRESENT_GUARDS}
    for accessor in _NONEMPTY_ACCESSORS:
        if base.endswith(accessor):
            coll = base[: -len(accessor)]
            keys.update(g.format(v=coll) for g in _NONEMPTY_GUARDS)
    return frozenset(keys)


def _conjuncts(cond: Node) -> list[str]:
    """The top-level ``&&`` operands of *cond*, whitespace-stripped."""
    while cond.type == "parenthesized_expression" and cond.named_child_count == 1:
        cond = cond.named_children[0]
    op = cond.child_by_field_name("operator") if cond.type == "binary_expression" else None
    if op is not None and op.type == "&&":
        left, right = cond.child_by_field_name("left"), cond.child_by_field_name("right")
        return _conjuncts(left) + _conjuncts(right)
    return [_norm(cond)]


def _guarding_condition(parent: Node, child: Node) -> Node | None:
    """The condition that must hold for *child* (a child of *parent*) to run."""
    if parent.type == "if_expression" and _same(parent.child_by_field_name("consequence"), child):
        return parent.child_by_field_name("condition")
    if parent.type == "binary_expression":
        op = parent.child_by_field_name("operator")
        if op is not None and op.type == "&&" and _same(parent.child_by_field_name("right"), child):
            return parent.child_by_field_name("left")
    return None


def _descendants(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(cur.named_children)


def _is_binder(ident: Node) -> bool:
    """*ident* names a new binding (a ``let``/parameter/closure/``for`` pattern)."""
    parent = ident.parent
    if parent is None:
        return False
    if parent.type in _PATTERN_FIELD_OWNERS:
        return _same(parent.child_by_field_name("pattern"), ident)
    return parent.type in _PATTERN_KINDS


def _binders(scope: Node, name: str, before: int | None = None) -> list[Node]:
    """Every binding of *name* in *scope*, those starting before byte *before* if given."""
    end = scope.end_byte if before is None else before
    named = (n for n in _descendants(scope) if n.type == "identifier" and _text(n) == name)
    return [n for n in named if n.start_byte < end and _is_binder(n)]


def _rebinds(region: Node, base: str, call: Node) -> bool:
    """*region* rebinds or assigns the guarded receiver before *call* runs."""
    root = _ROOT_NAME.match(base)
    if root is not None and root.group() != "self" and _binders(region, root.group(), call.start_byte):
        return True
    return any(
        n.type in ("assignment_expression", "compound_assignment_expr")
        and n.start_byte < call.start_byte
        and _norm(n.child_by_field_name("left")) in (base, root.group() if root else base)
        for n in _descendants(region)
    )


def _is_guarded(call: Node, receiver: Node) -> bool:
    keys = _guard_keys(receiver)
    child, parent = call, call.parent
    while parent is not None and parent.type not in _SCOPE_KINDS:
        cond = _guarding_condition(parent, child)
        if cond is not None and keys.intersection(_conjuncts(cond)):
            return not _rebinds(child, _norm(_base_receiver(receiver)), call)
        child, parent = parent, parent.parent
    return False


def _write_target(macro: Node) -> str | None:
    """The first argument of a ``write!``/``writeln!`` call, ``&mut`` dropped."""
    name = macro.child_by_field_name("macro")
    if _text(name) not in _STRING_WRITE_MACROS:
        return None
    args = next((c for c in macro.children if c.type == "token_tree"), None)
    body = _norm(args)[1:-1]
    first = body.split(",", 1)[0]
    return first.removeprefix("&mut") or None


def _declares_string(scope: Node, name: str) -> bool:
    """*scope* binds *name* exactly once, as a ``String`` (a ``let`` or a parameter).

    A second binding anywhere in the function (a shadowing ``let``, a closure
    parameter) could be what the write reaches, so it proves nothing.
    """
    binders = _binders(scope, name)
    if len(binders) != 1:
        return False
    decl = binders[0].parent
    if decl.type not in ("let_declaration", "parameter"):
        return False
    return _norm(decl.child_by_field_name("type")) in _STRING_TYPES or _norm(
        decl.child_by_field_name("value")
    ).startswith(_STRING_CTORS)


def _writes_to_string(receiver: Node) -> bool:
    if receiver.type != "macro_invocation":
        return False
    target = _write_target(receiver)
    if not target:
        return False
    scope = receiver.parent
    while scope is not None and scope.type != "function_item":
        scope = scope.parent
    return scope is not None and _declares_string(scope, target)


def cannot_panic(call: Node) -> bool:
    """True when the ``unwrap``/``expect`` *call* provably cannot panic."""
    receiver = method_call_parts(call)[1]
    if receiver is None:
        return False
    return _writes_to_string(receiver) or _is_guarded(call, receiver)


def _sync_idiom(receiver: Node | None) -> str | None:
    if receiver is None:
        return None
    method, _, args = method_call_parts(receiver)
    if args is None or args.named_child_count:
        return None
    return _SYNC_RECEIVER_IDIOMS.get(method)


def _expect_message_is_literal(call: Node) -> bool:
    args = call.child_by_field_name("arguments")
    named = args.named_children if args is not None else []
    if len(named) != 1:
        return False
    arg = named[0]
    # A string literal, or a SCREAMING_CASE identifier (a ``const`` message).
    return arg.type in ("string_literal", "raw_string_literal") or (
        arg.type == "identifier" and _text(arg).isupper()
    )


def idiom(node: Node) -> str | None:
    """The idiomatic-assertion label for a Rust error-handling hit, if any."""
    if node.type == "macro_invocation":
        mac = node.child_by_field_name("macro")
        return "unreachable" if _text(mac) in _INVARIANT_MACROS else None
    sync = _sync_idiom(method_call_parts(node)[1])
    if sync is not None:
        return sync
    if method_call_parts(node)[0] == "expect" and _expect_message_is_literal(node):
        return "invariant_expect"
    return None


def anchor_line(node: Node) -> int:
    """1-based line of the ``.unwrap``/``.expect`` token, not the chain start."""
    if node.type == "call_expression":
        fn = node.child_by_field_name("function")
        fld = fn.child_by_field_name("field") if fn is not None else None
        if fld is not None:
            return fld.start_point[0] + 1
    return node.start_point[0] + 1
