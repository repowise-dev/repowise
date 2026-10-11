"""What a loop walks, for dialects that describe their syntax through hooks.

Go, Java, C# and Ruby share one reading of a loop header: a literal is a
constant, a fixed-width cap bounds the trip count of whatever it caps, a read
that leaves the process returns rows that grow unless it is capped or keyed
on one id, a parameter is whatever the caller passes, and a cursor walks what
its receiver was bound to. Each dialect names its own node kinds and method
names (and its binding statements); anything this reading does not settle
stays ``unknown``, which no reader takes as bounded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from .. import loop_lexicon as lex
from ..loop_facts import LoopSource
from .base import BasePerfDialect

if TYPE_CHECKING:
    from collections.abc import Callable

    from tree_sitter import Node

    from ..loop_facts import LoopIterates, LoopMagnitude, SinkProbe

    Source = tuple[LoopIterates, LoopMagnitude | None]
    # The value a statement binds to a name, or ``None`` when it binds none.
    BindingReader = Callable[[Node, bytes], Node | None]

_UNKNOWN: Source = ("unknown", None)
_BLOCKS = frozenset({"block", "do_block"})
_SPLATS = frozenset({"splat_argument", "hash_splat_argument", "variadic_argument"})
# What a capping call's receiver may be when the cap belongs to a read.
_READS = frozenset({"unknown", "query", "caller_key", "rare_subset"})
# Statements that step or grow the name on their left, and methods that grow
# their receiver.
_CHANGES = frozenset(
    {
        "assignment", "assignment_statement", "assignment_expression", "operator_assignment",
        "augmented_assignment", "inc_statement", "dec_statement", "update_expression",
        "postfix_unary_expression", "prefix_unary_expression", "binary",
    }
)
_GROWS = frozenset(
    {"Add", "AddRange", "add", "addAll", "push", "append", "Push", "insert", "Insert",
     "concat", "put", "Put", "store", "unshift", "Enqueue", "offer"}
)


def text_of(node: Node | None) -> str:
    return (node.text or b"").decode("utf-8", "replace") if node is not None else ""


def upper_bound(cond: Node | None) -> Node | None:
    """``n`` of a counted loop's ``i < n`` / ``i <= n``; ``None`` for any other condition."""
    if cond is None or cond.type != "binary_expression":
        return None
    if not any(c.type in ("<", "<=") for c in cond.children):
        return None
    return cond.child_by_field_name("right")


class IterableSources(BasePerfDialect):
    """``loop_source`` read off a dialect's loop header.

    A dialect supplies :meth:`loop_header`, :meth:`call_parts`,
    :meth:`reaching_value` and :meth:`param_names`, plus the vocabularies below.
    """

    # Function scopes a binding or parameter belongs to.
    function_kinds: frozenset[str] = frozenset()
    # Inline collection literals, integer literals and name nodes.
    literal_kinds: frozenset[str] = frozenset()
    int_kinds: frozenset[str] = frozenset()
    name_kinds: frozenset[str] = frozenset({"identifier"})
    # Wrappers read through (``(x)``, ``await x``).
    wrapper_kinds: frozenset[str] = frozenset()
    # ``recv.m()`` walks ``recv``; ``m(x)`` walks ``x``; ``m(n)`` keeps at most n.
    pass_through: frozenset[str] = frozenset()
    arg_walkers: frozenset[str] = frozenset()
    caps: frozenset[str] = frozenset()
    # ``len(xs)`` / ``xs.size()``: the size of what the counted loop walks.
    size_names: frozenset[str] = frozenset()
    # ``rows.Next()`` / ``rs.next()``: a cursor that walks its receiver.
    cursor_advances: frozenset[str] = frozenset()
    listings: frozenset[str] = frozenset()
    streams: frozenset[str] = frozenset()
    # Method names that cap a read in the query (``Limit(10)``, ``take(5)``).
    query_caps: frozenset[str] = frozenset()
    # Text that makes a listing recursive (``**``, ``AllDirectories``).
    recursive_marks: ClassVar[tuple[bytes, ...]] = (b"**",)
    recursive_listings: frozenset[str] = frozenset()

    # -- hooks -----------------------------------------------------------------

    def loop_header(self, loop: Node) -> tuple[str, Node | None]:
        """``("each", iterable)``, ``("counted", stop)`` or ``("cursor", call)``."""
        return "each", None

    def call_parts(self, node: Node) -> tuple[str, Node | None, list[Node]] | None:
        """``(method, receiver, args)`` when *node* is a call, else ``None``."""
        return None

    def is_literal(self, node: Node) -> bool:
        """A collection the source spells out entry by entry. An empty one is
        filled later, and a splat (``[*xs]``) adds as many entries as ``xs`` holds."""
        entries = [c for c in node.named_children if c.type != "comment"]
        return (
            node.type in self.literal_kinds
            and bool(entries)
            and not any(child.type in _SPLATS for child in entries)
        )

    def member_parts(self, node: Node) -> tuple[str, Node] | None:
        """``(name, object)`` when *node* reads a property (``xs.Count``), else ``None``."""
        return None

    def reaching_value(self, loop: Node, name: bytes) -> Node | None:
        """The value the last binding of *name* before *loop* gives it."""
        return None

    def param_names(self, loop: Node) -> set[bytes]:
        """The parameters of the function *loop* sits in."""
        return set()

    def special_call(
        self,
        call: Node,
        parts: tuple[str, Node | None, list[Node]],
        loop: Node,
        probe: SinkProbe,
    ) -> Source | None:
        """A dialect's own reading of a call (``List.of(a, b)``, ``Role.values()``)."""
        return None

    def query_chain(self, node: Node) -> bool:
        """A read the sink probe does not see (an ActiveRecord relation)."""
        return False

    def fixed_slice(self, node: Node) -> Node | None:
        """The operand of a fixed-width slice (``xs[:5]``), else ``None``."""
        return None

    # -- shared reading ----------------------------------------------------------

    def loop_source(self, loop: Node, probe: SinkProbe) -> LoopSource | None:
        kind, node = self.loop_header(loop)
        if node is None:
            return None
        if kind == "counted":
            iterates, magnitude = self._counted(node, loop, probe)
        elif kind == "cursor":
            iterates, magnitude = self._cursor(node, loop, probe)
        else:
            iterates, magnitude = self._iterable(node, loop, probe, hop=True)
        if iterates == "unknown" and magnitude is None:
            return None
        # Every rule here reads syntax alone, so the claim is its own baseline.
        claim: LoopMagnitude = magnitude or "unknown"
        return LoopSource(iterates, claim, claim)

    def _unwrap(self, node: Node) -> Node:
        while node.type in self.wrapper_kinds:
            inner = next((c for c in node.named_children if c.type != "comment"), None)
            if inner is None:
                break
            node = inner
        return node

    def _iterable(self, expr: Node | None, loop: Node, probe: SinkProbe, *, hop: bool) -> Source:
        if expr is None:
            return _UNKNOWN
        node = self._unwrap(expr)
        if self.is_literal(node):
            return "constant", "bounded"
        if node.type in self.int_kinds:
            return "constant", "bounded"  # Go ``range 10``
        if node.type in self.name_kinds:
            return self._name(node, loop, probe, hop=hop)
        sliced = self.fixed_slice(node)
        if sliced is not None:
            # A fixed-width slice bounds the trip count, not what is walked.
            return self._iterable(sliced, loop, probe, hop=hop)[0], "bounded"
        parts = self.call_parts(node)
        if parts is not None:
            return self._call(node, parts, loop, probe, hop=hop)
        member = self.member_parts(node)
        if member is not None and member[0] in self.pass_through:
            return self._iterable(member[1], loop, probe, hop=hop)
        if member is not None and self.query_chain(node):
            return self._query(node, loop)
        if lex.is_config_name(text_of(node)):
            return "config_table", None
        return _UNKNOWN

    def _name(self, name: Node, loop: Node, probe: SinkProbe, *, hop: bool) -> Source:
        value = self.reaching_value(loop, name.text or b"") if hop else None
        if value is not None:
            found = self._iterable(value, loop, probe, hop=False)
            if found[0] == "constant" and self._changed_before(value, loop, name.text or b""):
                found = _UNKNOWN
            if found != _UNKNOWN:
                return found
        if (name.text or b"") in self.param_names(loop):
            return "parameter", None
        if lex.is_config_name(text_of(name)):
            # A registry grows with what users register: the name alone bounds nothing.
            return "config_table", None
        return _UNKNOWN

    def _call(
        self,
        call: Node,
        parts: tuple[str, Node | None, list[Node]],
        loop: Node,
        probe: SinkProbe,
        *,
        hop: bool,
    ) -> Source:
        method, receiver, args = parts
        found = self.special_call(call, parts, loop, probe) or self._read_or_cap(
            call, parts, loop, probe, hop=hop
        )
        if found is not None:
            return found
        if method in self.pass_through and receiver is not None:
            return self._iterable(receiver, loop, probe, hop=hop)
        if method in self.arg_walkers and args:
            return self._iterable(args[0], loop, probe, hop=hop)
        if method in self.listings:
            return "listing", "grows_with_data" if self._walks_repo(call, method, loop) else None
        if method in self.streams:
            return "stream", None
        if lex.is_config_name(method, callee=True):
            return "config_table", None
        return _UNKNOWN

    def _read_or_cap(
        self,
        call: Node,
        parts: tuple[str, Node | None, list[Node]],
        loop: Node,
        probe: SinkProbe,
        *,
        hop: bool,
    ) -> Source | None:
        """A read that leaves the process, or a cap on what the loop walks.

        A cap on what is already in hand bounds the walk (``ids.first(5)``); a
        cap inside a read is the read's (``User.limit(10)``).
        """
        method, receiver, args = parts
        capped = method in self.caps and receiver is not None and self._int_arg(args)
        walked = self._iterable(receiver, loop, probe, hop=hop)[0] if capped else "unknown"
        if capped and walked not in _READS:
            return walked, "bounded"
        if probe(call) in ("db", "network") or self.query_chain(call):
            return self._query(call, loop)
        if capped and method in self.query_caps:
            return walked, None  # likely a read's own cap, which proves nothing alone
        return (walked, "bounded") if capped else None

    def _int_arg(self, args: list[Node]) -> bool:
        return len(args) == 1 and self._unwrap(args[0]).type in self.int_kinds

    def _counted(self, stop: Node, loop: Node, probe: SinkProbe) -> Source:
        """``i < 3`` is a constant; ``i < len(rows)`` walks ``rows``; ``i < n`` a parameter."""
        node = self._unwrap(stop)
        if node.type in self.int_kinds:
            return "constant", "bounded"
        if node.type in self.name_kinds:
            if (node.text or b"") in self.param_names(loop):
                return "parameter", None
            value = self.reaching_value(loop, node.text or b"")
            if value is None or self._changed_before(value, loop, node.text or b""):
                return _UNKNOWN
            return self._counted(value, loop, probe)
        sized = self._sized(node)
        return self._iterable(sized, loop, probe, hop=True) if sized is not None else _UNKNOWN

    def _sized(self, node: Node) -> Node | None:
        """``xs`` of ``len(xs)`` / ``xs.size()`` / ``xs.Count`` / ``xs.length``."""
        parts = self.call_parts(node)
        if parts is not None:
            method, receiver, args = parts
            if method not in self.size_names:
                return None
            if receiver is not None and not args:
                return receiver
            return args[0] if receiver is None and len(args) == 1 else None
        member = self.member_parts(node)
        return member[1] if member is not None and member[0] in self.size_names else None

    def _cursor(self, call: Node, loop: Node, probe: SinkProbe) -> Source:
        """``for rows.Next()`` / ``while (rs.next())``: the read the receiver holds."""
        parts = self.call_parts(self._unwrap(call))
        if parts is None or parts[0] not in self.cursor_advances or parts[1] is None:
            return _UNKNOWN
        return self._iterable(parts[1], loop, probe, hop=True)

    def _query(self, call: Node, loop: Node) -> Source:
        """Rows of one key, a capped read, a rare state, or rows that grow; the
        names the call reads may be bound to their statement before the loop."""
        # A Ruby ``find_each do ... end`` reads before its block runs.
        block = call.child_by_field_name("block")
        head = (call.text or b"")[: block.start_byte - call.start_byte] if block else call.text
        names = [n.text for n in self._walk(call, prune=_BLOCKS) if n.type in self.name_kinds]
        values = [v for name in dict.fromkeys(names) if (v := self.reaching_value(loop, name or b""))]
        nodes = [call, *values]
        text = b" ".join([head or b"", *(value.text or b"" for value in values)])
        if lex.is_key_lookup(text):
            return "caller_key", "bounded"
        # The children of one parent (``user_id = ?``) are as many as that parent
        # has, and a capped read is as large as its cap: neither settles a size.
        capped = lex.has_sql_limit(text) or any(self._caps_read(node) for node in nodes)
        if capped or lex.is_parent_key_filter(text):
            return "query", None
        return ("rare_subset" if lex.is_rare_filter(text) else "query"), "grows_with_data"

    def _caps_read(self, node: Node) -> bool:
        for sub in self._walk(node, prune=_BLOCKS):
            parts = self.call_parts(sub)
            if parts is not None and parts[0] in self.query_caps and parts[2]:
                return True
        return False

    def _walks_repo(self, call: Node, method: str, loop: Node) -> bool:
        """A recursive listing whose path names a repository root; any other path
        proves nothing about its size."""
        text = call.text or b""
        if method not in self.recursive_listings and not any(
            mark in text for mark in self.recursive_marks
        ):
            return False
        names = [n for n in self._walk(call) if n.type in self.name_kinds]
        for node in list(names):
            value = self.reaching_value(loop, node.text or b"")
            if value is not None:
                names.extend(n for n in self._walk(value) if n.type in self.name_kinds)
        return any(lex.names_repo_root(text_of(n)) for n in names)

    # -- helpers the dialects share ---------------------------------------------

    def _scope(self, loop: Node) -> Node | None:
        scope = loop.parent
        while scope is not None and scope.type not in self.function_kinds:
            scope = scope.parent
        return scope

    def last_binding_before(self, loop: Node, bind: BindingReader, name: bytes) -> Node | None:
        """The value of the last binding of *name* that ends before *loop*,
        in the function around it (nested functions skipped)."""
        scope = self._scope(loop)
        if scope is None:
            return None
        best: tuple[int, Node] | None = None
        for node in self._walk(scope, prune=self.function_kinds):
            if node.end_byte > loop.start_byte:
                continue
            value = bind(node, name)
            if value is not None and (best is None or node.start_byte > best[0]):
                best = (node.start_byte, value)
        return best[1] if best is not None else None

    def _changed_before(self, value: Node, loop: Node, name: bytes) -> bool:
        """*name* is grown or stepped between its binding to *value* and *loop*:
        ``xs.Add(x)`` / ``xs << x`` / ``m[k] = v`` / ``n++`` / ``n += 1``. A literal
        it was bound to then says nothing about its size."""
        scope = self._scope(loop)
        if scope is None:
            return True
        for node in self._walk(scope, prune=self.function_kinds):
            if node.start_byte < value.end_byte or node.end_byte > loop.start_byte:
                continue
            first = node.named_children[0] if node.named_children else None
            target = first.text or b"" if first is not None else b""
            if node.type == "binary" and text_of(node.child_by_field_name("operator")) != "<<":
                continue
            if node.type in _CHANGES and (target == name or target.startswith(name + b"[")):
                return True
            parts = self.call_parts(node)
            if parts is not None and parts[0] in _GROWS and text_of(parts[1]).encode() == name:
                return True
        return False

    def enclosing_params(self, loop: Node, param_kinds: frozenset[str]) -> set[bytes]:
        """Names the function around *loop* takes as parameters."""
        scope = self._scope(loop)
        params = scope.child_by_field_name("parameters") if scope is not None else None
        if params is None:
            return set()
        names: set[bytes] = set()
        for node in self._walk(params):
            if node.type in param_kinds:
                name = node.child_by_field_name("name")
                if name is not None and name.text:
                    names.add(name.text)
        return names


__all__ = ["IterableSources", "text_of", "upper_bound"]
