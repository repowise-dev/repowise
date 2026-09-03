"""C++ ``DefUseDialect``.

Classifies each identifier in a statement as a write (def) or a read (use).
C++'s write sites are: a ``declaration`` (each nested ``init_declarator`` binds
one name, and a bare ``declarator`` binds an uninitialised one), plain and
compound assignments (both an ``assignment_expression``, told apart by the
``operator`` token ``=`` vs ``+=``, as in Java and Go), update expressions
(``x++`` / ``--x``, read-modify-write), the binder of a range-``for``
(``for (auto& v : xs)``) and the C-style ``for`` initializer. A ``catch``
clause's exception variable is deliberately NOT a def: the CFG never records a
handler binder as a head in any language, so it stays an unmatched *use* — the
conservative direction, matching the Java and Python dialects.

Field targets (``obj.f = …`` / ``this->f = …``) and subscript targets
(``arr[i] = …``) bind no *local*, so their base identifiers are reads — the
precision-first choice that keeps reaching definitions sound for the locals
they actually track. Pointer and reference declarators are transparent: ``int*
p = …`` and ``int& r = …`` both bind the inner ``identifier``.

Two grammar properties make the extraction simple and safe:

* a **type is never a bare ``identifier``** in tree-sitter-cpp (it is
  ``primitive_type`` / ``type_identifier`` / ``qualified_identifier`` /
  ``placeholder_type_specifier``), so "the first ``identifier`` descendant of a
  declarator" is unambiguously the bound name — no field threading needed for
  the reference/pointer wrappers, which label no field on their identifier;
* a ``call_expression``'s callee is its ``function`` field, so the called name
  is skipped while the receiver of a member callee (``db.execute()`` -> ``db``)
  is still read.

A ``switch`` stays a single CFG statement (no per-arm blocks), so a write inside
an arm is only a *may*-def: recorded as both a def and a use, which keeps the
promotion pass's must-def reasoning conservative — an uncertain write can only
ever refuse a promotion, never license one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseDefUseDialect, Occurrence, StatementDefUse

if TYPE_CHECKING:
    from tree_sitter import Node

    from ...complexity.languages import LanguageNodeMap

# Write-site / structural node kinds (kept in lockstep with the C++
# ``LanguageNodeMap``; declared here so the traversal needs no lmap threading).
_ASSIGN_KINDS = frozenset({"assignment_expression"})
_UPDATE_KINDS = frozenset({"update_expression"})  # ``x++`` / ``--x``
_DECL_KINDS = frozenset({"declaration"})
_INIT_DECLARATOR = "init_declarator"
_RANGE_FOR = "for_range_loop"

_CALL = "call_expression"
# Declarator wrappers that are transparent for binding (``int* p`` / ``int& r``
# / ``int a[8]`` / ``T f(x)`` — the most-vexing-parse form of a construction).
_DECLARATOR_WRAPPERS = frozenset(
    {"pointer_declarator", "reference_declarator", "array_declarator", "function_declarator"}
)
# Field (``obj.f``) and subscript (``arr[i]``) targets bind no local.
_FIELD_EXPRESSION = "field_expression"
_SUBSCRIPT_EXPRESSION = "subscript_expression"
# A ``switch`` is one CFG statement; writes inside its arms are may-defs.
_CONDITIONAL_KINDS = frozenset({"switch_statement", "conditional_expression"})
# Nested scopes whose identifiers belong to a different function.
_SCOPE_BOUNDARIES = frozenset({"lambda_expression"})
# Callee node kinds that name a function, not a variable.
_CALLEE_NAME_KINDS = frozenset({"identifier", "qualified_identifier", "template_function"})


class CppDefUseDialect(BaseDefUseDialect):
    language = "cpp"
    member_access_kinds = frozenset({_FIELD_EXPRESSION})
    keyword_kinds = frozenset()  # C++ has no keyword arguments.

    def _is_scope_boundary(self, node: Node) -> bool:
        return node.type in _SCOPE_BOUNDARIES

    def collect_reads(self, node: Node | None, out: list[Occurrence]) -> None:
        """Like the base collector, but a ``call_expression`` contributes only
        its receiver and arguments — never the called function's name."""
        if node is not None and node.type == _CALL:
            fn = node.child_by_field_name("function")
            if fn is not None and fn.type not in _CALLEE_NAME_KINDS:
                # A member / computed callee: the receiver is a real read.
                self.collect_reads(fn, out)
            self.collect_reads(node.child_by_field_name("arguments"), out)
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
        """Names bound by the signature.

        The parameter list hangs off the ``declarator`` chain
        (``function_definition -> function_declarator -> parameter_list``), not
        off the function node, so the chain is walked to find it.
        """
        params = self._parameter_list(fn_node)
        if params is None:
            return ()
        out: list[Occurrence] = []
        for child in params.named_children:
            name_node = self._binder_identifier(child)
            if name_node is not None:
                out.append(self._occ(name_node))
        return tuple(out)

    def _parameter_list(self, fn_node: Node) -> Node | None:
        node: Node | None = fn_node
        for _ in range(6):
            if node is None:
                return None
            params = node.child_by_field_name("parameters")
            if params is not None:
                return params
            node = node.child_by_field_name("declarator")
        return None

    def _binder_identifier(self, node: Node) -> Node | None:
        """The bound name inside a declarator. Safe to search for a bare
        ``identifier``: a C++ *type* is never one (see the module docstring)."""
        if node.type in self.identifier_kinds:
            return node
        declarator = node.child_by_field_name("declarator")
        if declarator is not None:
            found = self._binder_identifier(declarator)
            if found is not None:
                return found
        for child in node.named_children:
            if child.type in self.identifier_kinds:
                return child
            if child.type in _DECLARATOR_WRAPPERS:
                found = self._binder_identifier(child)
                if found is not None:
                    return found
        return None

    # -- head (loop clause / if condition / catch binder) ---------------------

    def _head(
        self, node: Node, lmap: LanguageNodeMap, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        t = node.type
        if t == _RANGE_FOR:  # ``for (auto& v : xs)``
            binder = self._binder_identifier(node.child_by_field_name("declarator"))
            if binder is not None:
                defs.append(self._occ(binder))
            self._process(node.child_by_field_name("right"), defs, uses)
        elif t in lmap.loop_kinds:  # for: init/cond/update; while / do: cond
            self._process(node.child_by_field_name("initializer"), defs, uses)
            self._process(node.child_by_field_name("condition"), defs, uses)
            self._process(node.child_by_field_name("update"), defs, uses)
        elif t in lmap.branch_kinds:
            self._process(node.child_by_field_name("condition"), defs, uses)
        # NB: a C++ ``try`` has no head. The CFG records a try head only for the
        # ``resources`` field (Java's try-with-resources), and a ``catch``
        # clause's binder is never recorded as a head in any language — so an
        # exception variable is an unmatched *use*, the conservative direction,
        # exactly as in the Java and Python dialects.
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
            arg = node.child_by_field_name("argument")
            self._targets(arg, defs, uses)
            self.collect_reads(arg, uses)
            return
        if t in _DECL_KINDS:
            for child in node.named_children:
                if child.type == _INIT_DECLARATOR:
                    binder = self._binder_identifier(child.child_by_field_name("declarator"))
                    if binder is not None:
                        defs.append(self._occ(binder))
                    self._process(child.child_by_field_name("value"), defs, uses)
                elif child.type in self.identifier_kinds or child.type in _DECLARATOR_WRAPPERS:
                    # ``int x;`` / ``int* p;`` — a binding with no initialiser.
                    binder = self._binder_identifier(child)
                    if binder is not None:
                        defs.append(self._occ(binder))
            return
        if t == _CALL:
            self.collect_reads(node, uses)
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
            self.boundary_def(node, defs)
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
        if t in (_FIELD_EXPRESSION, _SUBSCRIPT_EXPRESSION):  # ``o.f =`` / ``a[i] =``
            self.collect_reads(node, uses)
            return
        if t == "pointer_expression":  # ``*p = …`` writes through, not to, ``p``
            self.collect_reads(node, uses)
            return
        for child in node.named_children:
            self._targets(child, defs, uses)


DIALECT = CppDefUseDialect()
