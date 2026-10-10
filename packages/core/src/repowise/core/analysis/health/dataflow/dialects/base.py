"""The ``DefUseDialect`` plugin contract + the ``DEFUSE_DIALECTS`` registry.

The dataflow def/use pass is language-agnostic: every grammar difference lives
in a ``DefUseDialect``. This mirrors the per-language plugin idiom the rest of
the pipeline already uses (``perf/dialects/``, ``complexity/languages.py``,
``resolvers/``, ...) -- one module per language, registered in a dict, zero
edits to the core (``defuse.py`` / ``reaching.py``) to add one.

A dialect answers exactly one question, in two shapes:

================================  ============================================
Member                            What it answers
================================  ============================================
``statement_def_use(node, lmap,   the variables a single statement *writes*
``head_only)``                    (defs) and *reads* (uses). ``head_only`` is
                                  set for a compound construct's head (an
                                  ``if`` / ``while`` / ``for`` test) so the
                                  dialect inspects only the condition / loop
                                  clause, not the body (which lives in other
                                  CFG blocks).
``parameter_defs(fn_node)``       the parameter names a function signature
                                  binds -- seeded as defs at the CFG entry. The
                                  whole function node is passed so a language can
                                  also seed names bound outside the
                                  ``parameters`` field (a Go method receiver).
================================  ============================================

:class:`BaseDefUseDialect` carries the language-agnostic machinery a concrete
dialect reuses: identifier-name extraction and a member-access / keyword-aware
*read* collector (so ``obj.attr`` reads ``obj`` but not the member name, and
``f(key=x)`` reads ``x`` but not the keyword ``key``). A new language subclasses
it, declares its member-access and keyword node kinds, and implements the
write-site extraction for its assignment shapes -- typically ~100 lines, and the
def/use core plus the reaching-definitions fixpoint never change.

Every facet defaults to "no signal": a language with no registered dialect
produces no def/use and therefore no reaching definitions, the same
precision-first contract the perf pillar depends on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tree_sitter import Node

    from ...complexity.languages import LanguageNodeMap


@dataclass(frozen=True)
class Occurrence:
    """One variable reference at a source line: a def or a use of ``name``."""

    name: str
    line: int  # 1-indexed
    column: int = 0  # 0-indexed, where the reference starts
    #: A declaration only: the column on ``line`` where its declarator ends,
    #: which is where the new name starts to exist. A read on that line at or
    #: past it reads the new variable; one before it (the declaration's own
    #: initializer) reads an outer variable of the same name. None for any
    #: other write, and for a declarator that ends on a later line.
    declared_at: int | None = None
    #: A use recorded only because the paired write may not happen (a write
    #: inside a ``switch`` / ``match`` arm the CFG keeps as one statement), not
    #: a read in the source. It keeps must-def proofs conservative; code that
    #: asks what a span actually reads skips it.
    echo: bool = False
    #: A write that creates a new binding of the name: a block-scoped
    #: declaration (set by :meth:`BaseDefUseDialect._declare`, multi-line
    #: declarators included, where ``declared_at`` stays None) or a loop
    #: header binder (:meth:`BaseDefUseDialect._loop_scoped`). Never a
    #: hoisting TS/JS ``var``.
    declares: bool = False


@dataclass(frozen=True)
class StatementDefUse:
    """The variables one statement writes (``defs``) and reads (``uses``).

    Order is source order, de-duplication is left to the caller; both are
    deterministic for a given statement so downstream fixpoints are stable.
    """

    defs: tuple[Occurrence, ...]
    uses: tuple[Occurrence, ...]


@dataclass
class Captured:
    """What nested closures do to the enclosing function's variables, from
    :meth:`BaseDefUseDialect.collect_captured`.

    ``reads`` is liberal: every read except names the closure binds as
    parameters or writes before reading; it feeds Extract Method's IN/OUT
    liveness, where an extra name only adds a parameter or a return.
    ``shared`` and ``writes`` are strict: reads and assignments of the
    enclosing function's variables, leaving out every occurrence a
    closure-local binding covers (by scope range, not by name); they feed the
    gate that refuses a span a shared local crosses.
    """

    reads: list[Occurrence] = field(default_factory=list)
    shared: list[Occurrence] = field(default_factory=list)
    writes: list[Occurrence] = field(default_factory=list)


@dataclass(frozen=True)
class Receiver:
    """How a function's body reaches the instance it runs on.

    ``names`` are the tokens naming it (``self``, ``this``, a Go receiver's
    own name); empty for a plain function. ``access_kinds`` are the dialect's
    ``receiver.member`` node types and ``write_kinds`` its assignment,
    compound assignment and increment nodes. ``implicit``: a bare name can be a field
    (Java, C++), so a span may use or write the instance without naming it.
    ``copy``: the method holds a copy (a Go value receiver), so a field write
    reaches the caller only through this frame. ``bound``: False when
    ``this`` is not an instance a helper method could share (a TS/JS
    function outside a class).
    """

    names: frozenset[str] = frozenset()
    access_kinds: frozenset[str] = frozenset()
    write_kinds: frozenset[str] = frozenset()
    implicit: bool = False
    copy: bool = False
    bound: bool = True


#: A function with no instance: it is never a method.
NO_RECEIVER = Receiver()

#: ``super`` reaches the instance too (Python ``super()``, TS / Java ``super``).
SUPER = "super"


def mentions_receiver(node: Node, names: frozenset[str]) -> bool:
    """Whether *node*'s text holds one of *names* (or ``super``) as a word.
    A text test, so a mention in a string or comment counts: it can only
    make an answer unknown or cost a walk, never hide a use."""
    words = "|".join(re.escape(n) for n in sorted(names | {SUPER}))
    text = (node.text or b"").decode("utf-8", "replace")
    return re.search(rf"(?<![\w$])(?:{words})(?![\w$])", text) is not None


@runtime_checkable
class DefUseDialect(Protocol):
    """The contract a per-language def/use dialect satisfies."""

    language: str

    def statement_def_use(
        self, node: Node, lmap: LanguageNodeMap, *, head_only: bool
    ) -> StatementDefUse: ...

    def parameter_defs(self, fn_node: Node) -> tuple[Occurrence, ...]: ...


class BaseDefUseDialect:
    """Shared machinery for concrete dialects.

    Subclasses set :attr:`member_access_kinds` / :attr:`keyword_kinds` (usually
    sourced from the language's ``LanguageNodeMap``) and implement
    :meth:`statement_def_use`. The read collector and identifier helpers here
    are grammar-neutral enough to serve every full-tier language without
    override.
    """

    #: Language tag this dialect serves (informational; the registry is the
    #: source of truth for dispatch).
    language: str = ""

    #: Node types representing ``receiver.member`` access. When collecting
    #: reads, only the receiver is a variable; the member name is skipped.
    member_access_kinds: frozenset[str] = frozenset()

    #: Nodes that write their target: assignment, compound assignment and
    #: increment (``x++``), for the receiver fields a span writes.
    receiver_write_kinds: frozenset[str] = frozenset()

    #: Node types for a keyword / named argument (``f(key=value)``). Only the
    #: value is a variable read; the keyword name is skipped.
    keyword_kinds: frozenset[str] = frozenset()

    #: Identifier leaf node types that name a variable.
    identifier_kinds: frozenset[str] = frozenset({"identifier"})

    # -- identifier helpers ---------------------------------------------------

    def _name(self, node: Node | None) -> str | None:
        if node is None or node.type not in self.identifier_kinds or node.text is None:
            return None
        return node.text.decode("utf-8", "replace")

    def _occ(self, node: Node) -> Occurrence:
        return Occurrence(
            name=(node.text or b"").decode("utf-8", "replace"),
            line=node.start_point[0] + 1,
            column=node.start_point[1],
        )

    def _declare(
        self, defs: list[Occurrence], start: int, declarator: Node, *, binds: bool = True
    ) -> None:
        """Mark ``defs[start:]`` as names *declarator* declares.

        ``declared_at`` is set where the declarator ends on the def's own line;
        ``declares`` is set on every one of them when *binds* (False for a
        declaration that creates no block-scoped binding, a TS/JS ``var``).
        Call it right after the declarator's targets are collected and before
        its initializer is walked, so a write nested in the initializer is not
        taken for a declaration.
        """
        end_row, end_col = declarator.end_point
        for i in range(start, len(defs)):
            at = end_col if defs[i].line == end_row + 1 else None
            defs[i] = replace(defs[i], declared_at=at, declares=binds)

    @staticmethod
    def _loop_scoped(defs: list[Occurrence], start: int) -> None:
        """Mark ``defs[start:]`` as a loop header's own binders, scoped to the
        loop. Only ``declares`` is set; ``declared_at`` keeps the line rule."""
        for i in range(start, len(defs)):
            defs[i] = replace(defs[i], declares=True)

    # -- read (use) collection ------------------------------------------------

    def collect_reads(self, node: Node | None, out: list[Occurrence]) -> None:
        """Append every variable *read* under *node* to *out*, in source order.

        Member names (``obj.attr`` -> only ``obj``) and keyword-argument names
        (``f(key=x)`` -> only ``x``) are not variable reads and are skipped, so
        the use set stays precision-first. Nested function / lambda bodies are
        NOT descended into -- their reads belong to a different scope.
        """
        if node is None:
            return
        t = node.type
        if t in self.identifier_kinds:
            out.append(self._occ(node))
            return
        if t in self.member_access_kinds:
            # Only the receiver is a variable read; the member name is not.
            receiver = node.child_by_field_name("object") or node.child_by_field_name("value")
            if receiver is not None:
                self.collect_reads(receiver, out)
            elif node.named_child_count:
                self.collect_reads(node.named_children[0], out)
            return
        if t in self.keyword_kinds:
            self.collect_reads(node.child_by_field_name("value"), out)
            return
        if self._is_scope_boundary(node):
            return
        for child in node.named_children:
            self.collect_reads(child, out)

    #: Whether a plain assignment inside a nested scope writes the enclosing
    #: function's variable (JS/TS, Go, Rust, C++ closures). Python's assignment
    #: makes the name the closure's own local instead. A C++ ``[=]`` or Rust
    #: ``move`` closure writes its own copy; its writes still count, which can
    #: only refuse a span.
    closure_writes_outer: bool = True
    #: Statements that make a name in a nested scope refer to an outer
    #: variable (Python ``nonlocal`` / ``global``).
    outer_decl_kinds: frozenset[str] = frozenset()

    def collect_captured(self, node: Node | None, out: Captured, lmap: LanguageNodeMap) -> None:
        """Collect into *out* what nested scopes under *node* do to the
        enclosing function's variables (see :class:`Captured`).

        :meth:`collect_reads` stops at a nested function or lambda because its
        reads are not the statement's own, but code that moves a closure, or
        the code around it, still has to see what the closure touches.
        """
        if node is None:
            return
        if not self._is_scope_boundary(node):
            for child in node.named_children:
                self.collect_captured(child, out, lmap)
            return
        writes, direct, inner = self._closure_def_use(node, lmap)
        own_reads = [*direct, *inner.reads]
        bound = self._closure_bound_names(node) | _written_before_read(writes, own_reads)
        out.reads.extend(occ for occ in own_reads if occ.name not in bound)
        scopes, head_writes = self._closure_scopes(node, writes, lmap)
        out.shared.extend(o for o in (*direct, *inner.shared) if not _covered(o, scopes))
        out.writes.extend(
            w
            for w in (*writes, *head_writes, *inner.writes)
            if not w.declares and not _covered(w, scopes)
        )

    def _closure_def_use(
        self, node: Node, lmap: LanguageNodeMap
    ) -> tuple[list[Occurrence], list[Occurrence], Captured]:
        """The writes and direct reads inside nested scope *node*, and what
        deeper closures inside it capture.

        The body goes through the dialect's own walk, which tells a declaration
        from a read; a scope with no ``body`` field falls back to plain reads.
        """
        writes: list[Occurrence] = []
        reads: list[Occurrence] = []
        inner = Captured()
        body = node.child_by_field_name("body")
        process = getattr(self, "_process", None)
        if body is not None and process is not None:
            process(body, writes, reads)
            self.collect_captured(body, inner, lmap)
            return writes, reads, inner
        name = node.child_by_field_name("name")
        for child in node.named_children:
            if name is None or child.id != name.id:
                self.collect_reads(child, reads)
                self.collect_captured(child, inner, lmap)
        return writes, reads, inner

    def _closure_scopes(
        self, node: Node, writes: list[Occurrence], lmap: LanguageNodeMap
    ) -> tuple[_Scopes, list[Occurrence]]:
        """Where each closure-local name of nested scope *node* is bound, and
        the loop-header assignments to outer names the body walk missed.

        A parameter and a Python local (a name assigned without ``nonlocal`` /
        ``global``) cover the whole scope; a block-scoped declaration covers
        its statement to the end of the enclosing block; a loop header binder
        covers its loop. Ranges, not names, so an inner ``let x`` does not hide
        a write to the outer ``x`` elsewhere. Ceiling: a TS/JS ``var`` in a
        closure is read as an outer variable, which can only refuse a span.
        """
        whole = (node.start_point, node.end_point)
        scopes: _Scopes = {name: [whole] for name in self._closure_bound_names(node)}
        loops, decls, outer = self._closure_parts(node, lmap)
        head_writes = self._loop_binder_scopes(loops, lmap, scopes)
        for decl in decls:
            _add_declaration_scope(decl, writes, lmap, scopes)
        if not self.closure_writes_outer:
            for w in (*writes, *head_writes):
                if w.name not in outer:
                    scopes.setdefault(w.name, []).append(whole)
            head_writes = []
        for name in outer:
            scopes.pop(name, None)
        return scopes, head_writes

    def _closure_parts(
        self, node: Node, lmap: LanguageNodeMap
    ) -> tuple[list[Node], list[Node], set[str]]:
        """The loops and declaration statements of nested scope *node* (deeper
        scopes not entered), and the names it declares outer."""
        loops: list[Node] = []
        decls: list[Node] = []
        outer: set[str] = set()
        stack = list(node.named_children)
        while stack:
            cur = stack.pop()
            if self._is_scope_boundary(cur):
                continue
            if cur.type in self.outer_decl_kinds:
                outer |= {occ.name for occ in _leaf_names(cur, self.identifier_kinds)}
            elif cur.type in lmap.loop_kinds:
                loops.append(cur)
            elif cur.type in lmap.local_decl_kinds:
                decls.append(cur)
            stack.extend(cur.named_children)
        return loops, decls, outer

    def _loop_binder_scopes(
        self, loops: list[Node], lmap: LanguageNodeMap, scopes: _Scopes
    ) -> list[Occurrence]:
        """Scope each loop's own binders to the loop; return the header's
        assignments to names it does not bind (TS ``for (x of xs)``)."""
        head = getattr(self, "_head", None)
        if head is None:
            return []
        head_writes: list[Occurrence] = []
        for loop in loops:
            defs: list[Occurrence] = []
            head(loop, lmap, defs, [])
            for d in defs:
                if d.declares:
                    scopes.setdefault(d.name, []).append((loop.start_point, loop.end_point))
                else:
                    head_writes.append(d)
        return head_writes

    def _closure_bound_names(self, node: Node) -> set[str]:
        """Names a nested scope binds as its own parameters.

        A closure's parameter list is often shaped unlike a function's
        (``x => ...``, ``|x| ...``), so the identifiers under it are read
        directly on top of what :meth:`parameter_defs` finds.
        """
        bound = {occ.name for occ in self.parameter_defs(node)}
        stack = [node.child_by_field_name("parameters"), node.child_by_field_name("parameter")]
        while stack:
            cur = stack.pop()
            if cur is None:
                continue
            if cur.type in self.identifier_kinds and cur.text:
                bound.add(cur.text.decode("utf-8", "replace"))
            stack.extend(cur.named_children)
        return bound

    def _is_scope_boundary(self, node: Node) -> bool:
        """True if *node* opens a nested scope whose reads are not this
        statement's (a nested function / lambda). Default: never. Subclasses
        override for their function/lambda node kinds."""
        return False

    # Boundary kinds that additionally bind their own name in the ENCLOSING
    # scope. Empty by default, and deliberately not inferred from "the node has
    # a name field": in JS a named function *expression* has one and binds it
    # only inside itself, so inferring would inject a definition of a name the
    # enclosing scope never gets, which is worse than the omission this exists
    # to fix. Each dialect names its own binders.
    enclosing_binder_kinds: frozenset[str] = frozenset()

    def boundary_def(self, node: Node, defs: list[Occurrence]) -> None:
        """Record the name a nested scope binds in the *enclosing* scope.

        A nested function is two things at once: a scope whose reads belong to
        it and not to us, and, in some languages, a plain local binding of its
        own name right here. Skipping the whole node handles the first and used
        to lose the second, so a span calling a sibling closure saw no
        definition of it, the name never entered the extraction's IN set, and
        the lifted helper called something that was not passed to it. Measured
        on this repo's own ranked head: the Python function
        ``ingestion/dynamic_hints/cpp.py::extract`` offered a span calling a
        local ``_emit`` with ``_emit`` absent from its parameters.

        An anonymous scope - a lambda, a closure literal - binds nothing here,
        and neither does a construct whose name is scoped to itself; both are
        left alone via :data:`enclosing_binder_kinds`.
        """
        if node.type not in self.enclosing_binder_kinds:
            return
        name = node.child_by_field_name("name")
        if name is not None and name.type in self.identifier_kinds:
            defs.append(self._occ(name))

    # -- defaults a subclass may override -------------------------------------

    def parameter_defs(self, fn_node: Node) -> tuple[Occurrence, ...]:
        """Parameter names bound by *fn_node*'s signature, as entry defs.
        Default: none (a language with no override seeds no parameters)."""
        return ()

    def receiver(self, fn_node: Node, lmap: LanguageNodeMap) -> Receiver | None:
        """The instance *fn_node* runs on (:class:`Receiver`), ``NO_RECEIVER``
        for a plain function, None when this language cannot tell.

        A function with no receiver of its own nested in one that has one (a
        Python inner ``def``, a Go func literal, a Java or C++ lambda) can
        still reach the outer instance through the captured name; that is
        unknown, not "none", unless the name never appears in it. An outer
        implicit receiver (Java, C++) can be reached by a bare field name, so
        it is always unknown.
        """
        own = self._own_receiver(fn_node, lmap)
        if own is None or own.names:
            return own
        outer_fn = _enclosing_function(fn_node, lmap)
        if outer_fn is None:
            return own
        outer = self.receiver(outer_fn, lmap)
        if outer is None or outer.implicit:
            return None if outer is None or outer.names else own
        return None if outer.names and mentions_receiver(fn_node, outer.names) else own

    def _own_receiver(self, fn_node: Node, lmap: LanguageNodeMap) -> Receiver | None:
        """The receiver *fn_node* declares itself. Default: None (unknown)."""
        return None

    def _receiver(self, names: frozenset[str], **flags: bool) -> Receiver:
        return Receiver(names, self.member_access_kinds, self.receiver_write_kinds, **flags)

    def statement_def_use(
        self, node: Node, lmap: LanguageNodeMap, *, head_only: bool
    ) -> StatementDefUse:  # pragma: no cover - abstract
        raise NotImplementedError

    def _process(
        self, node: Node | None, defs: list[Occurrence], uses: list[Occurrence]
    ) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _process_may_def(self, node: Node, defs: list[Occurrence], uses: list[Occurrence]) -> None:
        """Process *node* whose writes execute only on some path (a switch or
        match arm, an expression-position ``if`` / loop, a ``let-else`` arm).

        Each def found within is recorded as a def AND a use: the may-def keeps
        the variable in every "written in this region" set while its paired use
        stays upward-exposed, so a downstream must-def proof can only get more
        conservative, never less. The paired use is marked :attr:`Occurrence.echo`.
        """
        inner_defs: list[Occurrence] = []
        for child in node.named_children:  # not the node itself: no re-dispatch
            self._process(child, inner_defs, uses)
        defs.extend(inner_defs)
        uses.extend(echoes(inner_defs))


# Per closure-local name, the (start, end) points where that binding is in scope.
_Scopes = dict[str, list[tuple[tuple[int, int], tuple[int, int]]]]


def _enclosing_function(node: Node, lmap: LanguageNodeMap) -> Node | None:
    """The nearest function or lambda holding *node*, stopping at a class
    (a method's class body starts a new instance)."""
    holders = lmap.function_kinds | lmap.lambda_kinds
    cur = node.parent
    while cur is not None and cur.type not in lmap.class_kinds:
        if cur.type in holders:
            return cur
        cur = cur.parent
    return None


def _pos(occ: Occurrence) -> tuple[int, int]:
    return (occ.line - 1, occ.column)


def _covered(occ: Occurrence, scopes: _Scopes) -> bool:
    """True when a closure-local binding is in scope at *occ*."""
    return any(lo <= _pos(occ) <= hi for lo, hi in scopes.get(occ.name, ()))


def _add_declaration_scope(
    decl: Node, writes: list[Occurrence], lmap: LanguageNodeMap, scopes: _Scopes
) -> None:
    """Scope the names *decl* binds from the declaration to its block's end."""
    scope = (decl.start_point, _enclosing_end(decl, lmap))
    for w in writes:
        if w.declares and decl.start_point <= _pos(w) <= decl.end_point:
            scopes.setdefault(w.name, []).append(scope)


def _enclosing_end(node: Node, lmap: LanguageNodeMap) -> tuple[int, int]:
    """End of the block or loop a declaration lives in (its scope's end)."""
    kinds = lmap.block_kinds | lmap.loop_kinds
    cur = node.parent
    while cur is not None and cur.type not in kinds:
        cur = cur.parent
    return (cur or node).end_point


def _leaf_names(node: Node, kinds: frozenset[str]) -> list[Occurrence]:
    """Every identifier leaf under *node*."""
    out: list[Occurrence] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type in kinds and cur.text:
            out.append(Occurrence(cur.text.decode("utf-8", "replace"), cur.start_point[0] + 1))
        stack.extend(cur.named_children)
    return out


def echoes(defs: list[Occurrence]) -> list[Occurrence]:
    """The may-def uses paired with *defs* (see :attr:`Occurrence.echo`)."""
    return [replace(occ, echo=True) for occ in defs]


def _written_before_read(writes: list[Occurrence], reads: list[Occurrence]) -> set[str]:
    """Names whose first write comes on an earlier line than their first read."""
    first_read: dict[str, int] = {}
    for occ in reads:
        first_read[occ.name] = min(occ.line, first_read.get(occ.name, occ.line))
    return {w.name for w in writes if w.line < first_read.get(w.name, w.line + 1)}


# The registry, populated by ``dialects/__init__.py`` from each language module.
# Keyed by ``LanguageTag``; a missing key => the def/use pass is silent for that
# language (no dialect = no signal).
DEFUSE_DIALECTS: dict[str, BaseDefUseDialect] = {}


def get_defuse_dialect(language: str) -> BaseDefUseDialect | None:
    """Return the def/use dialect for *language*, or ``None`` when unmapped."""
    return DEFUSE_DIALECTS.get(language)
