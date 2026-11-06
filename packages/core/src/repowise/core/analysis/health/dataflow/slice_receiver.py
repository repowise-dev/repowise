"""What a span or a whole function does with its receiver (``self`` / ``this``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..complexity.ast_utils import member_object, self_member_name
from ..complexity.body_facts import BodyTally, FactKinds, is_exit
from .dialects.base import SUPER, mentions_receiver
from .slice_nodes import _exit_macros

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap
    from .defuse import FunctionDefUse
    from .dialects.base import Receiver
    from .slice import _Prefix


@dataclass(frozen=True)
class FunctionFacts:
    """What a whole function does, counted on the complexity walk's visits.

    ``awaits``: it suspends outside any nested scope. ``is_generator``: it
    yields. ``uses_receiver`` / ``receiver_assigns`` as on :class:`Extraction`,
    for the whole body; ``receiver_assigns`` is None where an implicit receiver
    (Java, C++) can write fields by bare name, which only def/use can tell.
    ``early_exits``: returns and raises other than a final one.
    """

    awaits: bool
    is_generator: bool
    uses_receiver: bool | None
    receiver_assigns: tuple[str, ...] | None
    early_exits: int


class _ReceiverSink:
    """``BodyTally.on_receiver``: whether the body names its receiver, and
    which of its fields it assigns."""

    __slots__ = ("assigns", "receiver", "uses")

    def __init__(self, receiver: Receiver) -> None:
        self.receiver = receiver
        self.uses = False
        self.assigns: set[str] = set()

    def __call__(self, node: Node) -> None:
        self.uses = _receiver_ref(node, self.receiver, self.assigns) or self.uses


def body_tally(fn_node: Node, lmap: LanguageNodeMap, receiver: Receiver | None) -> BodyTally:
    """An empty tally for *fn_node*, for the complexity walk to fill. The
    receiver is watched only when the function's text names it."""
    watched = bool(
        receiver is not None and receiver.names and mentions_receiver(fn_node, receiver.names)
    )
    sink = _ReceiverSink(receiver) if watched and receiver is not None else None
    return BodyTally(_function_kinds(lmap, sink), sink)


# Built once per language map and receiver shape: the walk asks per function.
_FUNCTION_KINDS: dict[tuple[int, frozenset[str]], FactKinds] = {}


def _function_kinds(lmap: LanguageNodeMap, sink: _ReceiverSink | None) -> FactKinds:
    receiver = _receiver_kinds(sink.receiver) if sink else frozenset()
    key = (id(lmap), receiver)
    kinds = _FUNCTION_KINDS.get(key)
    if kinds is None:
        kinds = _FUNCTION_KINDS[key] = FactKinds(
            exits=lmap.return_kinds | lmap.raise_kinds,
            exit_macros=_exit_macros(lmap),
            yields=lmap.yield_kinds,
            awaits=lmap.await_kinds,
            await_scopes=lmap.await_scope_kinds,
            lambdas=lmap.lambda_kinds,
            receiver=receiver,
        )
    return kinds


def _receiver_kinds(receiver: Receiver) -> frozenset[str]:
    """What :func:`_receiver_ref` can answer for: a write, or a receiver leaf."""
    return receiver.write_kinds | receiver.names | {SUPER, "identifier"}


def function_facts(
    fn_node: Node, lmap: LanguageNodeMap, receiver: Receiver | None, tally: BodyTally
) -> FunctionFacts:
    """:class:`FunctionFacts` from a filled :func:`body_tally`."""
    body = fn_node.child_by_field_name("body") or fn_node
    sink = tally.on_receiver if isinstance(tally.on_receiver, _ReceiverSink) else None
    uses, assigns = _whole_receiver_facts(
        receiver, sink.uses if sink else False, frozenset(sink.assigns) if sink else frozenset()
    )
    return FunctionFacts(
        awaits=tally.awaits,
        is_generator=tally.yields > 0,
        uses_receiver=uses,
        receiver_assigns=assigns,
        early_exits=max(0, tally.exits - _ends_in_exit(body, lmap, tally.kinds)),
    )


def _ends_in_exit(body: Node, lmap: LanguageNodeMap, kinds: FactKinds) -> bool:
    """Whether a block body's last statement leaves the function, so that exit
    is not an early one. The statement is the exit (Python ``return x``, TS
    ``throw e;``) or wraps it as its first named child (Rust ``return x;`` and
    ``bail!(..);`` are expression statements). An expression body has no
    statements, so its exits are early by definition and none are subtracted."""
    if body.type not in lmap.block_kinds or not body.named_children:
        return False
    tail = body.named_children[-1]
    if is_exit(tail, kinds):
        return True
    return bool(tail.named_children) and is_exit(tail.named_children[0], kinds)


def _whole_receiver_facts(
    receiver: Receiver | None, uses: bool, assigned: frozenset[str]
) -> tuple[bool | None, tuple[str, ...] | None]:
    if receiver is None:
        return None, None
    if not receiver.names:
        return False, ()
    if receiver.implicit:
        return (True if uses else None), None
    return uses, None if _UNREAD_TARGET in assigned else tuple(sorted(assigned))


def _free_names(def_use: FunctionDefUse) -> frozenset[str]:
    params = {p.name for p in def_use.params}
    declared = {d.var for d in def_use.definitions if d.declares or d.declared_at is not None}
    return frozenset(
        d.var for d in def_use.definitions if d.var not in params and d.var not in declared
    )


def _free_write_lines(def_use: FunctionDefUse) -> list[int]:
    """Sorted lines writing a name the function neither declares nor takes
    as a parameter: in Java or C++ that is a field (or a C++ global) written
    without ``this``, so the span's receiver writes are not all known."""
    params = {p.name for p in def_use.params}
    declared = {d.var for d in def_use.definitions if d.declares or d.declared_at is not None}
    return sorted(
        d.line for d in def_use.definitions if d.var not in params and d.var not in declared
    )


def _receiver_facts(
    receiver: Receiver | None, pre: _Prefix, i: int, j: int, free_writes: int
) -> tuple[bool | None, tuple[str, ...] | None]:
    """``uses_receiver`` / ``receiver_assigns`` for the span over statements
    ``i..j`` (see :class:`Extraction`)."""
    if receiver is None:
        return None, None
    if not receiver.names:
        return False, ()
    named = pre.receiver[j + 1] > pre.receiver[i]
    found = frozenset().union(*pre.assigns[i : j + 1])
    fields = None if _UNREAD_TARGET in found else tuple(sorted(found))
    if receiver.implicit:
        return (True if (named or free_writes) else None), (None if free_writes else fields)
    return named, fields


# Index access on a receiver field (``self.cache[k] = v``) assigns into it.
_INDEX_KINDS = frozenset({"subscript", "subscript_expression", "index_expression", "array_access"})
# Unpacking targets whose named children are targets themselves
# (``a, self.b = ...``, ``[this.x, y] = ...``).
_UNPACK_KINDS = frozenset(
    {
        "pattern_list",
        "tuple_pattern",
        "list_pattern",
        "expression_list",
        "tuple",
        "list",
        "array_pattern",
        "object_pattern",
        "parenthesized_expression",
        "list_splat_pattern",
        "rest_pattern",
    }
)
# Marks a target rooted at the receiver whose shape is not read, so the
# span's assigned fields are unknown.
_UNREAD_TARGET = ""


def _receiver_ref(node: Node, receiver: Receiver, assigns: set[str]) -> bool:
    """True when *node* names the receiver; an assignment to one of its
    fields adds the field's name to *assigns* (the receiver leaf under it is
    visited next)."""
    if node.type in receiver.write_kinds:
        target = (
            node.child_by_field_name("left")
            or node.child_by_field_name("argument")
            or (node.named_children[0] if node.named_children else None)
        )
        if target is not None:
            _collect_assigned(target, receiver, assigns)
        return False
    return _is_receiver(node, receiver)


def _collect_assigned(target: Node, receiver: Receiver, assigns: set[str]) -> None:
    """The receiver fields one assignment target writes, unpacking included;
    a target of another shape that mentions the receiver makes them unknown."""
    if target.type in _UNPACK_KINDS:
        for each in target.named_children:
            _collect_assigned(each, receiver, assigns)
        return
    if target.type in ("assignment_pattern", "object_assignment_pattern"):  # ``[this.a = 1]``
        left = target.child_by_field_name("left")
        if left is not None:
            _collect_assigned(left, receiver, assigns)
        return
    if target.type == "pair_pattern":  # ``{key: this.a}``
        value = target.child_by_field_name("value")
        if value is not None:
            _collect_assigned(value, receiver, assigns)
        return
    field = _receiver_field(target, receiver)
    if field:
        assigns.add(field)
    elif target.type not in receiver.access_kinds | _INDEX_KINDS and mentions_receiver(
        target, receiver.names
    ):
        assigns.add(_UNREAD_TARGET)


def _is_receiver(node: Node, receiver: Receiver) -> bool:
    """A leaf naming the receiver: a ``this`` / ``self`` / ``super`` token,
    or an identifier spelling a named receiver (Python ``self``, Go ``s``)
    or ``super`` (Python's no-argument ``super()``)."""
    if node.children or not node.text:
        return False
    if node.type in receiver.names or node.type == SUPER:
        return True
    if node.type != "identifier":
        return False
    text = node.text.decode("utf-8", "replace")
    return text in receiver.names or text == SUPER


def _receiver_field(target: Node, receiver: Receiver) -> str | None:
    """``x`` for an assignment target ``self.x``, ``self.x.y`` or ``self.x[k]``."""
    cur: Node | None = target
    while cur is not None and (cur.type in receiver.access_kinds or cur.type in _INDEX_KINDS):
        if cur.type in receiver.access_kinds:
            field = self_member_name(cur, receiver.names)
            if field is not None:
                return field
        cur = member_object(cur)
    return None
