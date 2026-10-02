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

Purely syntactic. Ceiling: an early-return guard (``if v.is_empty() {
return; }``) or a mutation between the check and the unwrap is not tracked;
the first stays a finding, the second would be missed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


def _text(node: Node | None) -> str:
    return (node.text or b"").decode("utf-8", errors="replace") if node is not None else ""


def _norm(node: Node | None) -> str:
    return "".join(_text(node).split())


def _same(a: Node | None, b: Node) -> bool:
    return a is not None and (a.start_byte, a.end_byte) == (b.start_byte, b.end_byte)


def _receiver(call: Node) -> Node | None:
    fn = call.child_by_field_name("function")
    return fn.child_by_field_name("value") if fn is not None else None


def _base_receiver(node: Node) -> Node:
    """*node* with Some/Ok-preserving adapters, parentheses and ``*``/``&`` peeled."""
    while True:
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is None or fn.type != "field_expression":
                return node
            if _text(fn.child_by_field_name("field")) not in _GUARD_ADAPTERS:
                return node
            inner = fn.child_by_field_name("value")
        elif node.type in ("parenthesized_expression", "reference_expression") or (
            node.type == "unary_expression" and node.children[0].type == "*"
        ):
            inner = node.named_children[-1] if node.named_child_count else None
        else:
            return node
        if inner is None:
            return node
        node = inner


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


def _is_guarded(call: Node, receiver: Node) -> bool:
    keys = _guard_keys(receiver)
    child, parent = call, call.parent
    while parent is not None and parent.type not in _SCOPE_KINDS:
        cond = _guarding_condition(parent, child)
        if cond is not None and keys.intersection(_conjuncts(cond)):
            return True
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
    """*scope* binds *name* as a ``String`` (a ``let`` or a parameter)."""
    stack = [scope]
    while stack:
        node = stack.pop()
        if node.type in ("let_declaration", "parameter") and _norm(
            node.child_by_field_name("pattern")
        ) == name:
            if _norm(node.child_by_field_name("type")) in _STRING_TYPES:
                return True
            if _norm(node.child_by_field_name("value")).startswith(_STRING_CTORS):
                return True
        stack.extend(node.named_children)
    return False


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
    receiver = _receiver(call)
    if receiver is None:
        return False
    return _writes_to_string(receiver) or _is_guarded(call, receiver)


def _sync_idiom(receiver: Node | None) -> str | None:
    if receiver is None or receiver.type != "call_expression":
        return None
    args = receiver.child_by_field_name("arguments")
    fn = receiver.child_by_field_name("function")
    if args is None or args.named_child_count or fn is None or fn.type != "field_expression":
        return None
    return _SYNC_RECEIVER_IDIOMS.get(_text(fn.child_by_field_name("field")))


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
    sync = _sync_idiom(_receiver(node))
    if sync is not None:
        return sync
    fn = node.child_by_field_name("function")
    method = _text(fn.child_by_field_name("field")) if fn is not None else ""
    if method == "expect" and _expect_message_is_literal(node):
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
