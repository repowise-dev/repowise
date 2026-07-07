"""Java ``DefUseDialect``.

Classifies each identifier in a statement as a write (def) or a read (use).
Java's write sites are: ``local_variable_declaration`` (each nested
``variable_declarator`` binds one name), plain and compound assignments (both
are an ``assignment_expression`` -- told apart by the operator token, ``=`` vs
``+=``, as in Go), update expressions (``x++`` / ``--x``, read-modify-write),
the binder of an enhanced ``for`` (``for (T v : xs)``), the C-style ``for``
initializer, and the resource clause of try-with-resources. Field targets
(``obj.f = ...`` / ``this.f = ...``) and array targets (``arr[i] = ...``) bind
no *local*, so their base identifiers are reads -- the precision-first choice
that keeps reaching definitions sound for the locals they actually track.

A ``method_invocation``'s ``name`` is the called method, not a variable, so
the walk processes only its ``object`` and ``arguments``; the same for a
``method_reference``'s method side. A ``switch`` stays a single CFG statement
(no per-arm blocks), so a write inside an arm is only a *may*-def: it is
recorded as both a def and a use, which keeps the promotion pass's must-def
reasoning conservative (an uncertain write can only ever refuse a promotion,
never license one).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseDefUseDialect, Occurrence, StatementDefUse

if TYPE_CHECKING:
    from tree_sitter import Node

    from ...complexity.languages import LanguageNodeMap

# Write-site / structural node kinds (kept in lockstep with the Java
# ``LanguageNodeMap``; declared here so the traversal needs no lmap threading).
_ASSIGN_KINDS = frozenset({"assignment_expression"})
_UPDATE_KINDS = frozenset({"update_expression"})  # ``x++`` / ``--x``
_DECL_KINDS = frozenset({"local_variable_declaration"})
_DECLARATOR = "variable_declarator"
_RESOURCE = "resource"  # ``try (var res = open())``
_ENHANCED_FOR = "enhanced_for_statement"
_METHOD_INVOCATION = "method_invocation"
_METHOD_REFERENCE = "method_reference"  # ``Integer::parseInt``
# Field (``obj.f``) and array (``arr[i]``) targets bind no local.
_FIELD_ACCESS = "field_access"
_ARRAY_ACCESS = "array_access"
# A ``switch`` is one CFG statement; writes inside its arms are may-defs.
_CONDITIONAL_KINDS = frozenset({"switch_expression", "switch_statement"})
# Nested scopes whose identifiers belong to a different function. A
# ``class_body`` covers anonymous-class methods inside an expression.
_SCOPE_BOUNDARIES = frozenset({"lambda_expression", "class_body"})


class JavaDefUseDialect(BaseDefUseDialect):
    language = "java"
    member_access_kinds = frozenset({"field_access"})
    keyword_kinds = frozenset()  # Java has no keyword arguments.

    def _is_scope_boundary(self, node: Node) -> bool:
        return node.type in _SCOPE_BOUNDARIES

    def collect_reads(self, node: Node | None, out: list[Occurrence]) -> None:
        """Like the base collector, but a ``method_invocation`` / ``method_reference``
        contributes only its receiver and arguments -- never the method name."""
        if node is not None and node.type == _METHOD_INVOCATION:
            self.collect_reads(node.child_by_field_name("object"), out)
            self.collect_reads(node.child_by_field_name("arguments"), out)
            return
        if node is not None and node.type == _METHOD_REFERENCE:
            first = node.named_children[0] if node.named_children else None
            if first is not None and first.type in self.identifier_kinds:
                out.append(self._occ(first))
            return
        super().collect_reads(node, out)

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
        """Names bound by the signature. A ``formal_parameter`` names its
        binder in the ``name`` field; a varargs ``spread_parameter`` nests it
        in a ``variable_declarator`` -- both are found by the same probe (the
        type side is ``type_identifier`` nodes, never a bare ``identifier``)."""
        params_node = fn_node.child_by_field_name("parameters")
        if params_node is None:
            return ()
        out: list[Occurrence] = []
        for child in params_node.named_children:
            name_node = self._binder_identifier(child)
            if name_node is not None:
                out.append(self._occ(name_node))
        return tuple(out)

    def _binder_identifier(self, node: Node) -> Node | None:
        named = node.child_by_field_name("name")
        if named is not None and named.type in self.identifier_kinds:
            return named
        for child in node.named_children:
            found = self._binder_identifier(child)
            if found is not None:
                return found
        return None

    # -- head (loop clause / if condition / try resources) --------------------

    def _head(
        self, node: Node, lmap: LanguageNodeMap, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        t = node.type
        if t == _ENHANCED_FOR:  # ``for (T v : xs)``
            self._targets(node.child_by_field_name("name"), defs, uses)
            self._process(node.child_by_field_name("value"), defs, uses)
        elif t in lmap.loop_kinds:  # for: init/cond/update; while / do: cond
            self._process(node.child_by_field_name("init"), defs, uses)
            self._process(node.child_by_field_name("condition"), defs, uses)
            self._process(node.child_by_field_name("update"), defs, uses)
        elif t in lmap.branch_kinds:
            self._process(node.child_by_field_name("condition"), defs, uses)
        elif t in lmap.try_kinds:  # try-with-resources: the resource bindings
            resources = node.child_by_field_name("resources")
            if resources is not None:
                for res in resources.named_children:
                    if res.type == _RESOURCE:
                        self._targets(res.child_by_field_name("name"), defs, uses)
                        self._process(res.child_by_field_name("value"), defs, uses)
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
        if t in _UPDATE_KINDS:  # ``x++`` / ``--x`` -- read-modify-write
            for child in node.named_children:
                self._targets(child, defs, uses)
                self.collect_reads(child, uses)
            return
        if t in _DECL_KINDS:
            for declarator in node.named_children:
                if declarator.type != _DECLARATOR:
                    continue
                self._targets(declarator.child_by_field_name("name"), defs, uses)
                self._process(declarator.child_by_field_name("value"), defs, uses)
            return
        if t == _METHOD_INVOCATION:  # the ``name`` is a method, not a variable
            self._process(node.child_by_field_name("object"), defs, uses)
            self._process(node.child_by_field_name("arguments"), defs, uses)
            return
        if t == _METHOD_REFERENCE:  # ``recv::method`` -- only the receiver reads
            first = node.named_children[0] if node.named_children else None
            if first is not None and first.type in self.identifier_kinds:
                uses.append(self._occ(first))
            return
        if t in _CONDITIONAL_KINDS:
            self._process_may_def(node, defs, uses)
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

    def _process_may_def(self, node: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """Process *node* whose writes execute only on some path (a switch arm).

        Each def found within is recorded as a def AND a use: the may-def keeps
        the variable in every "written in this region" set while its paired use
        stays upward-exposed, so a downstream must-def proof can only get more
        conservative, never less.
        """
        inner_defs: list[Occurrence] = []
        for child in node.named_children:  # not the node itself: no re-dispatch
            self._process(child, inner_defs, uses)
        defs.extend(inner_defs)
        uses.extend(inner_defs)

    # -- write-target extraction ----------------------------------------------

    def _targets(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        if node is None:
            return
        t = node.type
        if t in self.identifier_kinds:
            defs.append(self._occ(node))
            return
        if t in (_FIELD_ACCESS, _ARRAY_ACCESS):  # ``obj.f = ...`` / ``a[i] = ...``
            self.collect_reads(node, uses)
            return
        for child in node.named_children:
            self._targets(child, defs, uses)


DIALECT = JavaDefUseDialect()
