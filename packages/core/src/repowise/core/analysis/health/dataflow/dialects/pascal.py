"""Object Pascal ``DefUseDialect``.

Pascal is case-insensitive, so ``Total`` and ``total`` are one variable: every
name takes the spelling its routine declares it with (a parameter or a ``var``
section entry), and an undeclared name (a field, a global) keeps its own.
The slicer compares those names with the source text, so a name written in
another case only costs an extraction.

Write sites: an ``assignment`` whose ``lhs`` is a bare identifier, an inline
``var X := ...`` (``varAssignDef``, a declaration), the counter of a ``for``
loop (its ``start`` assignment), the iterator of a ``for ... in`` loop, and the
variable an ``on E: Exception do`` handler binds. A field or element target
(``Obj.F := ...``, ``A[I] := ...``) binds no local, so its base identifiers are
reads. Locals declared in a routine's ``var`` section are not statements and
are bound only when first assigned.

Three writes are only possible, so each is recorded as a def AND a use, which
can refuse a promotion or an extraction but never license one:

* an identifier passed whole to a call (``Inc(I)``, ``ReadLn(S)``): the
  callee may take it as a ``var`` or ``out`` parameter;
* any write inside a ``with`` statement: ``with Obj do X := 1`` may assign
  ``Obj.X`` instead of the local;
* a write inside a ``case`` arm, as for a Java ``switch``.

A call's callee is not a variable: ``Foo(X)`` reads only ``X``, and a bare
``Foo;`` statement reads nothing. ``Obj.Foo`` reads ``Obj``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import (
    NO_RECEIVER,
    BaseDefUseDialect,
    DeclaredTypes,
    HandlerHead,
    Occurrence,
    Receiver,
    StatementDefUse,
    node_text,
)

if TYPE_CHECKING:
    from tree_sitter import Node

    from ...complexity.languages import LanguageNodeMap

_ASSIGN = "assignment"
_INLINE_VAR = "varAssignDef"
_CALL = "exprCall"
_DOT = "exprDot"
_STATEMENT = "statement"
_FOR = "for"
_FOREACH = "foreach"
_WITH = "with"
_CASE = "case"
_HANDLER = "exceptionHandler"
_SCOPE_BOUNDARIES = frozenset({"lambda"})
_PLAIN_ASSIGN = "kAssign"


class PascalDefUseDialect(BaseDefUseDialect):
    language = "pascal"
    member_access_kinds = frozenset({_DOT})
    receiver_write_kinds = frozenset({_ASSIGN})
    declared_types = DeclaredTypes(frozenset({"declArg", "declVar"}), frozenset())
    keyword_kinds = frozenset()

    # -- names ----------------------------------------------------------------

    def __init__(self) -> None:
        # The last routine's spellings: a function's statements are read one
        # after another, so one entry answers nearly every name. Read once into
        # a local below, so a concurrent analysis replacing it is harmless.
        self._last_spellings: tuple[Node, dict[str, str]] | None = None

    def _spelled(self, node: Node, name: str) -> str:
        folded = name.lower()
        if folded == "result":  # declared by the function header, never written out
            return "Result"
        routine = _enclosing_routine(node)
        if routine is None:
            return name
        last = self._last_spellings
        if last is None or last[0] != routine:
            last = (routine, _declared_spellings(routine))
            self._last_spellings = last
        return last[1].get(folded, name)

    def _name(self, node: Node | None) -> str | None:
        name = super()._name(node)
        return self._spelled(node, name) if node is not None and name is not None else name

    def _occ(self, node: Node) -> Occurrence:
        occ = super()._occ(node)
        return Occurrence(name=self._spelled(node, occ.name), line=occ.line, column=occ.column)

    def _is_scope_boundary(self, node: Node) -> bool:
        return node.type in _SCOPE_BOUNDARIES

    # -- signature ------------------------------------------------------------

    def parameter_defs(self, fn_node: Node) -> tuple[Occurrence, ...]:
        """Every name a ``declArg`` binds; ``A, B: Integer`` binds two."""
        header = fn_node.child_by_field_name("header") or fn_node
        args = header.child_by_field_name("args")
        if args is None:
            return ()
        return tuple(
            self._occ(name)
            for arg in args.named_children
            if arg.type == "declArg"
            for name in arg.children_by_field_name("name")
            if name.type in self.identifier_kinds
        )

    def exit_reads(self, fn_node: Node) -> tuple[Occurrence, ...]:
        """A function's ``Result`` and every ``var`` / ``out`` parameter: the
        caller sees what the body last wrote to them."""
        header = fn_node.child_by_field_name("header")
        if header is None:
            return ()
        line = fn_node.end_point[0] + 1
        out = []
        if any(c.type == "kFunction" for c in header.children):
            out.append(Occurrence(name="Result", line=line))
        args = header.child_by_field_name("args")
        for arg in args.named_children if args is not None else ():
            if arg.type == "declArg" and any(c.type in ("kVar", "kOut") for c in arg.children):
                out.extend(
                    Occurrence(name=self._spelled(n, node_text(n)), line=line)
                    for n in arg.children_by_field_name("name")
                    if n.type in self.identifier_kinds
                )
        return tuple(out)

    def _own_receiver(self, fn_node: Node, lmap: LanguageNodeMap) -> Receiver | None:
        """``Self`` for a method (``TFoo.Bar``), where a bare name can be a field."""
        if fn_node.type in lmap.lambda_kinds:
            return NO_RECEIVER
        header = fn_node.child_by_field_name("header")
        name = header.child_by_field_name("name") if header is not None else None
        if name is not None and name.type == "genericDot":
            return self._receiver(frozenset({"self"}), implicit=True)
        return NO_RECEIVER

    # -- exception handlers ---------------------------------------------------

    def handler_head(self, handler: Node) -> HandlerHead:
        exception = handler.child_by_field_name("exception")
        variable = next(
            (c for c in handler.children_by_field_name("variable") if c.type == "identifier"), None
        )
        types = (node_text(exception),) if exception is not None else None
        return types, self._name(variable)

    def handler_def_use(self, handler: Node) -> StatementDefUse:
        defs: list[Occurrence] = []
        for variable in handler.children_by_field_name("variable"):
            if variable.type in self.identifier_kinds:
                defs.append(self._occ(variable))
                self._declare(defs, len(defs) - 1, handler)
        return StatementDefUse(defs=tuple(defs), uses=())

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
        if _inside_with(node):
            uses.extend(defs)
        return StatementDefUse(defs=tuple(defs), uses=tuple(uses))

    def _head(
        self, node: Node, lmap: LanguageNodeMap, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        t = node.type
        if t == _FOR:  # ``for I := A to B do``
            start = len(defs)
            self._process(node.child_by_field_name("start"), defs, uses)
            self._loop_scoped(defs, start)
            self._process(node.child_by_field_name("end"), defs, uses)
        elif t == _FOREACH:  # ``for X in Items do`` / ``for var X in Items do``
            start = len(defs)
            self._targets(node.child_by_field_name("iterator"), defs, uses)
            self._loop_scoped(defs, start)
            self._process(node.child_by_field_name("iterable"), defs, uses)
        elif t in lmap.loop_kinds or t in lmap.branch_kinds:
            self._process(node.child_by_field_name("condition"), defs, uses)
        elif t == _CASE:  # the selector, before ``of``
            selector = next((c for c in node.named_children if c.type != "caseCase"), None)
            self._process(selector, defs, uses)
        elif t == _WITH:  # the objects, never the body
            for child in node.named_children:
                if child.type not in ("statement", "block", _ASSIGN):
                    self.collect_reads(child, uses)
        elif t in lmap.try_kinds:
            return
        else:
            self._process(node, defs, uses)

    # -- the statement / expression walk --------------------------------------

    def _process(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        if node is None:
            return
        t = node.type
        if t == _ASSIGN:
            lhs = node.child_by_field_name("lhs")
            self._targets(lhs, defs, uses)
            op = node.child_by_field_name("operator")
            if op is not None and op.type != _PLAIN_ASSIGN:  # ``X += 1`` reads X too
                self.collect_reads(lhs, uses)
            self._process(node.child_by_field_name("rhs"), defs, uses)
            return
        if t == _CALL:
            entity = node.child_by_field_name("entity")
            if entity is not None and entity.type == _DOT:
                self.collect_reads(entity, uses)
            self._call_arguments(node.child_by_field_name("args"), defs, uses)
            return
        if t == _STATEMENT and node.named_child_count == 1:
            only = node.named_children[0]
            if only.type in self.identifier_kinds:  # ``Foo;``: a parenless call
                return
            if only.type == _DOT:  # ``Obj.Foo;``
                self.collect_reads(only, uses)
                return
        if t == _CASE:
            self._process_may_def(node, defs, uses)
            return
        if t == _HANDLER:
            sdu = self.handler_def_use(node)
            defs.extend(sdu.defs)
            self._process(node.child_by_field_name("body"), defs, uses)
            return
        if t in self.member_access_kinds:
            self.collect_reads(node, uses)
            return
        if t in self.identifier_kinds:
            uses.append(self._occ(node))
            return
        if self._is_scope_boundary(node):
            self.boundary_def(node, defs)
            return
        for child in node.named_children:
            self._process(child, defs, uses)

    def _call_arguments(
        self, args: Node | None, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:
        if args is None:
            return
        for arg in args.named_children:
            if arg.type in self.identifier_kinds:  # may be a var/out parameter
                occ = self._occ(arg)
                defs.append(occ)
                uses.append(occ)
            else:
                self._process(arg, defs, uses)

    def _targets(self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        if node is None:
            return
        if node.type in self.identifier_kinds:
            defs.append(self._occ(node))
            return
        if node.type == _INLINE_VAR:  # ``var X := ...``
            start = len(defs)
            for child in node.named_children:
                if child.type in self.identifier_kinds:
                    defs.append(self._occ(child))
            self._declare(defs, start, node)
            return
        self.collect_reads(node, uses)  # ``Obj.F := ...`` / ``A[I] := ...``


def _enclosing_routine(node: Node) -> Node | None:
    while node is not None and node.type != "defProc":
        node = node.parent
    return node


def _declared_spellings(routine: Node) -> dict[str, str]:
    """``{folded name: spelling}`` for the routine's parameters and ``var`` locals."""
    out: dict[str, str] = {}
    header = routine.child_by_field_name("header")
    holders: list[Node] = []
    args = header.child_by_field_name("args") if header is not None else None
    if args is not None:
        holders.extend(a for a in args.named_children if a.type == "declArg")
    for section in routine.children_by_field_name("local"):
        holders.extend(v for v in section.named_children if v.type == "declVar")
    for holder in holders:
        for name in holder.children_by_field_name("name"):
            if name.type == "identifier" and name.text is not None:
                text = name.text.decode("utf-8", "replace")
                out.setdefault(text.lower(), text)
    return out


def _inside_with(node: Node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type == _WITH:
            return True
        if parent.type in ("defProc", "lambda"):
            return False
        parent = parent.parent
    return False


DIALECT = PascalDefUseDialect()
