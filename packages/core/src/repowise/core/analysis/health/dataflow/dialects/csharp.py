"""C# ``DefUseDialect``.

Classifies each identifier in a statement as a write (def) or a read (use).
C#'s write sites are: ``local_declaration_statement`` / ``variable_declaration``
(each nested ``variable_declarator`` binds one name), plain and compound
assignments (both an ``assignment_expression`` -- told apart by the operator
token, ``=`` vs ``+=``, as in Java and Go), update expressions (``x++`` /
``--x``, read-modify-write), the binder of a ``foreach`` (``foreach (int v in
xs)`` / ``foreach (var (a, b) in pairs)``), the C-style ``for`` initializer,
``using`` declarations and statements, ``out var`` / ``out T`` / ``out``
arguments (writes), ``ref`` arguments (both read and write), pattern variables
(``is string s``, ``case Foo f``) and tuple deconstruction (``(int p, int q) =
...``). A ``catch (Exception e)`` binder stays an unmatched use, matching the
Java and C++ dialects. Field targets (``obj.f = ...`` / ``this.f = ...``) and
indexer targets (``arr[i] = ...``) bind no *local*, so their base identifiers
are reads.

An ``invocation_expression``'s called method name is not a variable, so the
walk processes only its receiver and arguments. A ``switch`` statement or
switch expression stays a single CFG statement (no per-arm blocks), so writes
inside an arm are recorded as may-defs (both a def and a use), keeping the
must-def proof conservative.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseDefUseDialect, Occurrence, StatementDefUse, _written_before_read

if TYPE_CHECKING:
    from tree_sitter import Node

    from ...complexity.languages import LanguageNodeMap

# Write-site / structural node kinds
_ASSIGN_KINDS = frozenset({"assignment_expression"})
_UPDATE_KINDS = frozenset({"prefix_unary_expression", "postfix_unary_expression"})
_DECL_KINDS = frozenset({"local_declaration_statement", "variable_declaration", "using_statement"})
_DECLARATOR = "variable_declarator"
_FOREACH = "foreach_statement"
_INVOCATION = "invocation_expression"
_ARGUMENT = "argument"
_DECLARATION_EXPR = "declaration_expression"
_DECLARATION_PATTERN = "declaration_pattern"
_IS_PATTERN = "is_pattern_expression"

# Field (``obj.f``, ``this.f``) and indexer (``arr[i]``) targets bind no local.
_MEMBER_ACCESS = "member_access_expression"
_CONDITIONAL_ACCESS = "conditional_access_expression"
_ELEMENT_ACCESS = "element_access_expression"

# A ``switch`` is one CFG statement; writes inside its arms are may-defs.
_CONDITIONAL_KINDS = frozenset({"switch_statement", "switch_expression"})

# Nested scopes whose identifiers belong to a different function.
_SCOPE_BOUNDARIES = frozenset(
    {
        "lambda_expression",
        "anonymous_method_expression",
        "local_function_statement",
        "class_declaration",
        "struct_declaration",
        "record_declaration",
        "interface_declaration",
    }
)

# Callee node kinds that name a function/method, not a variable.
_CALLEE_NAME_KINDS = frozenset({"generic_name", "qualified_name"})


class CSharpDefUseDialect(BaseDefUseDialect):
    language = "csharp"
    member_access_kinds = frozenset({_MEMBER_ACCESS, _CONDITIONAL_ACCESS})
    keyword_kinds = frozenset()
    enclosing_binder_kinds = frozenset({"local_function_statement"})

    def _is_scope_boundary(self, node: Node) -> bool:
        return node.type in _SCOPE_BOUNDARIES

    def collect_reads(self, node: Node | None, out: list[Occurrence]) -> None:
        """Collect variable reads under *node*.

        Like the base collector, but an ``invocation_expression`` contributes only
        its receiver and arguments -- never the called method name.
        """
        if node is None:
            return
        t = node.type
        if t == _INVOCATION:
            fn = node.child_by_field_name("function") or (
                node.named_children[0] if node.named_children else None
            )
            if fn is not None and (
                fn.type in self.member_access_kinds or fn.type not in _CALLEE_NAME_KINDS
            ):
                self.collect_reads(fn, out)
            args = node.child_by_field_name("arguments") or (
                node.named_children[1] if len(node.named_children) > 1 else None
            )
            if args is not None:
                for arg in args.named_children:
                    if arg.type == _ARGUMENT:
                        # 'out' argument writes the target rather than reading it.
                        has_out = any(c.type == "out" for c in arg.children)
                        if not has_out:
                            for c in arg.named_children:
                                self.collect_reads(c, out)
                    else:
                        self.collect_reads(arg, out)
            return
        if t in self.member_access_kinds:
            receiver = node.child_by_field_name("expression") or (
                node.named_children[0] if node.named_children else None
            )
            if receiver is not None:
                self.collect_reads(receiver, out)
            return
        if t in (
            _DECLARATION_PATTERN,
            "var_pattern",
            _DECLARATION_EXPR,
            "subpattern",
            "recursive_pattern",
        ):
            return
        if t in self.identifier_kinds:
            out.append(self._occ(node))
            return
        if self._is_scope_boundary(node):
            return
        for child in node.named_children:
            self.collect_reads(child, out)

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
        """Names bound by the signature."""
        params_node = fn_node.child_by_field_name("parameters")
        if params_node is None:
            return ()
        out: list[Occurrence] = []
        for child in params_node.named_children:
            name_node = child.child_by_field_name("name")
            if name_node is not None and name_node.type in self.identifier_kinds:
                out.append(self._occ(name_node))
            else:
                for sub in child.named_children:
                    if sub.type in self.identifier_kinds:
                        out.append(self._occ(sub))
                        break
        return tuple(out)

    # -- head (loop clause / if condition) ------------------------------------

    def _head(
        self, node: Node, lmap: LanguageNodeMap, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        t = node.type
        if t == _FOREACH:  # ``foreach (int r in records)`` / ``foreach (var (a, b) in pairs)``
            start = len(defs)
            self._targets(node.child_by_field_name("left"), defs, uses)
            self._declare(defs, start, node.child_by_field_name("left") or node)
            self._process(node.child_by_field_name("right"), defs, uses)
        elif t in lmap.loop_kinds:  # for: init/cond/update; while / do: cond
            self._process(node.child_by_field_name("initializer"), defs, uses)
            self._process(node.child_by_field_name("condition"), defs, uses)
            self._process(node.child_by_field_name("update"), defs, uses)
        elif t in lmap.branch_kinds:
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
        if t in _UPDATE_KINDS:  # ``x++`` / ``--x`` -- read-modify-write
            has_update = any(
                c.type in ("++", "--") or c.text in (b"++", b"--") for c in node.children
            )
            if has_update:
                arg = node.child_by_field_name("argument") or (
                    node.named_children[0] if node.named_children else None
                )
                self._targets(arg, defs, uses)
                self.collect_reads(arg, uses)
                return
            for child in node.named_children:
                self._process(child, defs, uses)
            return
        if t in _DECL_KINDS:
            for child in node.named_children:
                if child.type == _DECLARATOR:
                    start = len(defs)
                    name_node = child.child_by_field_name("name")
                    self._targets(name_node, defs, uses)
                    self._declare(defs, start, child)
                    for sub in child.named_children:
                        if sub != name_node:
                            self._process(sub, defs, uses)
                else:
                    self._process(child, defs, uses)
            return
        if t == _INVOCATION:
            fn = node.child_by_field_name("function") or (
                node.named_children[0] if node.named_children else None
            )
            if fn is not None:
                if fn.type in self.member_access_kinds:
                    self.collect_reads(fn, uses)
                elif fn.type not in _CALLEE_NAME_KINDS:
                    self._process(fn, defs, uses)
            args = node.child_by_field_name("arguments") or (
                node.named_children[1] if len(node.named_children) > 1 else None
            )
            if args is not None:
                for arg in args.named_children:
                    if arg.type == _ARGUMENT:
                        has_out = any(c.type == "out" for c in arg.children)
                        has_ref = any(c.type == "ref" for c in arg.children)
                        if has_out:
                            # 'out' argument is a write (def)
                            for c in arg.named_children:
                                self._targets(c, defs, uses)
                        elif has_ref:
                            # 'ref' argument is both a write (def) and a read (use)
                            for c in arg.named_children:
                                self._targets(c, defs, uses)
                                self.collect_reads(c, uses)
                        else:
                            for c in arg.named_children:
                                self._process(c, defs, uses)
                    else:
                        self._process(arg, defs, uses)
            return
        if t == _IS_PATTERN:
            self._process(
                node.child_by_field_name("expression") or node.child_by_field_name("left"),
                defs,
                uses,
            )
            self._targets(node.child_by_field_name("pattern"), defs, uses)
            return
        if t in (_DECLARATION_PATTERN, "var_pattern", "recursive_pattern", _DECLARATION_EXPR):
            self._targets(node, defs, uses)
            return
        if t in _CONDITIONAL_KINDS:
            self._process_may_def(node, defs, uses)
            return
        if t in self.member_access_kinds:
            self.collect_reads(node, uses)
            return
        if t in self.identifier_kinds:
            uses.append(self._occ(node))
            return
        if self._is_scope_boundary(node):
            self.boundary_def(node, defs)
            if node.type in ("lambda_expression", "anonymous_method_expression"):
                writes, reads = self._closure_def_use(node)
                bound = self._closure_bound_names(node) | _written_before_read(writes, reads)
                defs.extend(w for w in writes if w.name not in bound)
            return
        for child in node.named_children:
            self._process(child, defs, uses)

    # -- write-target extraction ----------------------------------------------

    def _targets(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        if node is None:
            return
        t = node.type
        if t in self.identifier_kinds:
            defs.append(self._occ(node))
            return
        if t in (_MEMBER_ACCESS, _CONDITIONAL_ACCESS, _ELEMENT_ACCESS):  # obj.f / arr[i]
            self.collect_reads(node, uses)
            return
        if t == _DECLARATION_PATTERN:
            name = node.child_by_field_name("name")
            if name is not None and name.type in self.identifier_kinds:
                defs.append(self._occ(name))
            return
        if t == _DECLARATION_EXPR:
            name = node.child_by_field_name("name")
            if name is not None and name.type in self.identifier_kinds:
                defs.append(self._occ(name))
            else:
                for c in node.named_children:
                    if c.type in self.identifier_kinds:
                        defs.append(self._occ(c))
                        break
            return
        if t == "recursive_pattern":
            type_node = node.child_by_field_name("type")
            for child in node.named_children:
                if child != type_node:
                    self._targets(child, defs, uses)
            return
        if t == "subpattern":
            pattern = node.child_by_field_name("pattern") or (
                node.named_children[-1] if node.named_children else None
            )
            if pattern is not None and pattern != node:
                self._targets(pattern, defs, uses)
            return
        for child in node.named_children:
            self._targets(child, defs, uses)


DIALECT = CSharpDefUseDialect()
