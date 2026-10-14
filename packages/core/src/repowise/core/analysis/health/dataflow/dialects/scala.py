"""Scala ``DefUseDialect``.

Classifies each identifier in a statement as a write (def) or a read (use).
Scala is expression-oriented, so the write sites are spread over several node
shapes:

* ``val_definition`` / ``var_definition`` (``lazy val`` too): the ``pattern``
  field binds one name (``identifier``) or several (``tuple_pattern``,
  ``case_class_pattern``, ``x @ p``).
* ``assignment_expression``: ``x = ...``. ``this.n = ...`` / ``obj.f = ...`` /
  ``arr(i) = ...`` bind no *local*, so their bases are reads. The same node is
  a named argument inside an ``arguments`` list (``p.copy(a = 2)``): there only
  the value is read and ``a`` is neither a def nor a use.
* Compound assignment (``x += 1``) has NO node of its own in tree-sitter-scala:
  it is an ``infix_expression`` whose ``operator`` is an ``operator_identifier``
  spelling ``+=``. The write is therefore found by the operator text, never by
  node type (``a + b``, ``a <= b`` and ``a == b`` are the same node type and
  write nothing). An operator is an assignment operator when it ends in ``=``,
  does not start with ``=`` and is not ``<=`` / ``>=`` / ``!=`` (SLS 6.12.4).
* ``for`` enumerators: ``i <- xs`` and ``j = i * 2`` bind their first child in
  the loop body only; a ``guard`` is a filter (reads).
* ``case_clause`` patterns bind in the arm (``typed_pattern``,
  ``tuple_pattern``, ``infix_pattern``, ...). A ``catch { case e: E => }``
  binder is bound like any other arm: ``handler_head`` reports it.

Ceilings, each of which can only make a plan *less* likely, never wrong:

* ``buf += x`` is the same node as ``count += x``. On a ``var`` of an immutable
  type it rebinds the local; on a ``ListBuffer`` it mutates the receiver and
  rebinds nothing. The two cannot be told apart without types, so the left
  name is recorded as BOTH a may-def and a use (a write that may not happen):
  it can add to what reaches a later read and hides nothing.
* An ``if`` / ``match`` / ``try`` / ``for`` / ``while`` met inside an
  expression (``val z = if (c) a else b``, a call argument) stays inside its
  CFG statement, so writes in its arms are may-defs (def and echo use), as for
  a Java ``switch``.
* ``implicit`` / ``using`` parameters and ``implicit`` locals are uses a call
  site never writes. The dialect cannot see which call needs which implicit,
  so every call in a function that has one counts as a use of each: a span
  with a call carries them as parameters. Implicits that come from the
  enclosing class or an import stay in scope for a sibling helper.
* Not modelled (needs a hook in shared code, see the PR description): a
  ``breakable { ... break() }`` call, a ``return`` inside a lambda, and a
  ``Future`` / ``Await`` call are ordinary calls to the CFG.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .base import (
    NO_RECEIVER,
    BaseDefUseDialect,
    Captured,
    Occurrence,
    Receiver,
    StatementDefUse,
    node_text,
)

if TYPE_CHECKING:
    from tree_sitter import Node

    from ...complexity.languages import LanguageNodeMap

_VAL_KINDS = frozenset({"val_definition", "var_definition"})
_ASSIGN = "assignment_expression"
_INFIX = "infix_expression"
_CALL = "call_expression"
_ARGUMENTS = "arguments"
_ENUMERATORS = "enumerators"
_ENUMERATOR = "enumerator"
_GUARD = "guard"
_CASE_CLAUSE = "case_clause"
_CASE_BLOCK = "case_block"
_CATCH = "catch_clause"
_OPEN_BODY_KINDS = frozenset({"lambda_expression", "catch_clause", "case_block"})
_FIELD = "field_expression"

# ``x match { ... }``, ``if``, ``try``, ``for`` and ``while`` reached through an
# expression stay inside one CFG statement: their arm writes are may-defs.
_CONDITIONAL_KINDS = frozenset(
    {
        "match_expression",
        "if_expression",
        "try_expression",
        "for_expression",
        "while_expression",
        "do_while_expression",
    }
)

# Nested scopes whose identifiers belong to a different function. A
# ``template_body`` covers the members of ``new T { ... }`` inside an expression.
_SCOPE_BOUNDARIES = frozenset(
    {
        "lambda_expression",
        "function_definition",
        "class_definition",
        "object_definition",
        "trait_definition",
        "enum_definition",
        "given_definition",
        "template_body",
    }
)

# Pattern nodes whose identifier children are binders (lower-case) or stable
# identifiers / constants (upper-case, ``Nil``, ``Foo``) that are reads.
# Nodes whose named children are real statements.
_STATEMENT_CONTAINERS = frozenset(
    {"template_body", "indented_block", "case_block", "compilation_unit"}
)
# Fields through which a construct holds its arm / body.
_ARM_FIELDS = frozenset({"consequence", "alternative", "body"})

# ``this`` / ``super`` are the receiver, not variables (the Receiver contract
# carries them).
_NOT_VARIABLES = frozenset({"this", "super"})

_PATTERN_SKIP = frozenset({"type_identifier", "stable_type_identifier", "generic_type"})

# SLS 6.12.4: an assignment operator ends in ``=``, does not start with ``=``,
# and is not one of the comparison operators.
_NOT_ASSIGN_OPS = frozenset({"<=", ">=", "!=", "=="})
_ASSIGN_OP = re.compile(r"^[^=\s].*=$")


def compound_operator(node: Node) -> str | None:
    """The ``op=`` text of an ``infix_expression`` that is a compound
    assignment, else None. ``x += 1`` -> ``+=``; ``a + b`` / ``a <= b`` ->
    None."""
    if node.type != _INFIX:
        return None
    op = node.child_by_field_name("operator")
    text = node_text(op) if op is not None else ""
    if len(text) < 2 or text in _NOT_ASSIGN_OPS or not _ASSIGN_OP.match(text):
        return None
    return text


def _is_arm(node: Node) -> bool:
    """Whether *node* is the brace-less arm / body of a construct: held through
    ``consequence`` / ``alternative`` / ``body`` by its parent."""
    parent = node.parent
    if parent is None:
        return False
    for i, child in enumerate(parent.children):
        if child.id == node.id:
            return parent.field_name_for_child(i) in _ARM_FIELDS
    return False


def _binds(name: str) -> bool:
    """A pattern identifier binds a variable when it starts lower-case; an
    upper-case one (``Nil``, ``Foo``) is a stable identifier, a read."""
    return bool(name) and not name[0].isupper()


def _is_modifier(node: Node, word: bytes) -> bool:
    mods = next((c for c in node.children if c.type in ("modifiers", "access_modifier")), None)
    return mods is not None and word in (mods.text or b"").split()


class ScalaDefUseDialect(BaseDefUseDialect):
    language = "scala"
    member_access_kinds = frozenset({_FIELD})
    receiver_write_kinds = frozenset({_ASSIGN})
    keyword_kinds = frozenset()  # named arguments are an ``assignment_expression``
    enclosing_binder_kinds = frozenset({"function_definition"})

    def _is_scope_boundary(self, node: Node) -> bool:
        t = node.type
        if t in _SCOPE_BOUNDARIES:
            return True
        # ``xs.map { case n => ... }``: a partial-function literal is a closure
        # for capture purposes. A ``match`` / ``catch`` body is also a
        # ``case_block`` but runs in place, so only a call argument counts.
        if t == _CATCH:
            # ``catch_kinds`` is empty for Scala (mapping it would double-count
            # every single-case catch in the complexity numbers), so the CFG
            # builder does not see handler arms. They are read as closures
            # instead: what they read counts as a use at its line, and what
            # they write is a closure-shared write the slicer refuses to cut.
            return True
        return t == _CASE_BLOCK and node.parent is not None and node.parent.type == _CALL

    def _closure_def_use(
        self, node: Node, lmap: LanguageNodeMap
    ) -> tuple[list[Occurrence], list[Occurrence], Captured]:
        """The writes and direct reads inside a nested scope.

        A Scala ``lambda_expression`` and a partial-function ``case_block`` have
        no ``body`` field (the body is whatever follows the parameters), and a
        ``catch_clause`` is just its ``case_block``. The base fallback reads
        them as plain reads, which would hide ``xs.foreach { v => total += v }``'s
        write to ``total``: walk the body with the dialect's own statement walk.
        """
        if node.type not in _OPEN_BODY_KINDS:
            return super()._closure_def_use(node, lmap)
        params = node.child_by_field_name("parameters")
        writes: list[Occurrence] = []
        reads: list[Occurrence] = []
        inner = Captured()
        for child in node.named_children:
            if params is not None and child.id == params.id:
                continue
            self._process(child, writes, reads)
            self.collect_captured(child, inner, lmap)
        return writes, reads, inner

    # -- public contract ------------------------------------------------------

    def statement_def_use(
        self, node: Node, lmap: LanguageNodeMap, *, head_only: bool
    ) -> StatementDefUse:
        defs: list[Occurrence] = []
        uses: list[Occurrence] = []
        if head_only:
            self._head(node, lmap, defs, uses)
        else:
            owner = self._bare_arm_owner(node, lmap)
            if owner is not None:
                if owner.id == node.id:  # a fragment past the first: already read
                    return StatementDefUse(defs=(), uses=())
                node = owner
            self._process(node, defs, uses)
        return StatementDefUse(defs=tuple(defs), uses=tuple(uses))

    def _bare_arm_owner(self, node: Node, lmap: LanguageNodeMap) -> Node | None:
        """For a CFG "statement" that is really a fragment of a brace-less arm,
        the arm it belongs to; the arm itself for its first fragment and *node*
        for every later one. None for a real statement.

        ``if (c) errors += 1 else buf += r`` and ``def f = x + 1`` have no
        ``block``: the arm is one expression, and the CFG builder, which treats
        an arm's named children as its statements, hands over ``errors``,
        ``+=`` and ``1`` one by one. Taken apart, the write is lost. The whole
        arm is read at its first fragment instead (a nested ``if`` / ``match``
        as a may-def, so nothing becomes a definite write it is not).
        """
        parent = node.parent
        if parent is None or node.type in lmap.return_kinds | lmap.raise_kinds:
            return None
        if parent.type in lmap.block_kinds or parent.type in _STATEMENT_CONTAINERS:
            return None
        if not _is_arm(parent):
            return None
        first = next((c for c in parent.named_children if "comment" not in c.type), None)
        return parent if first is not None and first.id == node.id else node

    def parameter_defs(self, fn_node: Node) -> tuple[Occurrence, ...]:
        """Names bound by every parameter clause (``def f(a: Int)(b: Int)``),
        ``implicit`` / ``using`` clauses included, and by a lambda's bare
        parameter (``v => ...``) or parenthesised ones (``(a, b) => ...``)."""
        out: list[Occurrence] = []
        for clause in self._parameter_clauses(fn_node):
            if clause.type in self.identifier_kinds:  # ``v => ...``
                out.append(self._occ(clause))
                continue
            for child in clause.named_children:
                name = child.child_by_field_name("name")
                if name is not None and name.type in self.identifier_kinds:
                    out.append(self._occ(name))
                elif child.type in self.identifier_kinds:
                    out.append(self._occ(child))
        return tuple(out)

    @staticmethod
    def _parameter_clauses(fn_node: Node) -> list[Node]:
        """Every ``parameters`` clause of *fn_node* (a curried ``def`` has
        several, and ``child_by_field_name`` would see only the first)."""
        clauses = []
        for i, child in enumerate(fn_node.children):
            if fn_node.field_name_for_child(i) in ("parameters", "parameter") and child.is_named:
                clauses.append(child)
        return clauses

    def implicit_names(self, fn_node: Node) -> frozenset[str]:
        """Names of the ``implicit`` / ``using`` parameters of *fn_node* and of
        the ``implicit`` / ``given`` locals in its body. A call anywhere in the
        function may consume one without naming it."""
        names: set[str] = set()
        text = fn_node.text or b""
        if b"implicit" not in text and b"using" not in text and b"given" not in text:
            return frozenset()
        for clause in self._parameter_clauses(fn_node):
            text = (clause.text or b"")[:12]
            if text.lstrip(b"( \t\n").startswith((b"implicit", b"using")):
                names |= {o.name for o in self.parameter_defs_of(clause)}
        stack = list(fn_node.named_children)
        while stack:
            cur = stack.pop()
            if cur.type in ("given_definition",):
                name = cur.child_by_field_name("name")
                if name is not None:
                    names.add(node_text(name))
            elif cur.type in _VAL_KINDS and _is_modifier(cur, b"implicit"):
                names |= {o.name for o in self._pattern_names(cur.child_by_field_name("pattern"))}
            if cur.type not in ("function_definition", "lambda_expression") or cur is fn_node:
                stack.extend(cur.named_children)
        return frozenset(names)

    def parameter_defs_of(self, clause: Node) -> list[Occurrence]:
        out: list[Occurrence] = []
        for child in clause.named_children:
            name = child.child_by_field_name("name")
            if name is not None and name.type in self.identifier_kinds:
                out.append(self._occ(name))
        return out

    def _own_receiver(self, fn_node: Node, lmap: LanguageNodeMap) -> Receiver | None:
        """``this`` for a method of a class, trait, object or enum, where a bare
        name can also be a member; a top-level or local ``def`` has none. A
        lambda reaches the enclosing method's (:meth:`receiver`)."""
        if fn_node.type in lmap.lambda_kinds:
            return NO_RECEIVER
        cur = fn_node.parent
        while cur is not None:
            if cur.type in lmap.class_kinds:
                return self._receiver(lmap.self_identifiers, implicit=True)
            if cur.type in lmap.function_kinds:
                return None  # a local ``def``: the outer method's receiver, unknown
            cur = cur.parent
        return NO_RECEIVER

    # -- reads ----------------------------------------------------------------

    def collect_reads(self, node: Node | None, out: list[Occurrence]) -> None:
        """Like the base collector, but a named argument (``f(key = x)``)
        contributes only its value, and a call to a plain method name does not
        read that name as a variable unless it is a bare identifier (a local
        function value is called the same way, so it is a read)."""
        if node is None:
            return
        if node.type == _ARGUMENTS:
            for arg in node.named_children:
                value = arg.child_by_field_name("right") if arg.type == _ASSIGN else arg
                if arg.type == _ASSIGN:
                    self.collect_reads(value, out)
                else:
                    self.collect_reads(arg, out)
            return
        if node.type in self.identifier_kinds and node_text(node) in _NOT_VARIABLES:
            return
        super().collect_reads(node, out)

    # -- head (loop clause / if condition) ------------------------------------

    def _head(
        self, node: Node, lmap: LanguageNodeMap, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        t = node.type
        if t == "for_expression":  # ``for (i <- xs if p; j = i * 2) ...``
            # The ``(`` and ``)`` tokens carry the ``enumerators`` field too, so
            # ``child_by_field_name`` would return the paren: look by type.
            self._process(
                next((c for c in node.named_children if c.type == _ENUMERATORS), None), defs, uses
            )
        elif t in lmap.loop_kinds or t in lmap.branch_kinds or t in lmap.if_kinds:
            # while / do-while / if: the condition
            self._process(node.child_by_field_name("condition"), defs, uses)
        else:
            self._process(node, defs, uses)

    # -- the unified expression / statement walk ------------------------------

    def _process(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        if node is None:
            return
        t = node.type
        if t in _VAL_KINDS:
            self._definition(node, defs, uses)
            return
        if t == _ASSIGN:
            self._assignment(node, defs, uses)
            return
        if t == _INFIX:
            self._infix(node, defs, uses)
            return
        if t == _CALL:
            self._call(node, defs, uses)
            return
        if t == _ARGUMENTS:
            self._arguments(node, defs, uses)
            return
        if t == _ENUMERATORS:
            for child in node.named_children:
                self._process(child, defs, uses)
            return
        if t == _ENUMERATOR:
            self._enumerator(node, defs, uses)
            return
        if t == _CASE_CLAUSE:
            self._case_clause(node, defs, uses)
            return
        if t in _CONDITIONAL_KINDS:
            self._process_may_def(node, defs, uses)
            return
        if t in self.member_access_kinds:
            self.collect_reads(node, uses)  # receiver is a read; member name is not
            return
        if t in self.identifier_kinds:
            if node_text(node) not in _NOT_VARIABLES:
                uses.append(self._occ(node))
            return
        if self._is_scope_boundary(node):
            self.boundary_def(node, defs)
            return
        for child in node.named_children:
            self._process(child, defs, uses)

    def _definition(self, node: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """``val`` / ``var`` / ``lazy val``: bind the pattern, then walk the
        initializer (so a write nested in it is not taken for a declaration)."""
        start = len(defs)
        defs.extend(self._pattern_names(node.child_by_field_name("pattern")))
        self._declare(defs, start, node.child_by_field_name("pattern") or node)
        self._process(node.child_by_field_name("value"), defs, uses)

    def _assignment(self, node: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        left = node.child_by_field_name("left")
        self._targets(left, defs, uses)
        self._process(node.child_by_field_name("right"), defs, uses)

    def _infix(self, node: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """``a + b`` reads both sides; ``x += y`` also writes ``x`` (see the
        module doc for why the write is a may-def and a use)."""
        left = node.child_by_field_name("left")
        if compound_operator(node) is None:
            self._process(left, defs, uses)
            self._process(node.child_by_field_name("right"), defs, uses)
            return
        written: list[Occurrence] = []
        self._targets(left, written, uses)
        defs.extend(may_def for may_def in _as_may(written))
        self.collect_reads(left, uses)  # the old value (and ``buf`` itself) is read
        self._process(node.child_by_field_name("right"), defs, uses)

    def _call(self, node: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """``f(a)`` / ``recv.m(a)``: the callee name is not a variable unless it
        is a bare identifier (a local function value); a receiver is a read."""
        for name in self._implicits_at(node):
            uses.append(Occurrence(name, node.start_point[0] + 1, node.start_point[1]))
        fn = node.child_by_field_name("function")
        if fn is not None:
            if fn.type in self.member_access_kinds:
                self.collect_reads(fn, uses)
            elif fn.type == _CALL:  # ``f(a)(b)`` curried
                self._call(fn, defs, uses)
            elif fn.type in self.identifier_kinds and not _binds(node_text(fn)):
                pass  # ``Some(x)`` / ``P(1)``: a constructor or companion ``apply``
            else:
                self._process(fn, defs, uses)
        self._process(node.child_by_field_name("arguments"), defs, uses)

    def _implicits_at(self, call: Node) -> frozenset[str]:
        """The implicit names in scope of the ``def`` that holds *call*."""
        cur = call.parent
        while cur is not None and cur.type != "function_definition":
            cur = cur.parent
        return self.implicit_names(cur) if cur is not None else frozenset()

    def _arguments(self, node: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """Arguments: ``name = value`` is a named argument (only ``value`` is a
        read, ``name`` is not a variable)."""
        for arg in node.named_children:
            if arg.type == _ASSIGN:
                self._process(arg.child_by_field_name("right"), defs, uses)
            else:
                self._process(arg, defs, uses)

    def _enumerator(self, node: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """``i <- xs`` / ``j = i * 2``: the first child binds in the loop body
        only (``declares``), the rest are reads; a ``guard`` is a filter."""
        kids = node.named_children
        if not kids:
            return
        start = len(defs)
        defs.extend(self._pattern_names(kids[0]))
        self._loop_scoped(defs, start)
        for child in kids[1:]:
            self._process(child, defs, uses)

    def _case_clause(self, node: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """A ``case`` pattern binds in its arm; the guard and body read."""
        start = len(defs)
        defs.extend(self._pattern_names(node.child_by_field_name("pattern")))
        self._loop_scoped(defs, start)  # an arm-local binding: ``declares``
        for child in node.named_children:
            if child.type != "pattern" and child != node.child_by_field_name("pattern"):
                self._process(child, defs, uses)

    # -- patterns / write targets ---------------------------------------------

    def _pattern_names(self, node: Node | None) -> list[Occurrence]:
        """The variables a pattern binds: lower-case identifiers outside a type
        position. ``Nil`` / ``Foo`` / ``Some(x)``'s ``Some`` are not binders."""
        out: list[Occurrence] = []
        stack = [node] if node is not None else []
        while stack:
            cur = stack.pop()
            if cur.type in _PATTERN_SKIP:
                continue
            if cur.type in self.identifier_kinds:
                if _binds(node_text(cur)):
                    out.append(self._occ(cur))
                continue
            stack.extend(reversed(cur.named_children))
        return sorted(out, key=lambda o: (o.line, o.column))

    def _targets(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """Write targets of ``=`` / ``op=``: a bare name is a def; ``a.f`` and
        ``a(i)`` reach through ``a`` and bind no local, so ``a`` is a read."""
        if node is None:
            return
        t = node.type
        if t in self.identifier_kinds:
            defs.append(self._occ(node))
            return
        if t in (_FIELD, _CALL):  # ``obj.f = ...`` / ``arr(i) = ...``
            self.collect_reads(node, uses)
            return
        if t in ("tuple_expression", "tuple_pattern"):  # ``(a, b) = pair``
            for child in node.named_children:
                self._targets(child, defs, uses)
            return
        self.collect_reads(node, uses)


def _as_may(defs: list[Occurrence]) -> list[Occurrence]:
    from .base import may_defs

    return may_defs(defs)


DIALECT = ScalaDefUseDialect()
