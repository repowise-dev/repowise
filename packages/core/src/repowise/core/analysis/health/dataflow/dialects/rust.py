"""Rust ``DefUseDialect``.

Classifies each identifier in a statement as a write (def) or a read (use).
Rust's write sites are: ``let`` declarations (whose pattern may destructure --
tuple, slice, struct, ``&``-reference, ``ref`` / ``mut`` binders), plain
assignments (``assignment_expression``), compound assignments
(``compound_assignment_expr``, read-modify-write), the binder of a ``for``
pattern, and the pattern of a ``let_condition`` (``if let`` / ``while let``).
Place-expression targets -- field (``self.f = ...``), index (``arr[i] = ...``),
and deref (``*ptr = ...``) -- bind no *local*, so their base identifiers are
reads. A ``self_parameter`` is seeded like a Go receiver so a span reading
``self`` infers it as an IN.

Two conservatisms keep the promotion pass's must-def reasoning sound on
expression-oriented grammar the CFG does not open up:

* a ``match`` (and any control-flow expression met in *expression* position)
  stays a single CFG statement, so a write inside it executes only on some
  path -- it is recorded as both a def and a use (a may-def), which can only
  ever refuse a promotion, never license one;
* a ``let_condition`` binder only binds when the pattern matches, so it is a
  may-def too (unlike a ``for`` binder, which binds on every body entry).

Macro bodies (``format!(...)``) are walked as token trees, so identifiers
inside them still register as reads; a macro that *binds* a local is invisible,
which errs toward extra refusals, never a wrong proof. Statement-position
control flow arrives here already unwrapped from its ``expression_statement``
carrier by the CFG builder (``statement_wrapper_kinds``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseDefUseDialect, Occurrence, StatementDefUse

if TYPE_CHECKING:
    from tree_sitter import Node

    from ...complexity.languages import LanguageNodeMap

# Write-site / structural node kinds (kept in lockstep with the Rust
# ``LanguageNodeMap``; declared here so the traversal needs no lmap threading).
_ASSIGN_KINDS = frozenset({"assignment_expression"})
_AUG_KINDS = frozenset({"compound_assignment_expr"})
_LET_DECL = "let_declaration"
_LET_CONDITION = "let_condition"  # ``if let PAT = expr`` / ``while let ...``
# Pattern containers whose identifier leaves are binders (defs).
_PATTERN_CONTAINERS = frozenset(
    {
        "tuple_pattern",
        "slice_pattern",
        "or_pattern",
        "reference_pattern",
        "mut_pattern",
        "captured_pattern",
        "ref_pattern",
        "match_pattern",
        "parenthesized_pattern",
    }
)
# Patterns that carry a *type* side to skip (``Some(v)`` / ``Point { x }`` --
# the type is a plain ``identifier`` node in tuple-struct position, so a naive
# descent would mint a phantom def for ``Some``).
_TYPED_PATTERNS = frozenset({"tuple_struct_pattern", "struct_pattern"})
_FIELD_PATTERN = "field_pattern"
# Shorthand struct-pattern binder leaf (``Point { x, y }``).
_SHORTHAND_BINDER = "shorthand_field_identifier"
# Place-expression targets that bind no local: field / index / deref.
_PLACE_TARGETS = frozenset({"field_expression", "index_expression", "unary_expression"})
# Control-flow expressions met in expression position (or as the mega-statement
# a ``match`` stays): their writes are may-defs.
_CONDITIONAL_KINDS = frozenset(
    {
        "match_expression",
        "if_expression",
        "if_let_expression",
        "while_expression",
        "while_let_expression",
        "for_expression",
        "loop_expression",
    }
)
# A ``scoped_identifier`` (``Vec::new`` / ``Color::Red``) is a path, never a
# local variable; its component identifiers are skipped wholesale.
_SCOPED_IDENTIFIER = "scoped_identifier"
# Nested scopes whose identifiers belong to a different function; an
# ``async_block`` captures like a closure and runs later.
_SCOPE_BOUNDARIES = frozenset({"closure_expression", "function_item", "async_block"})


class RustDefUseDialect(BaseDefUseDialect):
    language = "rust"
    member_access_kinds = frozenset({"field_expression"})
    keyword_kinds = frozenset()  # struct-literal field names are not reads.
    # ``self`` is its own node type in tree-sitter-rust, not an ``identifier``;
    # without it here every ``self.field`` read would drop its receiver (Go and
    # Python receivers are plain identifiers, so only Rust needs the addition).
    identifier_kinds = frozenset({"identifier", "self"})

    def _is_scope_boundary(self, node: Node) -> bool:
        return node.type in _SCOPE_BOUNDARIES

    def collect_reads(self, node: Node | None, out: list[Occurrence]) -> None:
        """Like the base collector, but a path (``Vec::new``) reads nothing."""
        if node is not None and node.type == _SCOPED_IDENTIFIER:
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
        """Names bound by the signature: each ``parameter``'s pattern (which
        may destructure) plus a ``self_parameter``, seeded like a Go receiver."""
        params_node = fn_node.child_by_field_name("parameters")
        if params_node is None:
            return ()
        out: list[Occurrence] = []
        sink: list[Occurrence] = []
        for child in params_node.named_children:
            if child.type == "self_parameter":
                for sub in child.children:
                    if sub.type == "self":
                        out.append(self._occ(sub))
                        break
                continue
            self._targets(child.child_by_field_name("pattern"), out, sink)
        return tuple(out)

    # -- head (loop clause / if condition) ------------------------------------

    def _head(
        self, node: Node, lmap: LanguageNodeMap, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        t = node.type
        if t in lmap.loop_kinds:
            pattern = node.child_by_field_name("pattern")
            if pattern is not None:  # ``for PAT in expr`` -- binds every entry
                self._targets(pattern, defs, uses)
                self._process(node.child_by_field_name("value"), defs, uses)
            else:  # ``while cond`` / ``while let`` / bare ``loop`` (no head)
                self._condition(node.child_by_field_name("condition"), defs, uses)
        elif t in lmap.branch_kinds:
            self._condition(node.child_by_field_name("condition"), defs, uses)
        else:
            self._process(node, defs, uses)

    def _condition(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """An ``if`` / ``while`` condition; a ``let_condition`` binds its
        pattern only when it matches, so those binders are may-defs."""
        if node is None:
            return
        if node.type == _LET_CONDITION:
            binders: list[Occurrence] = []
            self._targets(node.child_by_field_name("pattern"), binders, uses)
            defs.extend(binders)
            uses.extend(binders)
            self._process(node.child_by_field_name("value"), defs, uses)
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
        if t == _LET_DECL:
            self._targets(node.child_by_field_name("pattern"), defs, uses)
            self._process(node.child_by_field_name("value"), defs, uses)
            # ``let ... else { diverge }``: the arm runs only on mismatch, so
            # anything it writes is a may-def.
            alternative = node.child_by_field_name("alternative")
            if alternative is not None:
                self._process_may_def(alternative, defs, uses)
            return
        if t in _CONDITIONAL_KINDS:
            self._process_may_def(node, defs, uses)
            return
        if t == _LET_CONDITION:
            self._condition(node, defs, uses)
            return
        if t == _SCOPED_IDENTIFIER:  # a path, never a local read
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
        """Process *node* whose writes execute only on some path (a match arm,
        an expression-position ``if`` / loop, a ``let-else`` arm).

        Each def found within is recorded as a def AND a use, so a downstream
        must-def proof can only get more conservative, never less.
        """
        inner_defs: list[Occurrence] = []
        for child in node.named_children:  # not the node itself: no re-dispatch
            self._process(child, inner_defs, uses)
        defs.extend(inner_defs)
        uses.extend(inner_defs)

    # -- write-target extraction (assignment LHS + binding patterns) ----------

    def _targets(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        if node is None:
            return
        t = node.type
        if t in self.identifier_kinds or t == _SHORTHAND_BINDER:
            defs.append(self._occ(node))
            return
        if t in _PATTERN_CONTAINERS:
            for child in node.named_children:
                self._targets(child, defs, uses)
            return
        if t in _TYPED_PATTERNS:  # skip the type side (``Some`` / ``Point``)
            type_node = node.child_by_field_name("type")
            for child in node.named_children:
                if type_node is not None and child.id == type_node.id:
                    continue
                self._targets(child, defs, uses)
            return
        if t == _FIELD_PATTERN:  # ``x: pat`` descends; shorthand ``x`` binds
            pattern = node.child_by_field_name("pattern")
            if pattern is not None:
                self._targets(pattern, defs, uses)
            else:
                name = node.child_by_field_name("name")
                if name is not None:
                    self._targets(name, defs, uses)
            return
        if t in _PLACE_TARGETS:  # ``obj.f`` / ``a[i]`` / ``*p``: bases are reads
            self.collect_reads(node, uses)
            return
        for child in node.named_children:
            self._targets(child, defs, uses)


DIALECT = RustDefUseDialect()
