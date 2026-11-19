"""Go ``DefUseDialect``.

Classifies each identifier in a statement as a write (def) or a read (use). Go's
write sites are: ``:=`` short variable declarations (``a, b := ...``), ``var``
declarations (``var x int = ...``), plain and compound assignments (both are an
``assignment_statement`` -- told apart by the operator token, ``=`` vs ``+=``),
increment / decrement (``x++`` is a read-modify-write), and the loop variables of
a ``range`` clause. Selector targets (``obj.field = ...``) and index targets
(``arr[i] = ...``) bind no *local*, so their base identifiers are reads -- the
precision-first choice that keeps reaching definitions sound for the locals they
actually track. The base is a write as well where the variable holds the value
itself, a struct or an array, which Go copies (:func:`written_value`). The method ``receiver`` (``func (s *Server) m()``) is seeded as a
parameter so a span that reads it infers it as an IN.

A Go ``block`` nests its statements in a ``statement_list``; the CFG builder
unwraps that, so this dialect only ever sees individual statements.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .base import (
    NO_RECEIVER,
    BaseDefUseDialect,
    DeclaredTypes,
    Occurrence,
    Receiver,
    StatementDefUse,
    echoes,
    node_text,
)
from .go_values import written_value

if TYPE_CHECKING:
    from tree_sitter import Node

    from ...complexity.languages import LanguageNodeMap

# Write-site / structural node kinds (kept in lockstep with the Go
# ``LanguageNodeMap``; declared here so the traversal needs no lmap threading).
_ASSIGN_KINDS = frozenset({"assignment_statement"})
_SHORT_VAR = "short_var_declaration"
_VAR_DECL_KINDS = frozenset({"var_declaration", "const_declaration"})
# A grouped ``var ( ... )`` holds its specs in a list; a grouped ``const`` does not.
_VAR_SPEC_LIST = "var_spec_list"
_INC_DEC_KINDS = frozenset({"inc_statement", "dec_statement"})
_EXPRESSION_LIST = "expression_list"
_RANGE_CLAUSE = "range_clause"
_FOR_CLAUSE = "for_clause"
# A ``switch`` / ``select`` is one CFG statement; writes inside its arms are
# may-defs.
_CONDITIONAL_KINDS = frozenset(
    {"expression_switch_statement", "type_switch_statement", "select_statement"}
)
# ``case v := <-ch:`` / ``case x = <-ch:`` in a ``select``.
_RECEIVE = "receive_statement"
# ``&x``: the one unary operator that hands out the variable itself.
_UNARY = "unary_expression"
# Selector (``obj.field``) and index (``arr[i]``) targets bind no local.
_SELECTOR = "selector_expression"
_INDEX = "index_expression"
# Nested scopes whose identifiers belong to a different function (closures).
_SCOPE_BOUNDARIES = frozenset({"func_literal"})


class GoDefUseDialect(BaseDefUseDialect):
    language = "go"
    member_access_kinds = frozenset({"selector_expression"})
    receiver_write_kinds = _ASSIGN_KINDS | _INC_DEC_KINDS
    declared_types = DeclaredTypes(frozenset({"parameter_declaration", "var_spec"}))
    keyword_kinds = frozenset()  # Go has no keyword arguments.

    def _is_scope_boundary(self, node: Node) -> bool:
        return node.type in _SCOPE_BOUNDARIES

    # -- public contract ------------------------------------------------------

    def statement_def_use(
        self, node: Node, lmap: LanguageNodeMap, *, head_only: bool
    ) -> StatementDefUse:
        defs: list[Occurrence] = []
        uses: list[Occurrence] = []
        if head_only:
            self._head(node, lmap, defs, uses)
        else:
            self._process(node, defs, uses)
        return StatementDefUse(defs=tuple(defs), uses=tuple(uses))

    def parameter_defs(self, fn_node: Node) -> tuple[Occurrence, ...]:
        """Names bound by the signature -- both the ``parameters`` list and a
        method ``receiver``. A ``parameter_declaration``'s names are its plain
        ``identifier`` children (the type is a ``type_identifier`` / ``*T`` /
        ``[]T`` node, never a bare identifier)."""
        out: list[Occurrence] = []
        for field in ("receiver", "parameters"):
            plist = fn_node.child_by_field_name(field)
            if plist is None:
                continue
            for decl in plist.named_children:
                for child in decl.named_children:
                    if child.type in self.identifier_kinds and child.text != b"_":
                        out.append(self._occ(child))
        return tuple(out)

    def receiver_decl(self, fn_node: Node) -> str | None:
        """The method's receiver list, ``(s *S)``, which a helper method repeats."""
        plist = fn_node.child_by_field_name("receiver")
        return node_text(plist) if plist is not None else None

    def _own_receiver(self, fn_node: Node, lmap: LanguageNodeMap) -> Receiver | None:
        """The method receiver's own name; a value receiver (``s T``, not
        ``s *T``) is a copy. An unnamed receiver is unreachable from the body."""
        plist = fn_node.child_by_field_name("receiver")
        decl = plist.named_children[0] if plist is not None and plist.named_children else None
        name = decl.child_by_field_name("name") if decl is not None else None
        if name is None or not name.text or name.text == b"_":
            return NO_RECEIVER
        kind = decl.child_by_field_name("type")
        copy = kind is not None and kind.type != "pointer_type"
        return self._receiver(frozenset({name.text.decode("utf-8", "replace")}), copy=copy)

    # -- head (loop clause / if condition) ------------------------------------

    def _head(
        self, node: Node, lmap: LanguageNodeMap, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        if node.type in lmap.loop_kinds:  # for_statement: range / clause / cond
            for child in node.named_children:
                if child.type not in lmap.block_kinds:  # the body is in successor blocks
                    self._loop_clause(child, defs, uses)
            return
        if node.type in lmap.branch_kinds:  # if_statement: init + condition
            self._process(node.child_by_field_name("initializer"), defs, uses)
            self._process(node.child_by_field_name("condition"), defs, uses)
            return
        self._process(node, defs, uses)

    def _loop_clause(self, child: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """One header child of a ``for``: a range clause, a three-part clause,
        or a bare condition (while-style ``for cond {}``)."""
        if child.type == _RANGE_CLAUSE:
            start = len(defs)
            self._targets(child.child_by_field_name("left"), defs, uses)
            if any(tok.type == ":=" for tok in child.children):
                self._loop_scoped(defs, start)
            self._process(child.child_by_field_name("right"), defs, uses)
            return
        if child.type == _FOR_CLAUSE:
            for field_name in ("initializer", "condition", "update"):
                self._process(child.child_by_field_name(field_name), defs, uses)
            return
        self._process(child, defs, uses)

    # -- the unified expression / statement walk ------------------------------

    def _process(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        if node is None:
            return
        t = node.type
        if t in _ASSIGN_KINDS:
            left = node.child_by_field_name("left")
            self._targets(left, defs, uses)
            op = node.child_by_field_name("operator")
            if op is not None and op.text not in (b"=", None):  # compound: read too
                self.collect_reads(left, uses)
            self._process(node.child_by_field_name("right"), defs, uses)
            return
        if t == _SHORT_VAR:  # ``a, b := ...``
            start = len(defs)
            self._targets(node.child_by_field_name("left"), defs, uses)
            self._declare(defs, start, node)
            self._process(node.child_by_field_name("right"), defs, uses)
            return
        if t in _VAR_DECL_KINDS:  # ``var x int = ...`` / ``const c = ...``
            for spec in node.named_children:
                grouped = spec.named_children if spec.type == _VAR_SPEC_LIST else (spec,)
                for one in grouped:
                    self._var_spec(one, defs, uses)
            return
        if t in _INC_DEC_KINDS:  # ``x++`` / ``x--`` -- read-modify-write
            for child in node.named_children:
                self._targets(child, defs, uses)
                self.collect_reads(child, uses)
            return
        if t in _CONDITIONAL_KINDS:
            self._process_may_def(node, defs, uses)
            return
        if t == _RECEIVE:
            start = len(defs)
            self._targets(node.child_by_field_name("left"), defs, uses)
            if any(tok.type == ":=" for tok in node.children):
                self._declare(defs, start, node)
            # Bound only if this case is the one taken, and a ``:=`` name lives
            # only in its case: neither hides the value from before the
            # ``select``. ``may_defs`` leaves a declared name certain, so the
            # flag is set here for both.
            defs[start:] = [replace(occ, may=True) for occ in defs[start:]]
            self._process(node.child_by_field_name("right"), defs, uses)
            return
        if t == _UNARY and _is_address_of(node):
            self._address_of(node.child_by_field_name("operand"), defs, uses)
            return
        if t in self.member_access_kinds:
            self.collect_reads(node, uses)  # receiver is a read; field name is not
            return
        if t in self.identifier_kinds:
            uses.append(self._occ(node))
            return
        if self._is_scope_boundary(node):
            self.boundary_def(node, defs)
            return
        for child in node.named_children:
            self._process(child, defs, uses)

    def _address_of(
        self, operand: Node | None, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        """``&x`` reads ``x`` and lets whoever holds the address write it:
        ``fill(&buf)`` can assign ``*dst``, so the variable may change here.
        A may-def (a def with its paired use), since nothing says the write
        happens. ``&x.f`` / ``&x[i]`` reach the variable only where the path
        stays inside the value (:func:`written_value`); through a pointer, a
        slice or a map the storage is shared and the variable is untouched."""
        self._process(operand, defs, uses)
        base = written_value(operand) if operand is not None else None
        if base is not None:
            written = [self._occ(base)]
            defs.extend(written)
            uses.extend(echoes(written))

    def _var_spec(self, spec: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """A ``var_spec`` / ``const_spec``: its direct ``identifier`` children are
        the bound names (defs); its ``value`` is a read."""
        start = len(defs)
        for child in spec.named_children:
            if child.type in self.identifier_kinds and child.text != b"_":
                defs.append(self._occ(child))
        self._declare(defs, start, spec)
        self._process(spec.child_by_field_name("value"), defs, uses)

    # -- write-target extraction ----------------------------------------------

    def _targets(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        if node is None:
            return
        t = node.type
        if t in self.identifier_kinds:
            if node.text != b"_":  # the blank identifier binds nothing
                defs.append(self._occ(node))
            return
        if t == _EXPRESSION_LIST:
            for child in node.named_children:
                self._targets(child, defs, uses)
            return
        if t in (_SELECTOR, _INDEX):  # ``obj.f = ...`` / ``a[i] = ...``: base is a read
            self.collect_reads(node, uses)
            base = written_value(node)
            if base is not None:  # ... and a write too, where the variable holds the value
                defs.append(self._occ(base))
            return
        for child in node.named_children:
            self._targets(child, defs, uses)


def _is_address_of(unary: Node) -> bool:
    op = unary.child_by_field_name("operator")
    return op is not None and op.text == b"&"


DIALECT = GoDefUseDialect()
