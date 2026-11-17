"""PHP ``DefUseDialect``.

Classifies each identifier in a statement as a write (def) or a read (use).
PHP's write sites are:
- Plain assignment: ``$x = ...`` (``assignment_expression``)
- Compound assignments: ``$x += ...``, ``$x ??= ...``, ``$x .= ...``
  (``augmented_assignment_expression`` -- read-modify-write)
- Update expressions: ``$x++`` / ``--$x`` (``update_expression`` -- read-modify-write)
- Foreach binders: ``foreach ($xs as $k => $v)`` (both ``$k`` and ``$v`` are defs;
  ``$xs`` is a use; ``foreach ($xs as &$v)`` is a by-reference alias)
- Destructuring: ``list($a, $b) = ...`` / ``[$a, $b] = ...`` / ``['k' => $v] = ...``
  (all bound targets are defs)
- Static variables: ``static $s = 0;`` (``function_static_declaration``)

Property and array targets (``$this->f = ...``, ``$a[$i] = ...``, ``$a[] = ...``)
bind no local, so their bases stay reads. ``$this`` is the receiver, never a local
parameter or return.

Delegate-style variable calls (``$fn($x)``) treat ``$fn`` as a variable read.
Closures capture by value (``use ($x)``) or reference (``use (&$x)``).
By-reference names (reference assignment, ``&`` parameter, ``use (&$x)``) trigger
span refusal when written, guaranteeing precision against aliasing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseDefUseDialect, Occurrence, StatementDefUse

if TYPE_CHECKING:
    from tree_sitter import Node

    from ...complexity.languages import LanguageNodeMap

# Write-site / structural node kinds
_ASSIGN_KINDS = frozenset({"assignment_expression"})
_REF_ASSIGN_KINDS = frozenset({"reference_assignment_expression"})
_AUG_KINDS = frozenset({"augmented_assignment_expression"})
_UPDATE_KINDS = frozenset({"update_expression"})
_STATIC_DECL_KINDS = frozenset({"function_static_declaration", "static_variable_declaration"})
_FOREACH = "foreach_statement"
_FUNCTION_CALL = "function_call_expression"
_MEMBER_CALL = "member_call_expression"
_SCOPED_CALL = "scoped_call_expression"
_NULLSAFE_MEMBER_CALL = "nullsafe_member_call_expression"

# Member / property access node kinds
_MEMBER_ACCESS = frozenset(
    {"member_access_expression", "nullsafe_member_access_expression", "member_call_expression"}
)
_SUBSCRIPT_ACCESS = frozenset({"subscript_expression"})

# Built-in functions that mutate an argument in-place by reference
_BYREF_INOUT_BUILTINS = frozenset(
    {
        "sort",
        "rsort",
        "asort",
        "arsort",
        "ksort",
        "krsort",
        "usort",
        "uasort",
        "uksort",
        "natsort",
        "natcasesort",
        "shuffle",
        "array_push",
        "array_pop",
        "array_shift",
        "array_unshift",
        "array_splice",
        "reset",
        "end",
        "next",
        "prev",
    }
)
_BYREF_OUT_BUILTINS = frozenset({"preg_match", "preg_match_all"})

# Nested scopes whose identifiers belong to a different function
_SCOPE_BOUNDARIES = frozenset(
    {
        "anonymous_function",
        "arrow_function",
        "class_declaration",
        "interface_declaration",
        "trait_declaration",
        "enum_declaration",
        "anonymous_class",
    }
)


class PhpDefUseDialect(BaseDefUseDialect):
    language = "php"
    member_access_kinds = frozenset(
        {
            "member_access_expression",
            "nullsafe_member_access_expression",
            "member_call_expression",
            "nullsafe_member_call_expression",
        }
    )
    keyword_kinds = frozenset()
    identifier_kinds = frozenset({"variable_name"})

    def _is_scope_boundary(self, node: Node) -> bool:
        return node.type in _SCOPE_BOUNDARIES

    def _occ(self, node: Node) -> Occurrence:
        raw = (node.text or b"").decode("utf-8", "replace")
        name = raw[1:] if raw.startswith("$") else raw
        return Occurrence(
            name=name,
            line=node.start_point[0] + 1,
            column=node.start_point[1],
        )

    def _is_this(self, node: Node | None) -> bool:
        return node is not None and (node.text or b"") == b"$this"

    def collect_reads(self, node: Node | None, out: list[Occurrence]) -> None:
        """Collect variable reads under *node*."""
        if node is None:
            return
        t = node.type
        if t in self.identifier_kinds:
            if not self._is_this(node):
                out.append(self._occ(node))
            return
        if t in self.member_access_kinds:
            # $obj->prop or $obj->method(): receiver is a variable read
            receiver = node.child_by_field_name("object") or (
                node.named_children[0] if node.named_children else None
            )
            if receiver is not None and not self._is_this(receiver):
                self.collect_reads(receiver, out)
            if t in (_MEMBER_CALL, _NULLSAFE_MEMBER_CALL):
                args = node.child_by_field_name("arguments") or (
                    node.named_children[-1] if len(node.named_children) > 1 else None
                )
                if args is not None:
                    self.collect_reads(args, out)
            return
        if t in _SCOPED_CALL:
            # Foo::bar($x): Foo and bar are class/method names, not local variables
            args = node.child_by_field_name("arguments") or (
                node.named_children[-1] if node.named_children else None
            )
            if args is not None:
                self.collect_reads(args, out)
            return
        if t == _FUNCTION_CALL:
            fn = node.child_by_field_name("function") or (
                node.named_children[0] if node.named_children else None
            )
            # Delegate-style call: $fn($x) - function is a variable_name!
            if fn is not None and fn.type in self.identifier_kinds:
                self.collect_reads(fn, out)
            args = node.child_by_field_name("arguments") or (
                node.named_children[-1] if len(node.named_children) > 1 else None
            )
            if args is not None:
                self.collect_reads(args, out)
            return
        if t == "anonymous_function":
            # use ($x, &$y) clause
            self._collect_use_clause_reads(node, out)
            return
        if self._is_scope_boundary(node):
            return
        for child in node.named_children:
            self.collect_reads(child, out)

    def _collect_use_clause_reads(self, fn_node: Node, out: list[Occurrence]) -> None:
        """Collect captured reads from anonymous_function_use_clause."""
        for c in fn_node.children:
            if c.type == "anonymous_function_use_clause":
                for child in c.named_children:
                    if child.type == "by_ref":
                        var = (
                            child.child_by_field_name("variable")
                            or (child.named_children[0] if child.named_children else None)
                        )
                        if var is not None and var.type in self.identifier_kinds and not self._is_this(var):
                            out.append(self._occ(var))
                    elif child.type in self.identifier_kinds and not self._is_this(child):
                        out.append(self._occ(child))
                break

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
            if node.type in lmap.return_kinds:
                for p in self._byref_param_names(node):
                    uses.append(Occurrence(name=p, line=node.start_point[0] + 1))
        return StatementDefUse(defs=tuple(defs), uses=tuple(uses))

    def _byref_param_names(self, node: Node) -> set[str]:
        cur: Node | None = node
        while cur is not None and cur.type not in ("function_definition", "method_declaration"):
            cur = cur.parent
        if cur is None:
            return set()
        params = cur.child_by_field_name("parameters")
        if params is None:
            return set()
        byref: set[str] = set()
        for p in params.named_children:
            if any(c.type == "reference_modifier" for c in p.children):
                name_node = self._binder_identifier(p)
                if name_node is not None:
                    raw = (name_node.text or b"").decode("utf-8", "replace")
                    byref.add(raw[1:] if raw.startswith("$") else raw)
        return byref

    def _closure_bound_names(self, node: Node) -> set[str]:
        """Names a nested scope binds as its own parameters."""
        bound = {occ.name for occ in self.parameter_defs(node)}
        stack = [node.child_by_field_name("parameters"), node.child_by_field_name("parameter")]
        while stack:
            cur = stack.pop()
            if cur is None:
                continue
            if cur.type in self.identifier_kinds and cur.text:
                raw = cur.text.decode("utf-8", "replace")
                bound.add(raw[1:] if raw.startswith("$") else raw)
            stack.extend(cur.named_children)
        return bound

    def parameter_defs(self, fn_node: Node) -> tuple[Occurrence, ...]:
        """Names bound by the signature."""
        params_node = fn_node.child_by_field_name("parameters")
        if params_node is None:
            return ()
        out: list[Occurrence] = []
        for child in params_node.named_children:
            name_node = self._binder_identifier(child)
            if name_node is not None and not self._is_this(name_node):
                out.append(self._occ(name_node))
        return tuple(out)

    def _binder_identifier(self, node: Node) -> Node | None:
        if node.type in self.identifier_kinds:
            return node
        name_node = node.child_by_field_name("name")
        if name_node is not None and name_node.type in self.identifier_kinds:
            return name_node
        for child in node.children:
            if child.type in self.identifier_kinds:
                return child
            if child.is_named:
                sub = self._binder_identifier(child)
                if sub is not None:
                    return sub
        return None

    # -- head (loop clause / if condition) ------------------------------------

    def _head(
        self, node: Node, lmap: LanguageNodeMap, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        t = node.type
        if t == _FOREACH:
            self._foreach_head(node, defs, uses)
        elif t in lmap.loop_kinds:
            # for / while / do
            for c in node.named_children:
                if c.type != "compound_statement":
                    self._process(c, defs, uses)
        elif t in lmap.branch_kinds:
            # if / else_if
            for c in node.named_children:
                if c.type != "compound_statement" and c.type != "else_clause":
                    self.collect_reads(c, uses)
        else:
            self._process(node, defs, uses)

    def _foreach_head(self, node: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """Handle foreach ($xs as $k => $v) / foreach ($xs as &$v)."""
        seen_as = False
        for c in node.children:
            if c.type == "as":
                seen_as = True
                continue
            if c.type == "(" or c.type == ")" or c.type == "compound_statement" or c.type == "colon":
                continue
            if not seen_as:
                # Expression being iterated
                if c.is_named:
                    self.collect_reads(c, uses)
            else:
                # Binder side
                if c.is_named:
                    self._foreach_binder(c, defs, uses)

    def _foreach_binder(self, node: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        if node.type == "pair":
            # $k => $v
            children = [c for c in node.named_children]
            if len(children) >= 2:
                key, val = children[0], children[1]
                self._targets(key, defs, uses)
                self._foreach_binder(val, defs, uses)
            return
        if node.type == "by_ref":
            # &$v: read-modify-write alias of array
            inner = node.child_by_field_name("variable") or (
                node.named_children[0] if node.named_children else None
            )
            if inner is not None:
                self._targets(inner, defs, uses)
                self.collect_reads(inner, uses)
                # By-reference aliasing: refuse span
                defs.append(Occurrence(name="__byref_alias__", line=inner.start_point[0] + 1))
            return
        self._targets(node, defs, uses)

    # -- statement / expression processing ------------------------------------

    def _process(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        if node is None:
            return
        t = node.type
        if t in _ASSIGN_KINDS:
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            self._targets(left, defs, uses)
            self._process(right, defs, uses)
            return
        if t in _REF_ASSIGN_KINDS:
            # $a = &$b: reference assignment
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            self._targets(left, defs, uses)
            if right is not None:
                self._targets(right, defs, uses)
                self.collect_reads(right, uses)
            # Reference assignment aliases both names
            defs.append(Occurrence(name="__byref_alias__", line=node.start_point[0] + 1))
            return
        if t in _AUG_KINDS:
            # $x += 1, $x .= "a", $x ??= 2: read-modify-write
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            self._targets(left, defs, uses)
            self.collect_reads(left, uses)
            self._process(right, defs, uses)
            return
        if t in _UPDATE_KINDS:
            # $x++ / --$x: read-modify-write
            has_update = any(
                c.type in ("++", "--") or c.text in (b"++", b"--") for c in node.children
            )
            if has_update:
                arg = node.child_by_field_name("argument") or (
                    node.named_children[0] if node.named_children else None
                )
                if arg is not None:
                    self._targets(arg, defs, uses)
                    self.collect_reads(arg, uses)
                    return
        if t in _STATIC_DECL_KINDS:
            # static $s = 0;
            for child in node.named_children:
                if child.type == "static_variable_declaration":
                    name = child.child_by_field_name("name") or (
                        child.named_children[0] if child.named_children else None
                    )
                    self._targets(name, defs, uses)
                    val = child.child_by_field_name("value") or (
                        child.named_children[1] if len(child.named_children) > 1 else None
                    )
                    self._process(val, defs, uses)
                else:
                    self._process(child, defs, uses)
            return
        if t == _FOREACH:
            self._foreach_head(node, defs, uses)
            body = node.child_by_field_name("body")
            if body is not None:
                self._process(body, defs, uses)
            return
        if t == _FUNCTION_CALL:
            fn = node.child_by_field_name("function") or (
                node.named_children[0] if node.named_children else None
            )
            # Calling compact() or extract() makes scope unmodelled
            fn_text = (fn.text or b"").decode("utf-8", "replace") if fn else ""
            if fn_text in ("compact", "extract"):
                defs.append(Occurrence(name="__byref_alias__", line=node.start_point[0] + 1))
            # Delegate call
            if fn is not None and fn.type in self.identifier_kinds:
                self.collect_reads(fn, uses)
            args = node.child_by_field_name("arguments") or (
                node.named_children[-1] if len(node.named_children) > 1 else None
            )
            if args is not None:
                if fn_text in _BYREF_INOUT_BUILTINS:
                    self._process_byref_inout_call(args, defs, uses)
                elif fn_text in _BYREF_OUT_BUILTINS:
                    self._process_byref_out_call(args, defs, uses)
                else:
                    self._process(args, defs, uses)
            return
        if t in self.member_access_kinds:
            receiver = node.child_by_field_name("object") or (
                node.named_children[0] if node.named_children else None
            )
            if receiver is not None and not self._is_this(receiver):
                self.collect_reads(receiver, uses)
            if t in (_MEMBER_CALL, _NULLSAFE_MEMBER_CALL):
                args = node.child_by_field_name("arguments") or (
                    node.named_children[-1] if len(node.named_children) > 1 else None
                )
                if args is not None:
                    self._process(args, defs, uses)
            return
        if t in self.identifier_kinds:
            if not self._is_this(node):
                uses.append(self._occ(node))
            return
        if t == "anonymous_function":
            self._collect_use_clause_reads(node, uses)
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
        if t in self.identifier_kinds:
            if not self._is_this(node):
                defs.append(self._occ(node))
            return
        if t in _MEMBER_ACCESS:
            # $this->f = ... or $obj->f = ... binds no local; receiver is a read
            self.collect_reads(node, uses)
            return
        if t in _SUBSCRIPT_ACCESS:
            # PHP arrays are values: $out[] = ... or $out['k'] = ... mutates the local array.
            root = node
            while root is not None and root.type in _SUBSCRIPT_ACCESS:
                root = root.named_children[0] if root.named_children else None
            if root is not None and root.type in self.identifier_kinds and not self._is_this(root):
                defs.append(self._occ(root))
            self.collect_reads(node, uses)
            return
        if t == "list_literal" or t == "array_creation_expression":
            # list($a, $b) = ... or [$a, $b] = ... or ['k' => $v] = ...
            for child in node.named_children:
                if child.type == "pair":
                    # ['k' => $v]
                    val = child.named_children[-1] if child.named_children else None
                    self._targets(val, defs, uses)
                elif child.type == "array_element_initializer":
                    for sub in child.named_children:
                        self._targets(sub, defs, uses)
                else:
                    self._targets(child, defs, uses)
            return
        if t == "by_ref":
            var = node.child_by_field_name("variable") or (
                node.named_children[0] if node.named_children else None
            )
            if var is not None:
                self._targets(var, defs, uses)
                self.collect_reads(var, uses)
                defs.append(Occurrence(name="__byref_alias__", line=node.start_point[0] + 1))
            return
        for child in node.named_children:
            self._targets(child, defs, uses)

    def _arg_nodes(self, args_node: Node) -> list[Node]:
        return [c for c in args_node.named_children if c.type == "argument" or c.is_named]

    def _process_byref_inout_call(
        self, args_node: Node, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        """First argument is modified in-place (def + read). Remaining arguments are reads."""
        arg_list = self._arg_nodes(args_node)
        if not arg_list:
            return
        arg0 = arg_list[0]
        expr0 = arg0.named_children[0] if arg0.type == "argument" and arg0.named_children else arg0
        self._targets(expr0, defs, uses)
        self.collect_reads(expr0, uses)
        for a in arg_list[1:]:
            self._process(a, defs, uses)

    def _process_byref_out_call(
        self, args_node: Node, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        """Third argument (index 2) is written to (def). Other arguments are reads."""
        arg_list = self._arg_nodes(args_node)
        for idx, a in enumerate(arg_list):
            if idx == 2:
                expr = a.named_children[0] if a.type == "argument" and a.named_children else a
                self._targets(expr, defs, uses)
            else:
                self._process(a, defs, uses)


DIALECT = PhpDefUseDialect()
