"""Go ``DefUseDialect``.

Classifies each identifier in a statement as a write (def) or a read (use). Go's
write sites are: ``:=`` short variable declarations (``a, b := ...``), ``var``
declarations (``var x int = ...``), plain and compound assignments (both are an
``assignment_statement`` -- told apart by the operator token, ``=`` vs ``+=``),
increment / decrement (``x++`` is a read-modify-write), and the loop variables of
a ``range`` clause. Selector targets (``obj.field = ...``) and index targets
(``arr[i] = ...``) bind no *local*, so their base identifiers are reads -- the
precision-first choice that keeps reaching definitions sound for the locals they
actually track. The method ``receiver`` (``func (s *Server) m()``) is seeded as a
parameter so a span that reads it infers it as an IN.

A Go ``block`` nests its statements in a ``statement_list``; the CFG builder
unwraps that, so this dialect only ever sees individual statements.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseDefUseDialect, Occurrence, StatementDefUse

if TYPE_CHECKING:
    from tree_sitter import Node

    from ...complexity.languages import LanguageNodeMap

# Write-site / structural node kinds (kept in lockstep with the Go
# ``LanguageNodeMap``; declared here so the traversal needs no lmap threading).
_ASSIGN_KINDS = frozenset({"assignment_statement"})
_SHORT_VAR = "short_var_declaration"
_VAR_DECL_KINDS = frozenset({"var_declaration", "const_declaration"})
_INC_DEC_KINDS = frozenset({"inc_statement", "dec_statement"})
_EXPRESSION_LIST = "expression_list"
_RANGE_CLAUSE = "range_clause"
_FOR_CLAUSE = "for_clause"
# Selector (``obj.field``) and index (``arr[i]``) targets bind no local.
_SELECTOR = "selector_expression"
_INDEX = "index_expression"
# Nested scopes whose identifiers belong to a different function (closures).
_SCOPE_BOUNDARIES = frozenset({"func_literal"})


class GoDefUseDialect(BaseDefUseDialect):
    language = "go"
    member_access_kinds = frozenset({"selector_expression"})
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

    # -- head (loop clause / if condition) ------------------------------------

    def _head(
        self, node: Node, lmap: LanguageNodeMap, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        if node.type in lmap.loop_kinds:  # for_statement: range / clause / cond
            for child in node.named_children:
                t = child.type
                if t in lmap.block_kinds:
                    continue  # the body lives in successor blocks
                if t == _RANGE_CLAUSE:
                    self._targets(child.child_by_field_name("left"), defs, uses)
                    self._process(child.child_by_field_name("right"), defs, uses)
                elif t == _FOR_CLAUSE:
                    self._process(child.child_by_field_name("initializer"), defs, uses)
                    self._process(child.child_by_field_name("condition"), defs, uses)
                    self._process(child.child_by_field_name("update"), defs, uses)
                else:  # bare condition (while-style ``for cond {}``)
                    self._process(child, defs, uses)
        elif node.type in lmap.branch_kinds:  # if_statement: init + condition
            self._process(node.child_by_field_name("initializer"), defs, uses)
            self._process(node.child_by_field_name("condition"), defs, uses)
        else:
            self._process(node, defs, uses)

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
            self._targets(node.child_by_field_name("left"), defs, uses)
            self._process(node.child_by_field_name("right"), defs, uses)
            return
        if t in _VAR_DECL_KINDS:  # ``var x int = ...`` / ``const c = ...``
            for spec in node.named_children:
                self._var_spec(spec, defs, uses)
            return
        if t in _INC_DEC_KINDS:  # ``x++`` / ``x--`` -- read-modify-write
            for child in node.named_children:
                self._targets(child, defs, uses)
                self.collect_reads(child, uses)
            return
        if t in self.member_access_kinds:
            self.collect_reads(node, uses)  # receiver is a read; field name is not
            return
        if t in self.identifier_kinds:
            uses.append(self._occ(node))
            return
        if self._is_scope_boundary(node):
            return
        for child in node.named_children:
            self._process(child, defs, uses)

    def _var_spec(self, spec: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """A ``var_spec`` / ``const_spec``: its direct ``identifier`` children are
        the bound names (defs); its ``value`` is a read."""
        for child in spec.named_children:
            if child.type in self.identifier_kinds and child.text != b"_":
                defs.append(self._occ(child))
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
            return
        for child in node.named_children:
            self._targets(child, defs, uses)


DIALECT = GoDefUseDialect()
