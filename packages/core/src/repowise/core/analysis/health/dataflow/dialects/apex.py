"""Apex ``DefUseDialect``.

Apex's statement grammar is Java's (tree-sitter-sfapex keeps Java's node and
field names), so this extends the Java dialect for what Apex adds:

- Identifiers are case-insensitive: ``toInsert`` and ``toinsert`` name one
  local. Every occurrence takes the spelling of the name's declaration in its
  method (:func:`_spellings`), so def/use and liveness compare one name, and a
  plan writes the name as it was declared. Two declarations spelled
  differently fold to the lowercase form, which no declared type matches, so
  their types are left as placeholders rather than guessed.
- Inline SOQL / SOSL (``[SELECT Id FROM Account WHERE Name = :n]``) reads only
  its ``:n`` bind expressions; field and object names are schema, not locals.
- ``new Account(Name = 'x')`` sets a field of the record: the ``Name =`` pair
  writes no local (Apex allows name=value pairs only in an SObject constructor).
- ``switch on x { when Account a { } }`` binds ``a`` for its arm; ``when``
  values are literals or enum constants, never variable reads. A ``break`` or
  ``continue`` in an arm leaves the enclosing loop (an arm never falls through),
  but the CFG keeps the switch whole, so the state at that point reaching the
  loop's exit or next iteration is modelled as a read, at the switch, of every
  name the loop mentions.
- ``System.runAs(u) { }`` is a ``with``-like block (``with_kinds``): its head
  reads ``u``.
- ``catch (DmlException e)`` declares ``e`` with a ``formal_parameter``.
- A ``static`` method has no instance, but a bare name in it can still be a
  static field, so its receiver is implicit with no name: such writes are never
  taken for locals, and its helper is written static.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import (
    HandlerHead,
    Occurrence,
    Receiver,
    StatementDefUse,
    echoes,
    may_defs,
    node_text,
)
from .java import JavaDeclaredTypes, JavaDefUseDialect

if TYPE_CHECKING:
    from tree_sitter import Node

    from ...complexity.languages import LanguageNodeMap

_QUERY = "query_expression"
_BIND = "bound_apex_expression"
_OBJECT_CREATION = "object_creation_expression"
_SWITCH = "switch_expression"
_SWITCH_LABEL = "switch_label"
_WHEN_TYPE = "when_sobject_type"  # ``when Account a``
_RUN_AS = "run_as_statement"
_CATCH = "catch_clause"
_LOOPS = frozenset({"for_statement", "enhanced_for_statement", "while_statement", "do_statement"})
_JUMPS = frozenset({"break_statement", "continue_statement"})
# What an identifier's spelling is resolved in: a method, constructor,
# property accessor or trigger body (anonymous scripts keep their spelling).
_SPELLING_SCOPES = frozenset(
    {"method_declaration", "constructor_declaration", "accessor_declaration", "trigger_declaration"}
)
_METHODS = frozenset({"method_declaration", "constructor_declaration"})
# Declarations whose ``name`` field binds a local.
_NAMED_BINDERS = frozenset({"formal_parameter", "variable_declarator", "enhanced_for_statement"})
_STOP = frozenset({"class_body"})
# Nodes whose own declarations end with them.
_BLOCK_SCOPES = frozenset({"block", *_LOOPS, _CATCH, "switch_rule"})
_DYNAMIC_QUERIES = frozenset(
    {
        ("database", "query"),
        ("database", "countquery"),
        ("database", "getquerylocator"),
        ("system.database", "query"),
        ("system.database", "countquery"),
        ("system.database", "getquerylocator"),
        ("search", "query"),
        ("system.search", "query"),
    }
)

# Spelling maps by method text. A pure function of the text, so a shared,
# bounded dict is safe across files and threads (a lost race only recomputes).
_SPELLING_CACHE: dict[bytes, dict[str, str]] = {}
_SPELLING_CACHE_MAX = 256
# The last scope asked about and its map: identifiers arrive method by method,
# so this skips re-reading the method's text per identifier. Holding the node
# keeps its tree alive, so ``==`` cannot match a node of a freed tree.
_LAST_SCOPE: list[tuple[Node, dict[str, str]]] = []


def _spelling_scope(node: Node) -> Node | None:
    cur = node.parent
    while cur is not None:
        if cur.type in _SPELLING_SCOPES:
            return cur
        if cur.type in _STOP:
            return None
        cur = cur.parent
    return None


def _binder_name(node: Node) -> Node | None:
    if node.type in _NAMED_BINDERS:
        return node.child_by_field_name("name")
    if node.type == _WHEN_TYPE:
        return next((c for c in node.named_children if c.type == "identifier"), None)
    return None


def _spellings(scope: Node) -> dict[str, str]:
    """Per lowercased local name, the spelling to use for it in *scope*."""
    last = _LAST_SCOPE[-1] if _LAST_SCOPE else None
    if last is not None and last[0] == scope:
        return last[1]
    key = scope.text or b""
    cached = _SPELLING_CACHE.get(key)
    if cached is not None:
        _LAST_SCOPE[:] = [(scope, cached)]
        return cached
    declared: dict[str, set[str]] = {}
    stack = [scope]
    while stack:
        cur = stack.pop()
        name = _binder_name(cur)
        if name is not None and name.type == "identifier":
            text = node_text(name)
            declared.setdefault(text.lower(), set()).add(text)
        stack.extend(cur.named_children)
    out = {low: next(iter(sp)) if len(sp) == 1 else low for low, sp in declared.items()}
    if len(_SPELLING_CACHE) >= _SPELLING_CACHE_MAX:
        _SPELLING_CACHE.clear()
    _SPELLING_CACHE[key] = out
    _LAST_SCOPE[:] = [(scope, out)]
    return out


def _is_local(node: Node) -> bool:
    """Whether identifier *node* names a local or parameter of its method."""
    scope = _spelling_scope(node)
    return scope is not None and node_text(node).lower() in _spellings(scope)


def _canonical(node: Node) -> str:
    """*node*'s identifier text in its declared spelling (see module docs)."""
    text = node_text(node)
    scope = _spelling_scope(node)
    if scope is None:
        return text
    return _spellings(scope).get(text.lower(), text)


class ApexDeclaredTypes(JavaDeclaredTypes):
    """Java's declarations, plus the type a ``when Account a`` arm binds."""

    def of(self, name: Node) -> str | None:
        holder = name.parent
        if holder is not None and holder.type == _WHEN_TYPE:
            typ = next((c for c in holder.named_children if c.type != "identifier"), None)
            return node_text(typ) if typ is not None else None
        return super().of(name)


class ApexDefUseDialect(JavaDefUseDialect):
    language = "apex"
    declared_types = ApexDeclaredTypes(
        frozenset({"formal_parameter", "local_variable_declaration", "enhanced_for_statement"}),
        frozenset({"variable_declarator"}),
    )

    def _occ(self, node: Node) -> Occurrence:
        occ = super()._occ(node)
        name = _canonical(node)
        return occ if name == occ.name else Occurrence(name, occ.line, occ.column)

    # -- exception handlers ---------------------------------------------------

    def handler_head(self, handler: Node) -> HandlerHead:
        """``catch (DmlException e)`` names one type and binds ``e``."""
        param = _catch_param(handler)
        if param is None:
            return (), None
        typ = param.child_by_field_name("type")
        name = param.child_by_field_name("name")
        types = (node_text(typ),) if typ is not None else ()
        return types, _canonical(name) if name is not None else None

    def handler_def_use(self, handler: Node) -> StatementDefUse:
        param = _catch_param(handler)
        name = param.child_by_field_name("name") if param is not None else None
        if param is None or name is None:
            return StatementDefUse(defs=(), uses=())
        defs = [self._occ(name)]
        self._declare(defs, 0, param)
        return StatementDefUse(defs=tuple(defs), uses=())

    # -- receiver -------------------------------------------------------------

    def _own_receiver(self, fn_node: Node, lmap: LanguageNodeMap) -> Receiver | None:
        # A trigger declares no methods, so its plans carry no helper text; a
        # property (``accessor_list``) has no ``body`` and is never sliced.
        if fn_node.type not in _METHODS:
            return None
        if _is_static(fn_node):
            return self._receiver(frozenset(), implicit=True)
        return self._receiver(lmap.self_identifiers, implicit=True)

    # -- reads ----------------------------------------------------------------

    def collect_reads(self, node: Node | None, out: list[Occurrence]) -> None:
        if node is not None and node.type == _QUERY:
            for bind in _binds(node):
                self.collect_reads(bind, out)
            return
        if node is not None and node.type == _OBJECT_CREATION:
            self._creation(node, out, out, reads_only=True)
            return
        if node is not None and _dynamic_query(node):
            out.extend(self._scope_reads(node))
        super().collect_reads(node, out)

    # -- heads and statements -------------------------------------------------

    def _head(
        self, node: Node, lmap: LanguageNodeMap, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        if node.type == _RUN_AS:
            self._process(node.child_by_field_name("user"), defs, uses)
            return
        super()._head(node, lmap, defs, uses)

    def _process(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        if node is None:
            return
        t = node.type
        if t == _QUERY:
            for bind in _binds(node):
                self._process(bind, defs, uses)
            return
        if t == _OBJECT_CREATION:
            self._creation(node, defs, uses, reads_only=False)
            return
        if t == _SWITCH_LABEL:  # ``when 1, RED``: literals and enum constants
            for child in node.named_children:
                if child.type == _WHEN_TYPE:
                    self._process(child, defs, uses)
            return
        if t == _WHEN_TYPE:  # ``when Account a`` declares ``a`` for the arm
            name = _binder_name(node)
            if name is not None:
                start = len(defs)
                defs.append(self._occ(name))
                self._declare(defs, start, node)
            return
        if t == _CATCH:  # a handler inside a switch arm, walked whole
            sdu = self.handler_def_use(node)
            defs.extend(sdu.defs)
            self._process(node.child_by_field_name("body"), defs, uses)
            return
        if t == _RUN_AS:  # inside a switch arm, walked whole
            for child in node.named_children:
                self._process(child, defs, uses)
            return
        if _dynamic_query(node):
            uses.extend(self._scope_reads(node))
        super()._process(node, defs, uses)
        if t == _SWITCH and _leaves_loop(node):
            uses.extend(self._loop_state(node))

    def _creation(
        self, node: Node, defs: list[Occurrence], uses: list[Occurrence], *, reads_only: bool
    ) -> None:
        """``new T(args)``: a ``Field = value`` argument sets a field of an
        SObject. Should the name also be a local of the method, the pair is
        kept as a write that may happen (a may-def and its echo), which can
        only refuse or widen a span, never hide a write."""
        for child in node.named_children:
            if child.type == "argument_list":
                for arg in child.named_children:
                    value = self._pair_value(arg, defs, uses, reads_only=reads_only)
                    self._walk(value, defs, uses, reads_only=reads_only)
            elif child.type != "class_body":  # an anonymous class body is its own scope
                self._walk(child, defs, uses, reads_only=reads_only)

    def _pair_value(
        self, arg: Node, defs: list[Occurrence], uses: list[Occurrence], *, reads_only: bool
    ) -> Node | None:
        """The part of constructor argument *arg* to walk: a pair's value
        (recording the may-def when its name is a local), else *arg*."""
        target = arg.child_by_field_name("left") if arg.type == "assignment_expression" else None
        if target is None or target.type != "identifier":
            return arg
        if not reads_only and _is_local(target):
            occ = [self._occ(target)]
            defs.extend(may_defs(occ))
            uses.extend(echoes(occ))
        return arg.child_by_field_name("right")

    def _walk(
        self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence], *, reads_only: bool
    ) -> None:
        if reads_only:
            self.collect_reads(node, uses)
        else:
            self._process(node, defs, uses)

    def _scope_reads(self, call: Node) -> list[Occurrence]:
        """A read at *call* of every local and parameter in scope there: a
        ``Database.query(q)`` string binds ``:name`` by name in the calling
        scope, so whatever holds the call must hold those names too."""
        line, column = call.start_point[0] + 1, call.start_point[1]
        names: set[str] = set()
        child, cur = call, call.parent
        while cur is not None and cur.type not in _STOP:
            # What *cur* binds before the call; a finished nested block is out of scope.
            stack = [cur]
            while stack:
                node = stack.pop()
                # A declarator the call initializes is not declared yet.
                name = None if node.id == cur.id and _binds_after(cur, child) else _binder_name(node)
                if name is not None and name.type == "identifier":
                    names.add(_canonical(name))
                for c in node.named_children:
                    if node.id == cur.id and c.start_byte >= child.start_byte:
                        break
                    if c.type not in _BLOCK_SCOPES:
                        stack.append(c)
            if cur.type in _SPELLING_SCOPES:
                break
            child, cur = cur, cur.parent
        return [Occurrence(n, line, column) for n in sorted(names)]

    def _loop_state(self, switch: Node) -> list[Occurrence]:
        """A read at *switch* of every name its enclosing loop mentions: a
        ``break`` / ``continue`` in an arm carries the state there to the
        loop's exit or next iteration, which the CFG does not draw."""
        loop = switch.parent
        while loop is not None and loop.type not in _LOOPS:
            if loop.type in _SPELLING_SCOPES:
                return []
            loop = loop.parent
        if loop is None:
            return []
        line, column = switch.start_point[0] + 1, switch.start_point[1]
        seen: dict[str, Occurrence] = {}
        stack = [loop]
        while stack:
            cur = stack.pop()
            if cur.type in self.identifier_kinds:
                name = _canonical(cur)
                seen.setdefault(name, Occurrence(name, line, column))
            stack.extend(cur.named_children)
        return [seen[n] for n in sorted(seen)]


def _dynamic_query(node: Node) -> bool:
    """``Database.query(q)`` and kin: SOQL / SOSL text whose ``:name`` binds
    resolve against the calling scope's locals (``queryWithBinds`` takes them
    in a map, so it is not one)."""
    if node.type != "method_invocation":
        return False
    obj, name = node.child_by_field_name("object"), node.child_by_field_name("name")
    if obj is None or name is None:
        return False
    owner = node_text(obj).lower().replace(" ", "")
    return (owner, node_text(name).lower()) in _DYNAMIC_QUERIES


def _binds_after(holder: Node, child: Node) -> bool:
    """Whether *holder*'s own name is not yet bound in *child*: a declarator's
    initializer, an enhanced ``for``'s iterable."""
    if holder.type == "variable_declarator":
        return True
    value = holder.child_by_field_name("value") if holder.type == "enhanced_for_statement" else None
    return value is not None and value.id == child.id


def _catch_param(handler: Node) -> Node | None:
    return next((c for c in handler.named_children if c.type == "formal_parameter"), None)


def _is_static(holder: Node) -> bool:
    modifiers = next((c for c in holder.named_children if c.type == "modifiers"), None)
    if modifiers is None:
        return False
    return any(
        m.type == "modifier" and any(k.type == "static" for k in m.named_children)
        for m in modifiers.named_children
    )


def _binds(query: Node) -> list[Node]:
    """The ``:expr`` bind expressions of an inline SOQL / SOSL query."""
    out: list[Node] = []
    stack = [query]
    while stack:
        cur = stack.pop()
        if cur.type == _BIND:
            out.extend(cur.named_children)
            continue
        stack.extend(reversed(cur.named_children))
    return out


def _leaves_loop(switch: Node) -> bool:
    """Whether an arm of *switch* holds a ``break`` / ``continue`` that is not
    inside a loop of its own (so it leaves the loop around the switch)."""
    stack = list(switch.named_children)
    while stack:
        cur = stack.pop()
        if cur.type in _JUMPS:
            return True
        if cur.type in _LOOPS or cur.type in _SPELLING_SCOPES or cur.type in _STOP:
            continue
        stack.extend(cur.named_children)
    return False


DIALECT = ApexDefUseDialect()
