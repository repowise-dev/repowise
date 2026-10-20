"""Extract Method slicing: find safe, single-exit spans with IN/OUT inference.

Given a function's analysis (CFG + def/use), it finds contiguous statement
spans that can be lifted into a helper method without changing behaviour, and
infers the helper's signature:

- **IN (parameters)** -- variables the span *reads* whose value is produced
  before the span (defined-before, used-inside).
- **OUT (return)** -- variables the span *defines* whose value may reach a
  read after it along some CFG path (so the helper returns the live value).

**Extractability predicate (precision-first).** A span is a candidate only when
it cuts at statement boundaries within a single block (so never a partial
branch or a mid-``try`` split), contains no control-flow jump that leaves the
region (``return`` / ``raise`` / ``break`` / ``continue`` -> single clean exit),
removes real complexity (at least one decision point), is substantial enough to
matter without being nearly the whole body, and has at most one return and a
small parameter list. Everything else is suppressed -- ten great extractions,
not two hundred maybes.

Node kinds come from the language's ``LanguageNodeMap``, so one slicer serves
every full-tier language. The helpers live in the ``slice_*`` modules and are
re-exported here; patch a name where it is looked up.
"""

# ruff: noqa: F401

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from ..complexity.ast_utils import member_object, self_member_name
from ..complexity.body_facts import BodyTally, FactKinds, is_exit, step
from ..complexity.nloc import _code_line_numbers
from .dialects.base import SUPER, mentions_receiver
from .raises import exception_flow_refusal
from .reaching import definitions_at, observed_definitions
from .slice_liveness import (
    _bindings_escape,
    _declaration_escapes,
    _declaration_lines,
    _declared_before_read,
    _hoisted_bindings,
    _import_reads,
    _infer_in_out,
    _needed_after,
    _outer_write_escapes,
    _reads_observing,
    _statements_by_line,
    _valueless_lines,
    _var_lines,
)
from .slice_nodes import (
    _BINDER_FIELDS,
    _awaits,
    _binder_names,
    _descend,
    _exit_macros,
    _function_lines,
    _holds_a_named_nested_function,
    _identifiers,
    _loop_header_names,
    _receiver_root,
    _unwrap_container,
    all_blocks,
)
from .slice_receiver import (
    _FUNCTION_KINDS,
    _INDEX_KINDS,
    _UNPACK_KINDS,
    _UNREAD_TARGET,
    FunctionFacts,
    _collect_assigned,
    _ends_in_exit,
    _free_write_lines,
    _function_kinds,
    _is_receiver,
    _receiver_facts,
    _receiver_field,
    _receiver_kinds,
    _receiver_ref,
    _ReceiverSink,
    _whole_receiver_facts,
    body_tally,
    function_facts,
)
from .slice_state import (
    _closure_state,
    _closure_state_crosses,
    _count_in,
    _declared_above,
    _declares_in,
    _loop_carry_free,
    _rebinding_defs_dropped,
    _SharedState,
    _span_binds,
    _span_only_rebinds,
)
from .slice_writes import (
    _branch_assigns,
    _conditional_assigns,
    _is_conditional,
    _outs_definitely_assigned,
    _stmt_assigns,
    _stmts_assign,
    _trailing_constant_output,
    _unconditional_write,
    _writes_var,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap
    from .analyze import FunctionAnalysis
    from .dialects.base import Receiver


# Gates (precision-first; tuned to suppress trivial or unwieldy extractions).
_MIN_STMTS = 2  # at least two statements
# The extracted helper is substantial: code lines, the walker's NLOC rule, so a
# span padded with comments does not clear it.
_MIN_SLICE_NLOC = 5
_MIN_CCN_REMOVED = 1  # extraction must remove a real decision point
_MAX_PARAMS = 5  # too many ins => the span is not cohesive
_MAX_RETURNS = 1  # a single clean return
# Backstop against a pathological function producing too many sub-ranges.
_MAX_CANDIDATES = 4000
# A span holding this share of the body's code lines or more is the whole
# function under another name: what stays behind is only a guard, a wrapper or
# the final ``return``.
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
    # Built from the fields above by :func:`_scan_for`; derived when absent.
    kinds: FactKinds | None = None
    #: What leaves the function itself (``return``); a raise does not, for a
    #: staged plan's helper, which re-raises it unchanged.
    exits: frozenset[str] = frozenset()
    yields: frozenset[str] = frozenset()


class _Metrics(NamedTuple):
    """What :func:`_span_metrics` finds in a span or a whole function body."""

    decisions: int
    jump: bool
    awaits: bool
    receiver_use: bool = False
    receiver_assigns: frozenset[str] = frozenset()
    #: Function exits and yields (``_Scan.exits``, exit macros, ``yield``).
    exits: int = 0


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
    exits: list[int]


def find_extractions(
    analysis: FunctionAnalysis,
    lmap: LanguageNodeMap,
    receiver: Receiver | None = None,
    prefixes: dict[int, _Prefix] | None = None,
    refused: set[str] | None = None,
) -> list[Extraction]:
    """Return safe Extract Method candidates for *analysis*, best first.

    Best is most complexity removed, then largest span, then fewest parameters,
    then earliest. *receiver* is the function's instance, None when unknown.
    *prefixes*, when given, receives each walked block's :class:`_Prefix` by
    block id; *refused* receives why a span that cleared every other gate would
    change exception flow (:func:`raises.exception_flow_refusal`).
    """
    fn_node = analysis.fn_node
    # A subtree tree-sitter could not parse (a macro-heavy C/C++ header emitting
    # one bogus function over a whole namespace) has no statements we understand.
    if fn_node is None or fn_node.has_error:
        return []
    body = fn_node.child_by_field_name("body")
    if body is None:
        return []
    slicer = _FunctionSlicer(analysis, lmap, receiver, fn_node, body)
    out: list[Extraction] = []
    for block, loop in all_blocks(fn_node, lmap.block_kinds, slicer.scan.scopes, lmap.loop_kinds):
        if not slicer.block_extractions(block, loop, prefixes, refused, out):
            break
    return _sorted(out)


class _Block(NamedTuple):
    """One statement container: its statements and per-block facts."""

    node: Node
    loop: Node | None
    stmts: list[Node]
    pre: _Prefix
    resources: list[int]


class _Span(NamedTuple):
    """Statements ``i..j`` of a block, spanning lines ``start..end``."""

    i: int
    j: int
    nodes: list[Node]
    start: int
    end: int


class _FunctionSlicer:
    """The per-function facts every candidate span is checked against, built once."""

    def __init__(
        self,
        analysis: FunctionAnalysis,
        lmap: LanguageNodeMap,
        receiver: Receiver | None,
        fn_node: Node,
        body: Node,
    ) -> None:
        self.analysis = analysis
        self.lmap = lmap
        self.receiver = receiver
        self.fn_node = fn_node
        # Go nests the statements in a ``statement_list``; spans covering it whole are not extractions.
        self.body_container = _unwrap_container(body, lmap.block_kinds)
        self.lines = _function_lines(fn_node)
        self.max_slice_nloc = _MAX_BODY_SHARE * _stmts_nloc(
            self.body_container.named_children, self.lines
        )
        def_use = analysis.def_use
        self.def_lines, self.use_lines = _var_lines(def_use)
        self.import_reads = _import_reads(analysis)
        self.declared_first = _declared_before_read(def_use)
        self.valueless = _valueless_lines(def_use)
        # Built on the first span that clears the cheap gates.
        self.reads: dict[str, list[tuple[int, frozenset[int]]]] | None = None
        self.hoisted = _hoisted_bindings(self.def_lines, self.use_lines)
        self.decl_lines = _declaration_lines(def_use)
        self.shared = _closure_state(def_use, self.def_lines, self.use_lines)
        self.outer = def_use.outer
        self.carry_defs = _rebinding_defs_dropped(analysis, self.def_lines, lmap)
        self.scan = _scan_for(lmap, receiver, fn_node)
        self.free_writes = _free_write_lines(def_use) if receiver and receiver.implicit else []
        # Expression-oriented grammars: a block's last non-statement child is its
        # value, and a span ending on it would drop that value.
        self.tail_stmt_kinds = (
            lmap.statement_wrapper_kinds | lmap.local_decl_kinds
            if lmap.statement_wrapper_kinds
            else None
        )
        self.evaluated = 0
        self.exhausted = False

    def block_extractions(
        self,
        block: Node,
        loop: Node | None,
        prefixes: dict[int, _Prefix] | None,
        refused: set[str] | None,
        out: list[Extraction],
    ) -> bool:
        """Append *block*'s candidates to *out*; False once the candidate budget is spent."""
        lmap = self.lmap
        stmts = block.named_children
        # A match/switch body holds arms, not statements; each arm's body is a block of its own.
        if any(stmt.type in lmap.case_kinds for stmt in stmts):
            return True
        n = len(stmts)
        resources = _scoped_resource_indexes(stmts, lmap)
        keeps_tail = self.tail_stmt_kinds is not None and _tail_is_block_value(
            stmts, self.tail_stmt_kinds, self.fn_node, lmap
        )
        spans = self._span_bounds(n, block.id == self.body_container.id, keeps_tail)
        if n < _MIN_STMTS:
            # No span is long enough, but each still counts against the budget.
            for _ in spans:
                pass
            return not self.exhausted
        pre = _block_prefix(stmts, self.scan, self.lines, lmap)
        if prefixes is not None:
            prefixes[block.id] = pre
        blk = _Block(block, loop, stmts, pre, resources)
        for i, j in spans:
            decisions = pre.decisions[j + 1] - pre.decisions[i]
            if pre.jumps[j + 1] > pre.jumps[i] or decisions < _MIN_CCN_REMOVED:
                continue
            slice_nloc = pre.code[j + 1] - pre.code[i]
            if not _MIN_SLICE_NLOC <= slice_nloc < self.max_slice_nloc:
                continue
            found = self._extraction(blk, i, j, decisions, slice_nloc, refused)
            if found is not None:
                out.append(found)
        return not self.exhausted

    def _span_bounds(self, n: int, is_body: bool, keeps_tail: bool) -> Iterator[tuple[int, int]]:
        """Ranges ``(i, j)`` long enough to lift, never the whole body or a block's
        value tail. Every range counts against the candidate budget."""
        for i in range(n):
            for j in range(i, n):
                self.evaluated += 1
                if self.evaluated > _MAX_CANDIDATES:
                    self.exhausted = True
                    return
                length = j - i + 1
                if length < _MIN_STMTS or (is_body and length == n) or (keeps_tail and j == n - 1):
                    continue
                yield i, j

    def _extraction(
        self,
        blk: _Block,
        i: int,
        j: int,
        decisions: int,
        slice_nloc: int,
        refused: set[str] | None,
    ) -> Extraction | None:
        nodes = blk.stmts[i : j + 1]
        span = _Span(i, j, nodes, nodes[0].start_point[0] + 1, nodes[-1].end_point[0] + 1)
        interface = self._interface(span)
        if interface is None or not self._self_contained(blk, span, interface[1]):
            return None
        s, e = span.start, span.end
        reason = exception_flow_refusal(self.analysis.cfg, s, e, self.reads, self.def_lines)
        if reason is not None:
            if refused is not None:
                refused.add(reason)
            return None
        params, returns = interface
        pre = blk.pre
        free_writes = _count_in(self.free_writes, s, e)
        uses, assigns = _receiver_facts(self.receiver, pre, i, j, free_writes)
        return Extraction(
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

    def _interface(self, span: _Span) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        """The span's ``(params, returns)``, or None when they are too many or an
        OUT is not written on every path."""
        s, e = span.start, span.end
        if any(import_line < s <= use_line <= e for import_line, use_line in self.import_reads):
            return None
        byref_alias_lines = self.def_lines.get("__byref_alias__")
        if byref_alias_lines:
            b_lo = bisect_left(byref_alias_lines, s)
            b_hi = bisect_right(byref_alias_lines, e)
            if b_lo < b_hi:
                return None
        if self.reads is None:
            self.reads = _reads_observing(self.analysis, self.def_lines)
        params, returns = _infer_in_out(
            self.def_lines,
            self.use_lines,
            s,
            e,
            self.declared_first,
            reads=self.reads,
            valueless=self.valueless,
        )
        if len(params) > _MAX_PARAMS or len(returns) > _MAX_RETURNS:
            return None
        if not _outs_definitely_assigned(span.nodes, returns, self.def_lines, self.lmap):
            return None
        if _trailing_constant_output(span.nodes, s, e, returns, self.def_lines, self.lmap):
            return None
        return params, returns

    def _self_contained(self, blk: _Block, span: _Span, returns: tuple[str, ...]) -> bool:
        """No binding, resource, nested function, closure state or loop-carried
        value ties the span to code it does not hold."""
        s, e = span.start, span.end
        if any(s <= first_def <= e and first_use < s for first_def, first_use in self.hoisted):
            return False
        if _bindings_escape(
            s, e, returns, self.decl_lines, self.def_lines, self.use_lines,
            self.declared_first, self.outer,
        ):
            return False
        pre = blk.pre
        if _disposes_early(blk.resources, span.i, span.j, pre.code):
            return False
        if pre.nested[span.j + 1] > pre.nested[span.i]:
            return False
        if self.shared is not None and _closure_state_crosses(
            self.shared, span.nodes, s, e, blk.node, self.lmap
        ):
            return False
        return blk.loop is None or _loop_carry_free(
            span.nodes, blk.loop, s, e, self.carry_defs, self.use_lines, self.lmap
        )


def _scoped_resource_indexes(stmts: list[Node], lmap: LanguageNodeMap) -> list[int]:
    """Positions in *stmts* of declarations whose resource the block disposes
    at its end (``scoped_resource_decl_kinds``)."""
    if not lmap.scoped_resource_decl_kinds:
        return []
    return [
        k
        for k, st in enumerate(stmts)
        if st.type in lmap.scoped_resource_decl_kinds
        and any(not c.is_named and c.type in lmap.scoped_resource_decl_tokens for c in st.children)
    ]


def _disposes_early(resources: list[int], i: int, j: int, code: list[int]) -> bool:
    """Whether statements ``i..j`` hold a declaration from *resources* while code
    after them still runs in the block (*code* is the block's code-line prefix
    sum). A helper would dispose the resource when it returns, before that code
    runs, where the original disposes it at the end of the block."""
    return code[-1] > code[j + 1] and any(i <= k <= j for k in resources)


def _scan_for(lmap: LanguageNodeMap, receiver: Receiver | None, fn_node: Node) -> _Scan:
    """The walk's kinds for *fn_node*. The receiver is looked for only when
    its name (or ``super``) appears in the function's text at all: most
    functions never mention it, and then no node needs the check."""
    if receiver is not None and not (receiver.names and mentions_receiver(fn_node, receiver.names)):
        receiver = None
    scan = _Scan(
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
        exits=lmap.return_kinds,
        yields=lmap.yield_kinds,
    )
    return scan._replace(kinds=_span_kinds(scan))


def _block_prefix(
    stmts: list[Node], scan: _Scan, lines: list[str], lmap: LanguageNodeMap
) -> _Prefix:
    """One subtree walk per statement, so every span's metrics come from
    prefix sums in O(1): a span's decision count is the sum over its
    statements and its jump bit the OR, and re-walking per span made the
    candidate loop O(n^2 * subtree). The named-nested-function check rides
    the same sums for the same reason."""
    pre = _Prefix([0], [0], [0], [0], [0], [0], [], [0])
    for st in stmts:
        m = _span_metrics([st], scan)
        pre.decisions.append(pre.decisions[-1] + m.decisions)
        pre.jumps.append(pre.jumps[-1] + m.jump)
        pre.awaits.append(pre.awaits[-1] + m.awaits)
        pre.nested.append(pre.nested[-1] + _holds_a_named_nested_function([st], lmap))
        pre.code.append(pre.code[-1] + _stmts_nloc([st], lines))
        pre.receiver.append(pre.receiver[-1] + m.receiver_use)
        pre.assigns.append(m.receiver_assigns)
        pre.exits.append(pre.exits[-1] + m.exits)
    return pre


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


def _stmts_nloc(stmts: list[Node], lines: list[str]) -> int:
    """Code lines of *stmts* by the walker's NLOC rule, summed per statement."""
    return sum(len(_code_line_numbers(st, lines, drop_docstrings=True)) for st in stmts)


def _sorted(candidates: list[Extraction]) -> list[Extraction]:
    return sorted(
        candidates,
        key=lambda x: (-x.ccn_removed, -x.slice_nloc, len(x.params), x.start_line),
    )


def _span_kinds(scan: _Scan) -> FactKinds:
    await_kinds, await_scope_kinds = scan.awaits
    return FactKinds(
        decisions=scan.decisions,
        jumps=scan.jumps,
        exits=scan.exits,
        exit_macros=scan.exit_macros,
        yields=scan.yields,
        awaits=await_kinds,
        await_scopes=await_scope_kinds,
        lambdas=scan.lambdas,
        receiver=_receiver_kinds(scan.receiver) if scan.receiver is not None else frozenset(),
    )


def _span_metrics(span: list[Node], scan: _Scan) -> _Metrics:
    """Decision points, jump and await presence, and receiver references
    within *span*: a candidate span's statements, or a function body's.
    Each node is read by :func:`body_facts.step`, the rule the complexity
    walk applies to a whole function. Nested scopes are not descended into,
    except a lambda for the receiver alone, since it shares the instance."""
    kinds = scan.kinds or _span_kinds(scan)
    watched = kinds.watched
    sink = _ReceiverSink(scan.receiver) if scan.receiver is not None else None
    tally = BodyTally(kinds, sink)
    for root in span:
        stack: list[tuple[Node, int]] = [(root, 0)]
        while stack:
            node, scope = stack.pop()
            if node.type in watched:
                scope = step(tally, node, scope)
            _push_children(node, stack, scope, scan)
    return _Metrics(
        tally.decisions,
        tally.jump,
        tally.awaits,
        sink.uses if sink else False,
        frozenset(sink.assigns) if sink else frozenset(),
        tally.exits + tally.yields,
    )


def _push_children(
    node: Node, stack: list[tuple[Node, int]], scope: int, scan: _Scan
) -> None:
    """Queue *node*'s children: nested scopes are skipped, except a lambda
    when the receiver is looked for (it shares the instance)."""
    for child in node.children:
        t = child.type
        if t not in scan.scopes or (scan.receiver is not None and t in scan.lambdas):
            stack.append((child, scope))
