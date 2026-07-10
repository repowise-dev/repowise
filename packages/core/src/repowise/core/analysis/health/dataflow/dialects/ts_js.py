"""TypeScript / JavaScript ``DefUseDialect`` (one dialect, shared grammar).

Classifies each identifier in a statement as a write (def) or a read (use).
The write sites are: ``let`` / ``const`` / ``var`` declarations (each nesting a
``variable_declarator``), plain assignments (``x = ...``), compound assignments
(``x += ...``), update expressions (``x++`` / ``--x``, read-modify-write),
destructuring binders (``const [a, b] = ...`` / ``const {p, q} = ...``), and the
loop binder of a ``for ... of`` / ``for ... in``. Member and subscript targets
(``obj.attr = ...`` / ``arr[i] = ...``) bind no *local*, so their base
identifiers are reads -- the precision-first choice that keeps reaching
definitions sound for the locals they actually track.

All grammar specifics live here; the CFG / reaching / slice core stays language-
agnostic. JavaScript and TypeScript share the relevant node shapes, so one
dialect serves ``typescript`` / ``tsx`` / ``javascript`` / ``jsx``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseDefUseDialect, Occurrence, StatementDefUse

if TYPE_CHECKING:
    from tree_sitter import Node

    from ...complexity.languages import LanguageNodeMap

# Write-site / structural node kinds (kept in lockstep with the TS
# ``LanguageNodeMap``; declared here so the traversal needs no lmap threading).
_ASSIGN_KINDS = frozenset({"assignment_expression"})
_AUG_KINDS = frozenset({"augmented_assignment_expression"})
_UPDATE_KINDS = frozenset({"update_expression"})  # ``x++`` / ``--x``
_DECL_KINDS = frozenset({"lexical_declaration", "variable_declaration"})
_DECLARATOR = "variable_declarator"
# Destructuring patterns and their parts.
_ARRAY_PATTERN = "array_pattern"
_OBJECT_PATTERN = "object_pattern"
_SHORTHAND_PAT = "shorthand_property_identifier_pattern"
_PAIR_PATTERN = "pair_pattern"
_REST_PATTERN = "rest_pattern"
_ASSIGN_PATTERN = "assignment_pattern"  # default value ``a = 1`` in a pattern
# Member / subscript targets bind no local.
_MEMBER = "member_expression"
_SUBSCRIPT = "subscript_expression"
# Nested scopes whose identifiers belong to a different function.
_SCOPE_BOUNDARIES = frozenset(
    {
        "arrow_function",
        "function_expression",
        "function_declaration",
        "generator_function",
        "generator_function_declaration",
        "method_definition",
    }
)


class TsJsDefUseDialect(BaseDefUseDialect):
    language = "typescript"
    member_access_kinds = frozenset({"member_expression"})
    keyword_kinds = frozenset()  # object-property keys are not variable reads.
    # ``shorthand_property_identifier`` is an object-literal read (``{ days }``
    # reads the local ``days``). Its ``_pattern`` twin is the destructuring
    # *binder*; the grammars (ts / tsx / js) never emit the non-pattern kind in
    # a write-target position, so counting it as an identifier only adds reads.
    identifier_kinds = frozenset({"identifier", "shorthand_property_identifier"})

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
        params_node = fn_node.child_by_field_name("parameters")
        if params_node is None:
            return ()
        out: list[Occurrence] = []
        sink: list[Occurrence] = []
        for child in params_node.named_children:
            pattern = child.child_by_field_name("pattern")
            if pattern is None:
                pattern = child if child.type in self.identifier_kinds else None
            self._targets(pattern, out, sink)
        return tuple(out)

    # -- head (loop clause / if condition) ------------------------------------

    def _head(
        self, node: Node, lmap: LanguageNodeMap, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        if node.type in lmap.loop_kinds:
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left is not None or right is not None:  # for-of / for-in binder
                self._targets(left, defs, uses)
                self._process(right, defs, uses)
                return
            # C-style for / while / do: initializer + condition + increment.
            self._process(node.child_by_field_name("initializer"), defs, uses)
            self._process(node.child_by_field_name("condition"), defs, uses)
            self._process(node.child_by_field_name("increment"), defs, uses)
        elif node.type in lmap.branch_kinds:
            self._process(node.child_by_field_name("condition"), defs, uses)
        else:
            self._process(node, defs, uses)

    # -- the unified expression / statement walk ------------------------------

    def _process(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        if node is None:
            return
        t = node.type
        if t in _ASSIGN_KINDS:
            self._targets(node.child_by_field_name("left"), defs, uses)
            self._process(node.child_by_field_name("right"), defs, uses)
            return
        if t in _AUG_KINDS:
            left = node.child_by_field_name("left")
            self._targets(left, defs, uses)  # write
            self.collect_reads(left, uses)  # ...and read (read-modify-write)
            self._process(node.child_by_field_name("right"), defs, uses)
            return
        if t in _UPDATE_KINDS:  # ``x++`` -- read-modify-write of the argument
            arg = node.child_by_field_name("argument")
            self._targets(arg, defs, uses)
            self.collect_reads(arg, uses)
            return
        if t in _DECL_KINDS:
            for declarator in node.named_children:
                if declarator.type != _DECLARATOR:
                    continue
                self._targets(declarator.child_by_field_name("name"), defs, uses)
                self._process(declarator.child_by_field_name("value"), defs, uses)
            return
        if t in self.member_access_kinds:
            self.collect_reads(node, uses)  # receiver is a read; property is not
            return
        if t in self.identifier_kinds:
            uses.append(self._occ(node))
            return
        if self._is_scope_boundary(node):
            return
        for child in node.named_children:
            self._process(child, defs, uses)

    # -- write-target extraction ----------------------------------------------

    def _targets(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        if node is None:
            return
        t = node.type
        if t in self.identifier_kinds or t == _SHORTHAND_PAT:
            defs.append(self._occ(node))
            return
        if t == _ARRAY_PATTERN:
            for child in node.named_children:
                self._targets(child, defs, uses)
            return
        if t == _OBJECT_PATTERN:
            for child in node.named_children:
                if child.type == _PAIR_PATTERN:
                    self._targets(child.child_by_field_name("value"), defs, uses)
                else:  # shorthand / rest / nested pattern
                    self._targets(child, defs, uses)
            return
        if t == _REST_PATTERN:
            for child in node.named_children:
                self._targets(child, defs, uses)
            return
        if t == _ASSIGN_PATTERN:  # ``a = default`` in a pattern / param
            self._targets(node.child_by_field_name("left"), defs, uses)
            self._process(node.child_by_field_name("right"), defs, uses)  # default is a read
            return
        if t in _DECL_KINDS:  # a declaration used as a loop binder (``for (let x ...``)
            self._process(node, defs, uses)
            return
        if t in (_MEMBER, _SUBSCRIPT):  # ``obj.f = ...`` / ``a[i] = ...``: base is a read
            self.collect_reads(node, uses)
            return
        for child in node.named_children:
            self._targets(child, defs, uses)


DIALECT = TsJsDefUseDialect()
