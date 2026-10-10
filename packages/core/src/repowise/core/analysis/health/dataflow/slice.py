"""Extract Method slicing: find safe, single-exit spans with IN/OUT inference.

The first user-facing consumer of the dataflow layer. Given a function's
analysis (CFG + def/use from D2), it finds contiguous statement spans that can
be lifted into a helper method without changing behaviour, and infers the
helper's signature:

- **IN (parameters)** -- variables the span *reads* whose value is produced
  before the span (defined-before, used-inside).
- **OUT (return)** -- variables the span *defines* that are *used after* it,
  with no intervening redefinition (so the helper returns the live value).

**Extractability predicate (precision-first).** A span is a candidate only when
it cuts at statement boundaries within a single block (so never a partial
branch or a mid-``try`` split), contains no control-flow jump that leaves the
region (``return`` / ``raise`` / ``break`` / ``continue`` -> single clean exit),
removes real complexity (at least one decision point), is substantial enough to
matter without being nearly the whole body, and has at most one return and a
small parameter list. Everything else is suppressed -- ten great extractions,
not two hundred maybes.

Line-based liveness over D2's def/use occurrences realises the IN/OUT inference;
the CFG/jump scan realises the single-exit predicate. The jump and nested-scope
node kinds come from the language's ``LanguageNodeMap`` (the same source the CFG
builder uses), so every full-tier language whose map populates them is served by
this one slicer.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from ..complexity.ast_utils import member_object, self_member_name
from ..complexity.cyclomatic import BodyTally
from ..complexity.nloc import _code_line_numbers
from .dialects.base import SUPER, mentions_receiver

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap
    from .analyze import FunctionAnalysis
    from .defuse import FunctionDefUse
    from .dialects.base import Receiver

# Gates (precision-first; tuned to suppress trivial or unwieldy extractions).
_MIN_STMTS = 2  # at least two statements
# The extracted helper is substantial: code lines, the walker's NLOC rule, so a
# span padded with comments does not clear it.
_MIN_SLICE_NLOC = 5
_MIN_CCN_REMOVED = 1  # extraction must remove a real decision point
_MAX_PARAMS = 5  # too many ins => the span is not cohesive
_MAX_RETURNS = 1  # a single clean return (v1); multi-output is future work
# Backstop against a pathological function producing too many sub-ranges.
_MAX_CANDIDATES = 4000
# A span holding this share of the body's code lines or more is the whole
# function under another name, not a split: what stays behind is a guard, a
# wrapper (``try`` / ``with``) or the final ``return``. Measured on 301 plans
# from seven repos (Python, TS, Go, Java, Rust): 52 sat at or above 0.75, and
# nearly all of those at 0.8 or more left only such a shell. The span offered
# instead may be the same block minus a statement, just under the cut.
_MAX_BODY_SHARE = 0.75


@dataclass(frozen=True)
class Extraction:
    """One safe Extract Method candidate over a function.

    ``start_line`` / ``end_line`` bound the span (1-indexed, inclusive).
    ``params`` are the inferred IN variables, ``returns`` the inferred OUT
    variable(s). ``slice_nloc`` is the span's code lines, counted with the
    walker's NLOC rule (blank, comment-only and docstring lines excluded, as in
    the function's own ``nloc``), and ``ccn_removed`` the decision points it
    carries (the complexity the residual method sheds). ``needs_async`` is
    True when the span suspends (an ``await_kinds`` token outside any nested
    scope): the helper must be async and its call site awaited.

    ``uses_receiver`` says whether the span names the instance its method
    runs on (``self``, ``this``, ``super``, a Go receiver; lambdas in the
    span count, they share it), and ``receiver_assigns`` which of its fields
    the span assigns directly (``self.x = ...``, ``self.x[k] += ...``,
    ``this.n++``, unpacking targets): not through an alias, a mutating call
    (``self.items.append``) or ``del``. Both are None when the language cannot
    tell (no receiver model, or a bare name that may be a field in Java /
    C++), and ``receiver_assigns`` also when an assignment target rooted at
    the receiver has a shape this does not read.
    """

    start_line: int
    end_line: int
    params: tuple[str, ...]
    returns: tuple[str, ...]
    slice_nloc: int
    ccn_removed: int
    needs_async: bool = False
    uses_receiver: bool | None = None
    receiver_assigns: tuple[str, ...] | None = None


class _Scan(NamedTuple):
    """The node kinds the per-statement walk counts, built once per function."""

    decisions: frozenset[str]
    jumps: frozenset[str]
    scopes: frozenset[str]
    exit_macros: tuple[frozenset[str], frozenset[str]]
    awaits: tuple[frozenset[str], frozenset[str]]
    lambdas: frozenset[str] = frozenset()
    receiver: Receiver | None = None


class _Metrics(NamedTuple):
    """What :func:`_span_metrics` finds in a span or a whole function body."""

    decisions: int
    jump: bool
    awaits: bool
    receiver_use: bool = False
    receiver_assigns: frozenset[str] = frozenset()


class _Prefix(NamedTuple):
    """Per-statement metrics of one block as prefix sums (``writes`` stays
    per statement: only the spans that pass every gate union it)."""

    decisions: list[int]
    jumps: list[int]
    awaits: list[int]
    nested: list[int]
    code: list[int]
    receiver: list[int]
    assigns: list[frozenset[str]]


def find_extractions(
    analysis: FunctionAnalysis, lmap: LanguageNodeMap, receiver: Receiver | None = None
) -> list[Extraction]:
    """Return safe Extract Method candidates for *analysis*, best first.

    Best is most complexity removed, then largest span, then fewest parameters,
    then earliest -- a deterministic order. Empty when the function has no AST
    node retained or no span clears the extractability gates. *receiver* is
    the function's instance (``DefUseDialect.receiver``), None when unknown.
    """
    fn_node = analysis.fn_node
    if fn_node is None:
        return []
    # A subtree tree-sitter could not parse is not a function whose statements
    # we understand, so no span in it is safe to lift. This fires on macro-heavy
    # C/C++ headers, where an unterminated function-like macro
    # (``ABSL_NAMESPACE_BEGIN``) makes the parser emit one bogus
    # ``function_definition`` spanning a whole class or namespace — proposing to
    # extract "statements" from that is a wrong suggestion, not a weak one.
    # Language-agnostic and strictly subtractive: a clean parse is unaffected.
    if fn_node.has_error:
        return []
    body = fn_node.child_by_field_name("body")
    if body is None:
        return []
    # The statement container of the body (Go nests it in a ``statement_list``
    # inside the ``block``); spans covering it whole are not extractions.
    body_container = _unwrap_container(body, lmap.block_kinds)
    lines = _function_lines(fn_node)
    max_slice_nloc = _MAX_BODY_SHARE * _stmts_nloc(body_container.named_children, lines)

    def_lines, use_lines = _var_lines(analysis.def_use)
    declared_first = _declared_before_read(analysis.def_use)
    hoisted = _hoisted_bindings(def_lines, use_lines)
    decl_lines = _declaration_lines(analysis.def_use)
    shared = _closure_state(analysis.def_use, def_lines, use_lines)
    scan = _scan_for(lmap, receiver, fn_node)
    scope_kinds = scan.scopes
    free_writes = _free_write_lines(analysis.def_use) if receiver and receiver.implicit else []
    # Expression-oriented grammars (nonempty ``statement_wrapper_kinds``): a
    # block's last child that is not a statement is its tail expression -- the
    # block's implicit value. A span ending on one would silently drop that
    # value when lifted into a helper, so such spans are refused outright.
    tail_stmt_kinds = (
        lmap.statement_wrapper_kinds | lmap.local_decl_kinds
        if lmap.statement_wrapper_kinds
        else None
    )

    out: list[Extraction] = []
    evaluated = 0
    for block, loop in all_blocks(fn_node, lmap.block_kinds, scope_kinds, lmap.loop_kinds):
        stmts = block.named_children
        n = len(stmts)
        is_body = block.id == body_container.id
        keeps_tail = tail_stmt_kinds is not None and _tail_is_block_value(
            stmts, tail_stmt_kinds, fn_node, lmap
        )
        if n >= _MIN_STMTS:
            pre = _block_prefix(stmts, scan, lines, lmap)
        for i in range(n):
            for j in range(i, n):
                evaluated += 1
                if evaluated > _MAX_CANDIDATES:
                    return _sorted(out)
                length = j - i + 1
                if length < _MIN_STMTS:
                    continue
                # Never extract the whole function body (that is not a split).
                if is_body and length == n:
                    continue
                if keeps_tail and j == n - 1:
                    continue
                decisions = pre.decisions[j + 1] - pre.decisions[i]
                has_jump = pre.jumps[j + 1] > pre.jumps[i]
                if has_jump or decisions < _MIN_CCN_REMOVED:
                    continue
                slice_nloc = pre.code[j + 1] - pre.code[i]
                if not _MIN_SLICE_NLOC <= slice_nloc < max_slice_nloc:
                    continue
                span = stmts[i : j + 1]
                s = span[0].start_point[0] + 1
                e = span[-1].end_point[0] + 1
                params, returns = _infer_in_out(def_lines, use_lines, s, e, declared_first)
                if len(params) > _MAX_PARAMS or len(returns) > _MAX_RETURNS:
                    continue
                if not _outs_definitely_assigned(span, returns, def_lines, lmap):
                    continue
                if any(s <= first_def <= e and first_use < s for first_def, first_use in hoisted):
                    continue
                if _declaration_escapes(
                    s, e, returns, decl_lines, def_lines, use_lines, declared_first
                ):
                    continue
                if pre.nested[j + 1] > pre.nested[i]:
                    continue
                if shared is not None and _closure_state_crosses(shared, span, s, e, block, lmap):
                    continue
                if loop is not None and not _loop_carry_free(
                    span, loop, s, e, def_lines, use_lines, lmap
                ):
                    continue
                uses, assigns = _receiver_facts(receiver, pre, i, j, _count_in(free_writes, s, e))
                out.append(
                    Extraction(
                        start_line=s,
                        end_line=e,
                        params=params,
                        returns=returns,
                        slice_nloc=slice_nloc,
                        ccn_removed=decisions,
                        needs_async=pre.awaits[j + 1] > pre.awaits[i],
                        uses_receiver=uses,
                        receiver_assigns=assigns,
                    )
                )
    return _sorted(out)


@dataclass(frozen=True)
class FunctionFacts:
    """What a whole function does, counted on the complexity walk's visits.

    ``awaits``: it suspends outside any nested scope. ``is_generator``: it
    yields. ``uses_receiver`` / ``receiver_assigns`` as on :class:`Extraction`,
    for the whole body; ``receiver_assigns`` is None where an implicit receiver
    (Java, C++) can write fields by bare name, which only def/use can tell.
    ``early_exits``: returns and raises other than a final one.
    """

    awaits: bool
    is_generator: bool
    uses_receiver: bool | None
    receiver_assigns: tuple[str, ...] | None
    early_exits: int


class _ReceiverSink:
    """``BodyTally.on_receiver``: whether the body names its receiver, and
    which of its fields it assigns."""

    __slots__ = ("assigns", "receiver", "uses")

    def __init__(self, receiver: Receiver) -> None:
        self.receiver = receiver
        self.uses = False
        self.assigns: set[str] = set()

    def __call__(self, node: Node) -> None:
        self.uses = _receiver_ref(node, self.receiver, self.assigns) or self.uses


def body_tally(fn_node: Node, lmap: LanguageNodeMap, receiver: Receiver | None) -> BodyTally:
    """An empty tally for *fn_node*, for the complexity walk to fill. The
    receiver is watched only when the function's text names it."""
    watched = receiver is not None and receiver.names and mentions_receiver(fn_node, receiver.names)
    return BodyTally(
        yield_kinds=lmap.yield_kinds,
        exit_kinds=lmap.return_kinds | lmap.raise_kinds,
        await_kinds=lmap.await_kinds,
        await_scope_kinds=lmap.await_scope_kinds,
        lambda_kinds=lmap.lambda_kinds,
        on_receiver=_ReceiverSink(receiver) if watched else None,
        # What ``_receiver_ref`` can answer for: a write, or a receiver leaf.
        receiver_kinds=receiver.write_kinds | receiver.names | {SUPER, "identifier"}
        if watched
        else frozenset(),
    )


def function_facts(
    fn_node: Node, lmap: LanguageNodeMap, receiver: Receiver | None, tally: BodyTally
) -> FunctionFacts:
    """:class:`FunctionFacts` from a filled :func:`body_tally`."""
    body = fn_node.child_by_field_name("body") or fn_node
    stmts = body.named_children
    exit_kinds = tally.exit_kinds
    tail = stmts[-1] if stmts else None
    ends_in_exit = tail is not None and (
        tail.type in exit_kinds or any(c.type in exit_kinds for c in tail.named_children[:1])
    )
    sink = tally.on_receiver if isinstance(tally.on_receiver, _ReceiverSink) else None
    uses, assigns = _whole_receiver_facts(
        receiver, sink.uses if sink else False, frozenset(sink.assigns) if sink else frozenset()
    )
    return FunctionFacts(
        awaits=tally.awaits,
        is_generator=tally.yields > 0,
        uses_receiver=uses,
        receiver_assigns=assigns,
        early_exits=max(0, tally.exits - ends_in_exit),
    )


def _whole_receiver_facts(
    receiver: Receiver | None, uses: bool, assigned: frozenset[str]
) -> tuple[bool | None, tuple[str, ...] | None]:
    if receiver is None:
        return None, None
    if not receiver.names:
        return False, ()
    if receiver.implicit:
        return (True if uses else None), None
    return uses, None if _UNREAD_TARGET in assigned else tuple(sorted(assigned))


def _scan_for(lmap: LanguageNodeMap, receiver: Receiver | None, fn_node: Node) -> _Scan:
    """The walk's kinds for *fn_node*. The receiver is looked for only when
    its name (or ``super``) appears in the function's text at all: most
    functions never mention it, and then no node needs the check."""
    if receiver is not None and not (receiver.names and mentions_receiver(fn_node, receiver.names)):
        receiver = None
    return _Scan(
        decisions=lmap.branch_kinds
        | lmap.loop_kinds
        | lmap.case_kinds
        | lmap.catch_kinds
        | lmap.boolean_operator_kinds,
        jumps=lmap.return_kinds
        | lmap.raise_kinds
        | lmap.break_kinds
        | lmap.continue_kinds
        | lmap.yield_kinds,
        scopes=lmap.function_kinds | lmap.lambda_kinds,
        exit_macros=_exit_macros(lmap),
        awaits=_awaits(lmap),
        lambdas=lmap.lambda_kinds,
        receiver=receiver,
    )


def _block_prefix(
    stmts: list[Node], scan: _Scan, lines: list[str], lmap: LanguageNodeMap
) -> _Prefix:
    """One subtree walk per statement, so every span's metrics come from
    prefix sums in O(1): a span's decision count is the sum over its
    statements and its jump bit the OR, and re-walking per span made the
    candidate loop O(n^2 * subtree). The named-nested-function check rides
    the same sums for the same reason."""
    pre = _Prefix([0], [0], [0], [0], [0], [0], [])
    for st in stmts:
        m = _span_metrics([st], scan)
        pre.decisions.append(pre.decisions[-1] + m.decisions)
        pre.jumps.append(pre.jumps[-1] + m.jump)
        pre.awaits.append(pre.awaits[-1] + m.awaits)
        pre.nested.append(pre.nested[-1] + _holds_a_named_nested_function([st], lmap))
        pre.code.append(pre.code[-1] + _stmts_nloc([st], lines))
        pre.receiver.append(pre.receiver[-1] + m.receiver_use)
        pre.assigns.append(m.receiver_assigns)
    return pre


def _free_write_lines(def_use: FunctionDefUse) -> list[int]:
    """Sorted lines writing a name the function neither declares nor takes
    as a parameter: in Java or C++ that is a field (or a C++ global) written
    without ``this``, so the span's receiver writes are not all known."""
    params = {p.name for p in def_use.params}
    declared = {d.var for d in def_use.definitions if d.declares or d.declared_at is not None}
    return sorted(
        d.line for d in def_use.definitions if d.var not in params and d.var not in declared
    )


def _receiver_facts(
    receiver: Receiver | None, pre: _Prefix, i: int, j: int, free_writes: int
) -> tuple[bool | None, tuple[str, ...] | None]:
    """``uses_receiver`` / ``receiver_assigns`` for the span over statements
    ``i..j`` (see :class:`Extraction`)."""
    if receiver is None:
        return None, None
    if not receiver.names:
        return False, ()
    named = pre.receiver[j + 1] > pre.receiver[i]
    found = frozenset().union(*pre.assigns[i : j + 1])
    fields = None if _UNREAD_TARGET in found else tuple(sorted(found))
    if receiver.implicit:
        return (True if named else None), (None if free_writes else fields)
    return named, fields


def _tail_is_block_value(
    stmts: list[Node], tail_stmt_kinds: frozenset[str], fn_node: Node, lmap: LanguageNodeMap
) -> bool:
    """True when the block's last statement is its value, which a span may not
    end on (lifting it would drop the value).

    That is a bare tail expression, and also an unterminated statement-wrapped
    one (Rust parses a tail ``if`` / ``match`` with no ``;`` as an
    ``expression_statement``) whose value something consumes: an ``else``
    block in a ``let`` initializer, the body of a function with a return type.
    A loop body's value, and that of a block in statement position, is
    discarded, so ending there stays allowed.
    """
    if not stmts:
        return False
    last = stmts[-1]
    if last.type not in tail_stmt_kinds:
        return True
    if last.type not in lmap.statement_wrapper_kinds or last.children[-1].type == ";":
        return False
    return _block_value_used(last.parent, fn_node, lmap)


def _block_value_used(block: Node, fn_node: Node, lmap: LanguageNodeMap) -> bool:
    """Whether the value of *block* reaches anything, climbing through the
    conditional chain that carries it (``else`` / ``match`` arms)."""
    node = block
    while True:
        parent = node.parent
        if parent is None or parent.id == fn_node.id:
            return fn_node.child_by_field_name("return_type") is not None
        verdict = _value_hop(node, parent, lmap)
        if verdict is not None:
            return verdict
        node = parent


def _value_hop(node: Node, parent: Node, lmap: LanguageNodeMap) -> bool | None:
    """One step of :func:`_block_value_used`: True or False when *parent*
    settles whether *node*'s value is used, None to keep climbing."""
    if parent.type in lmap.loop_kinds:
        return False  # a loop body's value is discarded
    if parent.type in lmap.block_kinds:
        # Only the last statement carries a block's value onward.
        return None if parent.named_children[-1].id == node.id else False
    if parent.type in lmap.statement_wrapper_kinds:
        return False if parent.children[-1].type == ";" else None
    if parent.type in lmap.value_passthrough_kinds:
        return None
    return True  # a let initializer, an argument, an operand


def _function_lines(fn_node: Node) -> list[str]:
    """Source rows indexed by absolute row, rebuilt from the function's own text.

    Lets the walker's NLOC rule (``_code_line_numbers``) run here without the
    file's bytes; rows above the function are never read, so they stay empty.
    """
    text = (fn_node.text or b"").decode("utf-8", errors="replace")
    return [""] * fn_node.start_point[0] + text.splitlines()


def _stmts_nloc(stmts: list[Node], lines: list[str]) -> int:
    """Code lines of *stmts* by the walker's NLOC rule, summed per statement."""
    return sum(len(_code_line_numbers(st, lines, drop_docstrings=True)) for st in stmts)


def _sorted(candidates: list[Extraction]) -> list[Extraction]:
    return sorted(
        candidates,
        key=lambda x: (-x.ccn_removed, -x.slice_nloc, len(x.params), x.start_line),
    )


def _var_lines(def_use: FunctionDefUse) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Per-variable sorted def lines and use lines from D2's facts.

    Parameter definitions are included (seeded at the signature line), so a
    parameter naturally counts as "defined before" any body span. Reads inside
    nested closures (``def_use.captured.reads``) count as uses at their own line.
    An import is not a def here: a helper re-imports a name rather than take
    or return it, and :func:`_declaration_escapes` refuses a span holding an
    import that later code still reads.
    """
    def_lines: dict[str, list[int]] = defaultdict(list)
    use_lines: dict[str, list[int]] = defaultdict(list)
    imported: set[str] = set()
    for d in def_use.definitions:
        if d.imports:
            imported.add(d.var)
        else:
            def_lines[d.var].append(d.line)
    for bdu in def_use.blocks.values():
        for u in bdu.uses:
            # A may-def's paired use is bookkeeping, not a read: counted, a
            # binder declared inside a ``match`` arm became its own parameter.
            if not u.echo:
                use_lines[u.name].append(u.line)
    # A closure's read counts where the closure is written: lifting the code
    # around it moves the read with it. Only names this function binds matter.
    for u in def_use.captured.reads:
        if u.name in def_lines or u.name in imported:
            use_lines[u.name].append(u.line)
    for lines in def_lines.values():
        lines.sort()
    for lines in use_lines.values():
        lines.sort()
    return def_lines, use_lines


def _declared_before_read(def_use: FunctionDefUse) -> dict[str, frozenset[int]]:
    """Per variable, the lines where a declaration of it comes before its first
    read on the same line.

    Lines alone cannot order a write and a read that share one. A C-style
    ``for (int i = 0; i < n; i++)`` declares ``i`` and then reads it, while
    ``total = total + a[i]`` reads ``total`` and then writes it. Only a
    declaration is ordered here, by where its declarator ends: a read past that
    point sees the new name, and a read inside the declaration's own
    initializer (Go's ``x := x + 1`` in an inner scope) still sees the outer
    one. A plain assignment keeps the line rule.
    """
    first_read: dict[tuple[str, int], int] = {}
    for bdu in def_use.blocks.values():
        for u in bdu.uses:
            if u.echo:
                continue
            key = (u.name, u.line)
            first_read[key] = min(u.column, first_read.get(key, u.column))
    declared: dict[str, set[int]] = defaultdict(set)
    for d in def_use.definitions:
        # No read on the line sorts before any declarator, so it never matches.
        if d.declared_at is not None and d.declared_at <= first_read.get((d.var, d.line), -1):
            declared[d.var].add(d.line)
    return {var: frozenset(lines) for var, lines in declared.items()}


def _infer_in_out(
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
    s: int,
    e: int,
    declared_first: dict[str, frozenset[int]] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Infer IN (parameters) and OUT (return) variables for span ``[s, e]``.

    IN: a variable read in the span whose first in-span read is not preceded by
    an in-span write, and which has a definition before the span (a parameter or
    an earlier assignment). A write on the same line as that read precedes it
    only where *declared_first* (:func:`_declared_before_read`) says the line
    declares the name first. OUT: a variable written in the span and read after
    it, with no redefinition between the span and that first later read.
    """
    params: list[str] = []
    returns: list[str] = []
    for var in sorted(set(def_lines) | set(use_lines)):
        dl = def_lines.get(var, [])
        ul = use_lines.get(var, [])
        in_uses = [ln for ln in ul if s <= ln <= e]
        in_defs = [ln for ln in dl if s <= ln <= e]

        if in_uses and any(ln < s for ln in dl):
            first_use = in_uses[0]
            declared = first_use in (declared_first or {}).get(var, ())
            if not declared and not any(ln < first_use for ln in in_defs):
                params.append(var)

        if in_defs:
            after_uses = [ln for ln in ul if ln > e]
            if after_uses:
                first_after = after_uses[0]
                redefined = any(e < ln < first_after for ln in dl)
                if not redefined:
                    returns.append(var)
    return tuple(params), tuple(returns)


class _SharedState(NamedTuple):
    """Per variable a closure shares with the function, sorted line lists:
    the closures' reads and writes (``Captured.shared`` / ``.writes``), the
    function's own defs, all its references, and its binding declarations."""

    reads: dict[str, list[int]]
    writes: dict[str, list[int]]
    defs: dict[str, list[int]]
    refs: dict[str, list[int]]
    binds: dict[str, frozenset[int]]


def _closure_state(
    def_use: FunctionDefUse,
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
) -> _SharedState | None:
    """The closure-shared locals of a function, or None when it has none
    (then the gate costs nothing per span)."""
    reads: dict[str, list[int]] = defaultdict(list)
    writes: dict[str, list[int]] = defaultdict(list)
    for out, occurrences in (
        (reads, def_use.captured.shared),
        (writes, def_use.captured.writes),
    ):
        for occ in occurrences:
            if occ.name in def_lines:  # a name this function binds
                out[occ.name].append(occ.line)
    if not reads and not writes:
        return None
    names = set(reads) | set(writes)
    return _SharedState(
        reads={var: sorted(lines) for var, lines in reads.items()},
        writes={var: sorted(lines) for var, lines in writes.items()},
        defs={var: def_lines.get(var, []) for var in names},
        refs={var: sorted((*def_lines.get(var, ()), *use_lines.get(var, ()))) for var in names},
        binds=_declaration_lines(def_use, binding=True),
    )


def _count_in(lines: list[int], s: int, e: int) -> int:
    return bisect_right(lines, e) - bisect_left(lines, s)


def _closure_state_crosses(
    st: _SharedState, span: list[Node], s: int, e: int, block: Node, lmap: LanguageNodeMap
) -> bool:
    """True when a local a closure shares crosses the span boundary.

    A closure runs when it is called, not where it is written, so line
    liveness cannot place its reads and writes; an IN/OUT signature copies a
    value where the closure needs the variable itself. Refused:

    - a closure in the span writes a local anything outside the span refers
      to (code, or a closure there): the lifted closure writes the helper's
      copy. A counter declared in the span with its ``inc`` closure is
      refused this way when the count is read after the span;
    - a closure outside the span writes a local the span refers to: the span
      reads or writes a stale copy;
    - the span writes a local that a closure written above it reads: the
      closure runs later and sees the old value. Measured on hermes
      ``apps/desktop/src/lib/ansi.ts::parseAnsi``, whose ``pushText`` reads
      the ``bold`` / ``fg`` a span inside the loop sets.

    The last two are waived when the span's name is a binding of its own
    (:func:`_span_binds`, :func:`_span_only_rebinds`): the closure's variable
    is then another one.
    """
    for var in st.reads.keys() | st.writes.keys():
        writes = st.writes.get(var, [])
        reads = st.reads.get(var, [])
        refs = st.refs[var]
        w_in = _count_in(writes, s, e)
        if w_in and (
            w_in < len(writes)
            or _count_in(reads, s, e) < len(reads)
            or _count_in(refs, s, e) < len(refs)
        ):
            return True
        stale = w_in < len(writes) and _count_in(refs, s, e) > 0
        if stale and not _span_binds(st, var, span, block, lmap):
            return True
        overwritten = bool(reads) and reads[0] < s and _count_in(st.defs[var], s, e) > 0
        if overwritten and not _span_only_rebinds(st, var, s, e, block, lmap):
            return True
    return False


def _declares_in(node: Node, lines: frozenset[int], lmap: LanguageNodeMap) -> bool:
    """True when *node* is a declaration statement holding one of *lines*."""
    lo, hi = node.start_point[0] + 1, node.end_point[0] + 1
    return node.type in lmap.local_decl_kinds and any(lo <= ln <= hi for ln in lines)


def _declared_above(binds: frozenset[int], block: Node, s: int, lmap: LanguageNodeMap) -> bool:
    """True when a statement of *block* above the span already declares the
    name: a re-declaration in the same scope (Go's ``v, err := h()``) is an
    assignment to it, not a new binding."""
    return any(
        node.end_point[0] + 1 < s and _declares_in(node, binds, lmap)
        for node in block.named_children
    )


def _span_binds(
    st: _SharedState, var: str, span: list[Node], block: Node, lmap: LanguageNodeMap
) -> bool:
    """True when one of the span's own top-level statements declares a new
    binding of *var*, so every reference to it in the span is to that one.

    Top-level only: a declaration in a nested block shadows just that block.
    """
    binds = st.binds.get(var, frozenset())
    s = span[0].start_point[0] + 1
    return (
        bool(binds)
        and not _declared_above(binds, block, s, lmap)
        and any(_declares_in(node, binds, lmap) for node in span)
    )


def _span_only_rebinds(
    st: _SharedState, var: str, s: int, e: int, block: Node, lmap: LanguageNodeMap
) -> bool:
    """True when every write the span makes to *var* creates a new binding
    (a declaration or a loop binder, at any depth), so none of them reaches
    the variable a closure outside the span reads."""
    binds = st.binds.get(var, frozenset())
    defs = st.defs[var][bisect_left(st.defs[var], s) : bisect_right(st.defs[var], e)]
    return all(ln in binds for ln in defs) and not _declared_above(binds, block, s, lmap)


def _holds_a_named_nested_function(span: list[Node], lmap: LanguageNodeMap) -> bool:
    """True when the span contains a nested function that binds its own name.

    Def/use is computed per function and deliberately does not descend into a
    nested scope, so a sibling closure's call to such a helper is invisible
    here. Lifting the declaration out of the scope moves the binding away from
    those callers, and nothing in the facts would show it.

    A named nested function exists to be called from elsewhere in its scope, so
    the safe answer is to refuse rather than to guess at its callers. An
    anonymous lambda bound to a local is a value and stays governed by the
    ordinary IN/OUT rules. Measured on this repo's ranked head:
    ``vscode/src/features/changeIntel.ts::registerChangeIntel`` offered its whole
    ``render`` declaration while a sibling closure called it.
    """
    kinds = lmap.function_kinds
    if not kinds:
        return False
    for st in span:
        stack = [st]
        while stack:
            node = stack.pop()
            if node.type in kinds and node.child_by_field_name("name") is not None:
                return True
            stack.extend(node.children)
    return False


def _hoisted_bindings(
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
) -> list[tuple[int, int]]:
    """First-definition lines of names the function reads before defining them.

    A read before the only definition of a name can only work by hoisting: a
    JS/TS ``function foo()`` declaration is visible from the top of its scope,
    so code above it calls it. A span holding that definition cannot be lifted -
    an OUT cannot express it, because the value has to exist *before* the span
    runs, not after - so :func:`find_extractions` refuses any span containing
    one of these lines.

    Returns ``(first_def, first_use)`` pairs. A span refuses when its range holds
    a ``first_def`` whose ``first_use`` sits *above* the span: the earlier read
    has no earlier definition to answer it, so the span holds the only one.
    A read that sits inside the span, before the definition, is a different
    shape and is left to the loop-carried gate.

    Computed once per function because the shape is rare (usually no name
    qualifies at all), and asking per candidate span meant walking every
    variable thousands of times. Both lists are sorted by :func:`_var_lines`,
    so the first entry of each is the earliest.

    Measured on this repo's ranked head:
    ``vscode/src/features/changeIntel.ts::registerChangeIntel`` offered its whole
    ``render`` declaration as a span while ``render(partners)`` sat above it.
    """
    hoisted: list[tuple[int, int]] = []
    for var, defs in def_lines.items():
        uses = use_lines.get(var)
        if defs and uses and uses[0] < defs[0]:
            hoisted.append((defs[0], uses[0]))
    return sorted(hoisted)


def _declaration_lines(
    def_use: FunctionDefUse, *, binding: bool = False
) -> dict[str, frozenset[int]]:
    """Per variable, the lines that declare it (a ``let`` / ``var`` / ``:=`` /
    typed local, as the dialect marks with ``declared_at``, or an import).
    With *binding*, the lines that create a new binding instead
    (``declares``: multi-line declarators and loop binders included, a TS/JS
    ``var`` not)."""
    lines: dict[str, set[int]] = defaultdict(set)
    for d in def_use.definitions:
        if (d.declares if binding else d.declared_at is not None or d.imports):
            lines[d.var].add(d.line)
    return {var: frozenset(found) for var, found in lines.items()}


def _declaration_escapes(
    s: int,
    e: int,
    returns: tuple[str, ...],
    decl_lines: dict[str, frozenset[int]],
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
    declared_first: dict[str, frozenset[int]],
) -> bool:
    """True when the span declares a name the code after it still refers to.

    A returned name is fine: the caller declares it from the helper's result.
    Otherwise the declaration leaves with the span and the caller's next
    reference names nothing, even when that reference is a plain assignment
    (Go ``var out T`` in the span, ``out = x`` after it, which is why liveness
    did not make it an OUT). A later reference that declares the name afresh
    (``y, err := g()``, a second ``for i := ...``, whose reads on that line
    follow the declaration per *declared_first*) needs nothing from the span.
    Block scopes are not modelled, so a same-named variable of an outer scope
    read after the span also refuses: a missed span, never a broken one.
    """
    return any(
        var not in returns
        and any(s <= ln <= e for ln in declared)
        and _needed_after(var, e, declared, def_lines, use_lines, declared_first)
        for var, declared in decl_lines.items()
    )


def _needed_after(
    var: str,
    e: int,
    declared: frozenset[int],
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
    declared_first: dict[str, frozenset[int]],
) -> bool:
    """True when the first reference to *var* after line *e* relies on an
    earlier declaration: it is a plain use or assignment, not a fresh one."""
    uses = use_lines.get(var, ())
    after = [ln for ln in (*def_lines.get(var, ()), *uses) if ln > e]
    if not after:
        return False
    first = min(after)
    if first not in declared:
        return True
    return first in uses and first not in declared_first.get(var, ())


def _outs_definitely_assigned(
    span: list[Node],
    returns: tuple[str, ...],
    def_lines: dict[str, list[int]],
    lmap: LanguageNodeMap,
) -> bool:
    """True when every OUT variable is written on *every* path through the span.

    A conditionally written OUT is the unsound case line-based liveness cannot
    see: the helper returns the unwritten value on the paths that skip the
    write, and the caller's assignment then clobbers the live one. The proof
    needs branch structure, so it runs structurally here and the span is
    refused whenever the proof does not go through.
    """
    return all(_stmts_assign(span, var, def_lines.get(var, []), lmap) for var in returns)


def _stmts_assign(stmts: list[Node], var: str, defs: list[int], lmap: LanguageNodeMap) -> bool:
    """True when *stmts* writes *var* on every path through them."""
    return any(_stmt_assigns(st, var, defs, lmap) for st in stmts)


def _stmt_assigns(st: Node, var: str, defs: list[int], lmap: LanguageNodeMap) -> bool:
    """True only when *st* is proved to write *var* however it is entered.

    Positive proof only: a statement kind this cannot classify returns False.
    Defaulting to True would let any statement sharing a line with a
    conditional write stand in as the proof.
    """
    lo = st.start_point[0] + 1
    hi = st.end_point[0] + 1
    if not any(lo <= d <= hi for d in defs):
        return False
    if _is_conditional(st, lmap):
        return _conditional_assigns(st, var, defs, lmap)
    if st.type in lmap.block_kinds:
        container = _unwrap_container(st, lmap.block_kinds)
        return _stmts_assign(container.named_children, var, defs, lmap)
    if st.type in lmap.with_kinds or st.type in lmap.statement_wrapper_kinds:
        return _stmts_assign(list(st.named_children), var, defs, lmap)
    return _unconditional_write(st, var, lmap)


def _unconditional_write(st: Node, var: str, lmap: LanguageNodeMap) -> bool:
    """True when *st* contains a write to *var* no branch or loop guards.

    The walk stops at any control container (a loop body may run zero times, a
    branch arm may not be taken, a catch arm may not fire) and at nested
    scopes, so only writes on the statement's own straight-line path count.
    """
    write_kinds = lmap.assignment_kinds | lmap.augmented_assign_kinds | lmap.local_decl_kinds
    if not write_kinds:
        return False
    barriers = (
        lmap.loop_kinds
        | lmap.try_kinds
        | lmap.catch_kinds
        | lmap.switch_kinds
        | lmap.case_kinds
        | lmap.branch_kinds
        | lmap.if_kinds
        | lmap.function_kinds
        | lmap.lambda_kinds
    )
    if st.type in barriers:
        return False
    stack = [st]
    while stack:
        node = stack.pop()
        if node.id != st.id and node.type in barriers:
            continue
        if node.type in write_kinds and _writes_var(node, var):
            return True
        stack.extend(node.children)
    return False


def _writes_var(node: Node, var: str) -> bool:
    """True when *var* is on the binding side of this write node."""
    for field in ("left", "pattern", "declarator", "name"):
        target = node.child_by_field_name(field)
        if target is not None:
            return var in _identifiers(target)
    return var in _identifiers(node)


def _is_conditional(node: Node, lmap: LanguageNodeMap) -> bool:
    return node.type in lmap.if_kinds or (
        node.type in lmap.branch_kinds and node.child_by_field_name("condition") is not None
    )


def _conditional_assigns(node: Node, var: str, defs: list[int], lmap: LanguageNodeMap) -> bool:
    """True when a conditional chain is exhaustive and every arm writes *var*.

    Two chain shapes. Python flattens ``elif``/``else`` into repeated
    ``alternative`` fields on one ``if``, so the terminal else is a sibling of
    the chained arms. The C family nests instead: the single ``alternative`` is
    another ``if``, whose own else ends the chain. Both must be exhaustive.
    """
    consequence = node.child_by_field_name("consequence") or node.child_by_field_name("body")
    if consequence is None:
        return False
    alternatives = list(node.children_by_field_name("alternative"))
    if not alternatives:
        return False  # no else at all: some path skips the write
    if not _branch_assigns(consequence, var, defs, lmap):
        return False
    for alt in alternatives:
        if _is_conditional(alt, lmap):
            # Nested chain (C family): it carries the chain's terminal else.
            if len(alternatives) == 1:
                return _conditional_assigns(alt, var, defs, lmap)
            # Flattened chain (Python's ``elif``): the else is a sibling below.
            arm = alt.child_by_field_name("consequence") or alt.child_by_field_name("body")
            if arm is None or not _branch_assigns(arm, var, defs, lmap):
                return False
        elif not _branch_assigns(alt, var, defs, lmap):
            return False
    return any(not _is_conditional(alt, lmap) for alt in alternatives)


def _branch_assigns(node: Node, var: str, defs: list[int], lmap: LanguageNodeMap) -> bool:
    if _is_conditional(node, lmap):
        return _conditional_assigns(node, var, defs, lmap)
    if node.type in lmap.block_kinds:
        container = _unwrap_container(node, lmap.block_kinds)
        return _stmts_assign(container.named_children, var, defs, lmap)
    return _stmts_assign(list(node.named_children), var, defs, lmap)


def _loop_carry_free(
    span: list[Node],
    loop: Node,
    s: int,
    e: int,
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
    lmap: LanguageNodeMap,
) -> bool:
    """True when a span nested in *loop* carries no state between iterations.

    Two shapes are refused. A variable the span writes that the loop also reads
    without the span having written it first is loop-carried: that read takes
    the previous iteration's value, and lifting the write into a helper whose
    result is discarded silently drops it.

    The read does not have to sit above the span. ``clojure.py::_spec_namespaces``
    reads ``expect_ns`` and then writes it, both inside one candidate span, in a
    ``while`` loop: textually the read precedes the write, so nothing follows
    the span to make the variable an OUT, and the state machine breaks on the
    next iteration. Testing only for reads above the span missed it, so the test
    is "read with no in-span write before it", which subsumes the old one.

    Also refused: a call on a name the loop header reads, which mutates the
    state the loop iterates over (``entries.remove(entry)`` inside
    ``for entry in entries``), and which an IN/OUT signature cannot express.
    """
    loop_start = loop.start_point[0] + 1
    for var, lines in def_lines.items():
        in_span_writes = [ln for ln in lines if s <= ln <= e]
        if not in_span_writes:
            continue
        for read in use_lines.get(var, []):
            if not loop_start <= read <= e:
                continue
            if not any(write < read for write in in_span_writes):
                return False

    carried = _loop_header_names(loop, lmap)
    if not carried:
        return True
    scope_kinds = lmap.function_kinds | lmap.lambda_kinds
    for st in span:
        for call in _descend(st, lmap.call_kinds, scope_kinds):
            callee = call.child_by_field_name("function")
            if callee is None:
                named = call.named_children
                callee = named[0] if named else None
            root = _receiver_root(callee) if callee is not None else None
            if root is not None and root in carried:
                return False
    return True


def _receiver_root(node: Node) -> str | None:
    """The base name a call is made on: ``entries`` in ``entries.remove(x)``.

    Only the root counts. Matching any name in the callee read
    ``existing.pages.push(page)`` as touching the ``pages`` the loop iterates,
    when the receiver is ``existing`` and the collision is in a member name.
    """
    cur = node
    while True:
        nxt = None
        for field in ("object", "operand", "value", "argument", "function"):
            child = cur.child_by_field_name(field)
            if child is not None:
                nxt = child
                break
        if nxt is None:
            break
        cur = nxt
    if not cur.children and cur.type.endswith("identifier"):
        text = cur.text
        return text.decode("utf-8", "replace") if text else None
    return None


# Fields a loop header binds through. Read structurally: a line-range test
# cannot tell the header's binder from a variable reassigned on the body's
# first line, and getting that wrong drops the iterated collection out of the
# carried set, which is exactly the name the mutation check exists to catch.
_BINDER_FIELDS = ("left", "pattern", "declarator", "name")


def _loop_header_names(loop: Node, lmap: LanguageNodeMap) -> set[str]:
    """Names the loop header reads, minus the ones it binds (the loop variable)."""
    body = loop.child_by_field_name("body")
    names: set[str] = set()
    # The binder is a field of the loop itself in most grammars, and of a
    # header clause it wraps in Go's ``range``.
    bound: set[str] = _binder_names(loop)
    for child in loop.children:
        if body is not None and child.id == body.id:
            continue
        names |= _identifiers(child)
        bound |= _binder_names(child)
    return names - bound


def _binder_names(node: Node) -> set[str]:
    """Identifiers in the binding position of *node* or of a header clause it
    wraps (Go nests its range clause inside the ``for``)."""
    out: set[str] = set()
    for field in _BINDER_FIELDS:
        target = node.child_by_field_name(field)
        if target is not None:
            out |= _identifiers(target)
    if not out:
        for child in node.named_children:
            for field in _BINDER_FIELDS:
                target = child.child_by_field_name(field)
                if target is not None:
                    out |= _identifiers(target)
    return out


def _identifiers(node: Node) -> set[str]:
    out: set[str] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if not cur.children and cur.type.endswith("identifier"):
            text = cur.text
            if text:
                out.add(text.decode("utf-8", "replace"))
        stack.extend(cur.children)
    return out


def _descend(root: Node, kinds: frozenset[str], skip: frozenset[str]) -> list[Node]:
    found: list[Node] = []
    stack = [root]
    while stack:
        cur = stack.pop()
        if cur.type in kinds:
            found.append(cur)
        for child in cur.children:
            if child.type in skip:
                continue
            stack.append(child)
    return found


def _unwrap_container(node: Node, block_kinds: frozenset[str]) -> Node:
    """Descend through a single nested statement-container (Go's
    ``block`` -> ``statement_list``) to the node whose named children are the
    actual statements; returns *node* unchanged when it is already that node."""
    cur = node
    while True:
        named = cur.named_children
        if len(named) == 1 and named[0].type in block_kinds:
            cur = named[0]
        else:
            return cur


def all_blocks(
    fn_node: Node,
    block_kinds: frozenset[str],
    scope_kinds: frozenset[str],
    loop_kinds: frozenset[str],
) -> list[tuple[Node, Node | None]]:
    """Every statement container in the function (body + nested), excluding the
    bodies of nested functions / lambdas, each paired with the innermost loop
    enclosing it (``None`` at loop-free depth)."""
    body = fn_node.child_by_field_name("body")
    if body is None:
        return []
    blocks: list[tuple[Node, Node | None]] = []
    stack: list[tuple[Node, Node | None]] = [(body, None)]
    while stack:
        node, loop = stack.pop()
        if node.type in block_kinds:
            blocks.append((node, loop))
        is_loop = node.type in loop_kinds
        body = node.child_by_field_name("body") if is_loop else None
        for child in node.children:
            if child.type in scope_kinds:
                continue
            # Only the loop's body repeats. A ``for ... else`` clause runs once.
            in_loop = node if (is_loop and body is not None and child.id == body.id) else loop
            stack.append((child, in_loop))
    return blocks


def _exit_macros(lmap: LanguageNodeMap) -> tuple[frozenset[str], frozenset[str]]:
    """The macro node kinds and the macro names that exit the function."""
    return lmap.exit_macro_kinds, lmap.exit_macro_names


def _awaits(lmap: LanguageNodeMap) -> tuple[frozenset[str], frozenset[str]]:
    """The await tokens, and the nodes that own the awaits inside them."""
    return lmap.await_kinds, lmap.await_scope_kinds


def _is_jump(
    node: Node,
    jump_kinds: frozenset[str],
    exit_macros: tuple[frozenset[str], frozenset[str]],
) -> bool:
    """True for a jump node, or a macro whose name is in *exit_macros*
    (matched by its last segment: ``bail`` in ``anyhow::bail!``)."""
    if node.type in jump_kinds:
        return True
    kinds, names = exit_macros
    if node.type not in kinds:
        return False
    macro = node.child_by_field_name("macro")
    name = macro.child_by_field_name("name") or macro if macro is not None else None
    return name is not None and bool(name.text) and name.text.decode("utf-8", "replace") in names


def _span_metrics(span: list[Node], scan: _Scan) -> _Metrics:
    """Decision points, jump and await presence, and receiver references
    within *span*: a candidate span's statements, or a function body's for
    facts about the whole function. Nested scopes are not descended into,
    except a lambda for the receiver alone, since it shares the instance. A
    macro named in ``scan.exit_macros`` counts as a jump; an await under one
    of the await scope kinds (``async`` blocks) does not suspend the
    function."""
    await_kinds, await_scope_kinds = scan.awaits
    receiver = scan.receiver
    decisions = 0
    has_jump = has_await = uses = False
    assigns: set[str] = set()
    for root in span:
        # (node, whether its awaits count, inside a lambda: receiver only)
        stack: list[tuple[Node, bool, bool]] = [(root, True, False)]
        while stack:
            node, counts_await, nested = stack.pop()
            if receiver is not None:
                uses = _receiver_ref(node, receiver, assigns) or uses
            if not nested:
                t = node.type
                has_jump = has_jump or _is_jump(node, scan.jumps, scan.exit_macros)
                has_await = has_await or (counts_await and t in await_kinds)
                decisions += t in scan.decisions
                counts_await = counts_await and t not in await_scope_kinds
            _push_children(node, stack, counts_await, nested, scan)
    return _Metrics(decisions, has_jump, has_await, uses, frozenset(assigns))


def _push_children(
    node: Node,
    stack: list[tuple[Node, bool, bool]],
    counts_await: bool,
    nested: bool,
    scan: _Scan,
) -> None:
    """Queue *node*'s children: nested scopes are skipped, except a lambda
    when the receiver is looked for (it shares the instance)."""
    for child in node.children:
        if child.type not in scan.scopes:
            stack.append((child, counts_await, nested))
        elif scan.receiver is not None and child.type in scan.lambdas:
            stack.append((child, False, True))


# Index access on a receiver field (``self.cache[k] = v``) assigns into it.
_INDEX_KINDS = frozenset({"subscript", "subscript_expression", "index_expression", "array_access"})
# Unpacking targets whose named children are targets themselves
# (``a, self.b = ...``, ``[this.x, y] = ...``).
_UNPACK_KINDS = frozenset(
    {
        "pattern_list",
        "tuple_pattern",
        "list_pattern",
        "expression_list",
        "tuple",
        "list",
        "array_pattern",
        "object_pattern",
        "parenthesized_expression",
        "list_splat_pattern",
        "rest_pattern",
    }
)
# Marks a target rooted at the receiver whose shape is not read, so the
# span's assigned fields are unknown.
_UNREAD_TARGET = ""


def _receiver_ref(node: Node, receiver: Receiver, assigns: set[str]) -> bool:
    """True when *node* names the receiver; an assignment to one of its
    fields adds the field's name to *assigns* (the receiver leaf under it is
    visited next)."""
    if node.type in receiver.write_kinds:
        target = (
            node.child_by_field_name("left")
            or node.child_by_field_name("argument")
            or (node.named_children[0] if node.named_children else None)
        )
        if target is not None:
            _collect_assigned(target, receiver, assigns)
        return False
    return _is_receiver(node, receiver)


def _collect_assigned(target: Node, receiver: Receiver, assigns: set[str]) -> None:
    """The receiver fields one assignment target writes, unpacking included;
    a target of another shape that mentions the receiver makes them unknown."""
    if target.type in _UNPACK_KINDS:
        for each in target.named_children:
            _collect_assigned(each, receiver, assigns)
        return
    if target.type in ("assignment_pattern", "object_assignment_pattern"):  # ``[this.a = 1]``
        left = target.child_by_field_name("left")
        if left is not None:
            _collect_assigned(left, receiver, assigns)
        return
    if target.type == "pair_pattern":  # ``{key: this.a}``
        value = target.child_by_field_name("value")
        if value is not None:
            _collect_assigned(value, receiver, assigns)
        return
    field = _receiver_field(target, receiver)
    if field:
        assigns.add(field)
    elif target.type not in receiver.access_kinds | _INDEX_KINDS and mentions_receiver(
        target, receiver.names
    ):
        assigns.add(_UNREAD_TARGET)


def _is_receiver(node: Node, receiver: Receiver) -> bool:
    """A leaf naming the receiver: a ``this`` / ``self`` / ``super`` token,
    or an identifier spelling a named receiver (Python ``self``, Go ``s``)
    or ``super`` (Python's no-argument ``super()``)."""
    if node.children or not node.text:
        return False
    if node.type in receiver.names or node.type == SUPER:
        return True
    if node.type != "identifier":
        return False
    text = node.text.decode("utf-8", "replace")
    return text in receiver.names or text == SUPER


def _receiver_field(target: Node, receiver: Receiver) -> str | None:
    """``x`` for an assignment target ``self.x``, ``self.x.y`` or ``self.x[k]``."""
    cur: Node | None = target
    while cur is not None and (cur.type in receiver.access_kinds or cur.type in _INDEX_KINDS):
        if cur.type in receiver.access_kinds:
            field = self_member_name(cur, receiver.names)
            if field is not None:
                return field
        cur = member_object(cur)
    return None
