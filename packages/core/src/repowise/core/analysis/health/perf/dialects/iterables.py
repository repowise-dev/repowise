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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from .. import loop_lexicon as lex
from ..loop_facts import LoopSource
from .base import BasePerfDialect

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..loop_facts import LoopIterates, LoopMagnitude, SinkProbe

    Source = tuple[LoopIterates, LoopMagnitude | None]

_UNKNOWN: Source = ("unknown", None)
_BLOCKS = frozenset({"block", "do_block"})
_SPLATS = frozenset({"splat_argument", "hash_splat_argument", "variadic_argument"})
# What a capping call's receiver may be when the cap belongs to a read.
_READS = frozenset({"unknown", "query", "caller_key", "rare_subset"})


def text_of(node: Node | None) -> str:
    return (node.text or b"").decode("utf-8", "replace") if node is not None else ""


def upper_bound(cond: Node | None) -> Node | None:
    """``n`` of a counted loop's ``i < n`` / ``i <= n``; ``None`` for any other condition."""
    if cond is None or cond.type != "binary_expression":
        return None
    if not any(c.type in ("<", "<=") for c in cond.children):
        return None
    return cond.child_by_field_name("right")


@dataclass(slots=True)
class ScopeFacts:
    """One function's bindings and mutations, read in one pass over its body.

    ``bindings``: name -> ``(statement, value)`` in source order; ``changes``:
    name -> start offsets of statements that grow or step it.
    """

    bindings: dict[bytes, list[tuple[Node, Node]]] = field(default_factory=dict)
    changes: dict[bytes, list[int]] = field(default_factory=dict)


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
    # Property reads a listing's path may name the repository root through.
    member_kinds: frozenset[str] = frozenset()
    # Statements that step or grow the name on their left (``n++``, ``m[k] = v``),
    # and methods that grow their receiver (``xs.Add(x)``).
    change_kinds: frozenset[str] = frozenset()
    grow_methods: frozenset[str] = frozenset()
    # Nodes whose body may run zero times or one of several ways; a binding
    # under one does not surely reach code after it (its condition does).
    conditional_kinds: frozenset[str] = frozenset()
    # ``None``: a dialect whose own syntax rules size a read (Python, TS).
    read_grows: LoopMagnitude | None = "grows_with_data"

    # The last scope read, held so its tree (and so the node id in the key) stays live.
    _facts_cache: tuple[Node, tuple[int, int, int], ScopeFacts] | None = None

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

    def bindings_of(self, node: Node) -> list[tuple[bytes, Node]]:
        """``(name, value)`` for each bare name the statement *node* binds."""
        return []

    def mutated_name(self, node: Node) -> bytes | None:
        """The name *node* grows or steps (``xs.Add(x)``, ``m[k] = v``, ``n++``)."""
        if node.type in self.change_kinds and node.named_children:
            return (node.named_children[0].text or b"").split(b"[", 1)[0]
        parts = self.call_parts(node) if self.grow_methods else None
        if parts is not None and parts[0] in self.grow_methods and parts[1] is not None:
            return parts[1].text
        return None

    def reads_config_table(self, call: Node, nodes: list[Node]) -> bool:
        """The read names only registry or configuration tables."""
        return False

    def is_cap(self, node: Node, loop: Node) -> bool:
        """*node* caps a read at a fixed count (``Limit(10)``, ``setMaxResults(5)``)."""
        parts = self.call_parts(node)
        return parts is not None and parts[0] in self.query_caps and bool(parts[2])

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
        return LoopSource(iterates, magnitude or "unknown")

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
        if self.reads_config_table(call, nodes):
            return "config_table", None
        text = b" ".join([head or b"", *(value.text or b"" for value in values)])
        if lex.is_key_lookup(text):
            return "caller_key", "bounded"
        # The children of one parent (``user_id = ?``) are as many as that parent
        # has, and a capped read is as large as its cap: neither settles a size.
        capped = lex.has_sql_limit(text) or any(
            self.is_cap(sub, loop) for node in nodes for sub in self._walk(node, prune=_BLOCKS)
        )
        if capped or lex.is_parent_key_filter(text):
            return "query", None
        return ("rare_subset" if lex.is_rare_filter(text) else "query"), self.read_grows

    def _walks_repo(self, call: Node, method: str, loop: Node) -> bool:
        """A recursive listing whose path names a repository root; any other path
        proves nothing about its size."""
        text = call.text or b""
        if method not in self.recursive_listings and not any(
            mark in text for mark in self.recursive_marks
        ):
            return False
        kinds = self.name_kinds | self.member_kinds
        names = [n for n in self._walk(call) if n.type in kinds]
        for node in [n for n in names if n.type in self.name_kinds]:
            value = self.reaching_value(loop, node.text or b"")
            if value is not None:
                names.extend(n for n in self._walk(value) if n.type in kinds)
        return any(lex.names_repo_root(text_of(n)) for n in names)

    # -- helpers the dialects share ---------------------------------------------

    def _scope(self, loop: Node) -> Node | None:
        scope = loop.parent
        while scope is not None and scope.type not in self.function_kinds:
            scope = scope.parent
        return scope

    def scope_facts(self, anchor: Node) -> ScopeFacts | None:
        """The bindings and mutations of the function around *anchor*, read once
        per function: every loop and call in it asks the same map."""
        scope = self._scope(anchor)
        if scope is None:
            return None
        key = (scope.id, scope.start_byte, scope.end_byte)
        cached = self._facts_cache
        if cached is not None and cached[1] == key:
            return cached[2]
        facts = ScopeFacts()
        for node in self._walk(scope, prune=self.function_kinds):
            for name, value in self.bindings_of(node):
                facts.bindings.setdefault(name, []).append((node, value))
            changed = self.mutated_name(node)
            if changed:
                facts.changes.setdefault(changed, []).append(node.start_byte)
        self._facts_cache = (scope, key, facts)
        return facts

    def bindings_before(self, anchor: Node, name: bytes) -> list[tuple[Node, Node]]:
        """Every ``(statement, value)`` binding *name* that ends before *anchor*."""
        facts = self.scope_facts(anchor)
        found = facts.bindings.get(name, ()) if facts is not None else ()
        return [(stmt, value) for stmt, value in found if stmt.end_byte <= anchor.start_byte]

    def reaching_value(self, loop: Node, name: bytes) -> Node | None:
        """The value *name* surely holds at *loop*: its one binding before the
        loop, made where the loop also runs. A second binding, or one under a
        branch or loop the target loop is not in, leaves it unknown."""
        before = self.bindings_before(loop, name)
        if len(before) != 1 or not self._surely_reaches(before[0][0], loop):
            return None
        return before[0][1]

    def _surely_reaches(self, stmt: Node, loop: Node) -> bool:
        """Every node between *stmt* and the first one holding *loop* runs
        whenever that one does (a branch's condition or initializer does)."""
        prev, cur = stmt, stmt.parent
        while cur is not None and not (
            cur.start_byte <= loop.start_byte and loop.end_byte <= cur.end_byte
        ):
            if cur.type in self.conditional_kinds and prev not in (
                cur.child_by_field_name("condition"),
                cur.child_by_field_name("initializer"),
            ):
                return False
            prev, cur = cur, cur.parent
        return cur is not None

    def _changed_before(self, value: Node, loop: Node, name: bytes) -> bool:
        """*name* is grown or stepped between its binding to *value* and *loop*:
        a literal it was bound to then says nothing about its size."""
        facts = self.scope_facts(loop)
        if facts is None:
            return True
        start, end = value.end_byte, loop.start_byte
        return any(start <= at < end for at in facts.changes.get(name, ()))

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


__all__ = ["IterableSources", "ScopeFacts", "text_of", "upper_bound"]
