"""Python ``DefUseDialect``.

Classifies each identifier in a statement as a write (def) or a read (use).
Python's write sites are: assignment LHS (``x = ...``), augmented-assignment LHS
(``x += ...`` -- both read and write), tuple/list unpacking (``a, b = ...``),
the walrus operator (``(x := ...)``), ``for`` targets (statement and
comprehension), and ``with ... as`` aliases. Attribute and subscript targets
(``obj.attr = ...`` / ``arr[i] = ...``) bind no *local* variable, so their base
identifiers are reads, not defs -- the precision-first choice that keeps reaching
definitions sound for the locals they actually track.

All grammar specifics live here; the core (``defuse.py`` / ``reaching.py``) and
the reaching-definitions fixpoint are language-agnostic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseDefUseDialect, Occurrence, StatementDefUse

if TYPE_CHECKING:
    from tree_sitter import Node

    from ...complexity.languages import LanguageNodeMap

# Write-site node kinds (kept in lockstep with the Python ``LanguageNodeMap``'s
# ``assignment_kinds`` / ``augmented_assign_kinds``; declared here so the
# traversal needs no lmap threading and stays thread-safe under parallel files).
_ASSIGN_KINDS = frozenset({"assignment"})
_AUG_KINDS = frozenset({"augmented_assignment"})

# Tuple / list unpacking target containers.
_TARGET_CONTAINERS = frozenset(
    {"pattern_list", "tuple_pattern", "list_pattern", "tuple", "list", "expression_list"}
)
# Splat targets: ``*rest`` / ``**kw`` on the LHS.
_SPLAT_TARGETS = frozenset({"list_splat_pattern", "dictionary_splat_pattern", "list_splat"})
# Subscript target (``arr[i] = ...``) -- not a local def; bases are reads.
_SUBSCRIPT_KINDS = frozenset({"subscript"})
# Nested scopes whose identifiers belong to a different function.
_SCOPE_BOUNDARIES = frozenset({"lambda", "function_definition", "async_function_definition"})


class PythonDefUseDialect(BaseDefUseDialect):
    language = "python"
    member_access_kinds = frozenset({"attribute"})
    keyword_kinds = frozenset({"keyword_argument"})

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

    def parameter_defs(self, params_node: Node | None) -> tuple[Occurrence, ...]:
        if params_node is None:
            return ()
        out: list[Occurrence] = []
        for child in params_node.named_children:
            name_node = self._param_name_node(child)
            if name_node is not None:
                out.append(self._occ(name_node))
        return tuple(out)

    # -- head (compound construct condition / loop clause) --------------------

    def _head(
        self, node: Node, lmap: LanguageNodeMap, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        if node.type in lmap.loop_kinds:
            left = node.child_by_field_name("left")
            if left is not None:  # for-style loop: ``for <target> in <iterable>``
                self._targets(left, defs, uses)
                self._process(node.child_by_field_name("right"), defs, uses)
            else:  # while-style loop: condition only
                self._process(node.child_by_field_name("condition"), defs, uses)
        elif node.type in lmap.branch_kinds:
            self._process(node.child_by_field_name("condition"), defs, uses)
        else:
            self._process(node, defs, uses)

    # -- the unified expression / statement walk ------------------------------

    def _process(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """Walk *node*, recording defs at write-sites and uses at reads.

        Handles every Python write-site wherever it nests (so a walrus or a
        comprehension target inside an expression is captured), treats member
        access and keyword names as non-variables, and does not descend into a
        nested function / lambda (a different scope).
        """
        if node is None:
            return
        t = node.type
        if t in _ASSIGN_KINDS:
            self._targets(node.child_by_field_name("left"), defs, uses)
            self.collect_reads(node.child_by_field_name("type"), uses)  # annotation
            self._process(node.child_by_field_name("right"), defs, uses)
            return
        if t in _AUG_KINDS:
            left = node.child_by_field_name("left")
            self._targets(left, defs, uses)  # write
            self.collect_reads(left, uses)  # ...and read (read-modify-write)
            self._process(node.child_by_field_name("right"), defs, uses)
            return
        if t == "named_expression":  # walrus ``x := value``
            self._targets(node.child_by_field_name("name"), defs, uses)
            self._process(node.child_by_field_name("value"), defs, uses)
            return
        if t == "for_in_clause":  # comprehension ``for <target> in <iter>``
            self._targets(node.child_by_field_name("left"), defs, uses)
            self._process(node.child_by_field_name("right"), defs, uses)
            return
        if t == "with_item":
            self._with_item(node, defs, uses)
            return
        if t in self.member_access_kinds:
            self.collect_reads(node, uses)  # receiver is a read; member is not
            return
        if t in self.keyword_kinds:
            self._process(node.child_by_field_name("value"), defs, uses)
            return
        if t in self.identifier_kinds:
            uses.append(self._occ(node))
            return
        if self._is_scope_boundary(node):
            return
        for child in node.named_children:
            self._process(child, defs, uses)

    def _with_item(self, node: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        value = node.child_by_field_name("value")
        if value is not None and value.type == "as_pattern":
            # ``ctx() as name`` -> the context expression is a read, the alias a def.
            named = value.named_children
            if named:
                self._process(named[0], defs, uses)
            self._targets(value.child_by_field_name("alias"), defs, uses)
        else:
            self._process(value, defs, uses)

    # -- write-target extraction ----------------------------------------------

    def _targets(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """Record the plain-local write targets in an assignment LHS.

        A bare identifier (or one nested in tuple/list unpacking or a splat) is a
        def. An attribute or subscript target binds no local, so its base
        identifiers are recorded as reads instead.
        """
        if node is None:
            return
        t = node.type
        if t in self.identifier_kinds:
            defs.append(self._occ(node))
            return
        if t in _TARGET_CONTAINERS or t in _SPLAT_TARGETS:
            for child in node.named_children:
                self._targets(child, defs, uses)
            return
        if t in self.member_access_kinds or t in _SUBSCRIPT_KINDS:
            self.collect_reads(node, uses)
            return
        if t == "as_pattern_target":
            for child in node.named_children:
                self._targets(child, defs, uses)
            return
        # parenthesized / typed target wrappers: descend.
        for child in node.named_children:
            self._targets(child, defs, uses)

    # -- parameter-name extraction --------------------------------------------

    def _param_name_node(self, node: Node) -> Node | None:
        if node.type in self.identifier_kinds:
            return node
        named = node.child_by_field_name("name")
        if named is not None and named.type in self.identifier_kinds:
            return named
        # typed_parameter / splat: first identifier descendant.
        for child in node.named_children:
            if child.type in self.identifier_kinds:
                return child
        return None


DIALECT = PythonDefUseDialect()
