"""Staged Extract Method: split a brain method into helpers called in order.

One helper cannot take a function of CCN 70 under the bar: the slicer's best
span is a small share of it. This module cuts the function's *effective body*
into K contiguous stages, each a helper the function then calls in sequence.

- **Effective body.** A ``try`` / ``with`` statement holding most of a block's
  decision points is a wrapper: its body is cut instead, and the wrapper (with
  its ``except`` / ``finally`` and anything around it) stays in the function.
  Past that, a loop or ``if`` holding most of them offers its body (the arm
  holding the most) as a deeper block, repeatedly; the statement itself stays
  in the function, which calls the stages from inside it. A deeper block's
  split is taken only when it lifts more than the outer one.
- **Stages.** Each is a run of that body's statements with ``CCN <= 10`` and
  ``NLOC <= 60``, at least two decision points, no ``return`` / ``yield``, and
  no ``break`` / ``continue`` that leaves it (a ``raise`` propagates from a
  helper unchanged). In a loop body, a value one iteration writes and the
  next reads comes back out of the stage writing it. Its inputs and
  outputs come from the same line liveness as a single span; an output not
  written on every path is also passed in (``inout``), so a skipped write hands
  back the value it came with. Every input must be written on every path into
  the stage (:mod:`assigned`): one bound only under a condition would raise
  at the call. A name a stage declares in a nested block (a loop's ``const``)
  is the stage's own, never an output. A guard (``if not x: raise``) whose
  names the code after the stage reads stays in the function: type checkers
  narrow ``x`` from it. A stage may not change exception flow
  (:func:`raises.exception_flow_refusal`): inside a ``try`` body it may not
  write a value a handler or ``finally`` reads.
- **Cuts.** A small dynamic program over the statement boundaries: lift as many
  decision points as possible, then prefer narrow interfaces (fewest names
  crossing a cut), cuts at a banner comment, a ``timed()`` label or a blank
  line, stages that do not run across a banner, and fewer stages.
  Deterministic: ties keep the earliest cut. A timer started in one
  statement and stopped in another (``timings.start("x")`` ...
  ``timings.stop("x")``) is never split by a stage edge.
- **Context.** When more than five values are read by two or more stages and
  never rebound once the first stage starts, they travel on one parameter
  object instead of as separate parameters. The receiver never does.
- **Composition.** The definitions each read observes
  (:func:`reaching.observed_definitions`) are checked against the plan: a
  value defined outside a stage and read inside it must be one of its
  inputs, and one defined in a stage and read outside it one of its
  outputs. A plan that fails is refused whole.

Function-local imports are not inputs: a helper re-imports a name, and the
function drops an import nothing it keeps still reads.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from ..complexity.nloc import is_string_stmt
from .assigned import DefiniteAssignment
from .loop_cut import carried, escapes
from .raises import exception_flow_refusal
from .reaching import observed_definitions
from .slice import (
    _MIN_SLICE_NLOC,
    Extraction,
    _block_prefix,
    _closure_state,
    _closure_state_crosses,
    _declaration_escapes,
    _declaration_lines,
    _declared_before_read,
    _disposes_early,
    _function_lines,
    _hoisted_bindings,
    _identifiers,
    _infer_in_out,
    _outer_write_escapes,
    _Prefix,
    _reads_observing,
    _receiver_facts,
    _scan_for,
    _scoped_resource_indexes,
    _stmt_assigns,
    _unwrap_container,
    _valueless_lines,
    _var_lines,
)
from .span import exiting_guard, is_comment, section_heads

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap
    from .analyze import FunctionAnalysis
    from .defuse import FunctionDefUse
    from .dialects.base import Receiver

STAGE_MAX_CCN = 10
STAGE_MAX_NLOC = 60
# A stage lifting one decision point is a renamed ``if``: it merges into a
# neighbour or stays in the function.
_STAGE_MIN_DECISIONS = 2
# More shared values than a signature carries well (the slicer's own cap).
_CONTEXT_OVER = 5
# Why a stage that cleared every other check was not offered (``StagePlan.refused``).
OUTER_WRITE = "outer_write"
SWITCH_ARM_WRITE = "switch_arm_write"
# A wrapper holding this share of a block's decision points is unwrapped.
_WRAPPER_SHARE = 0.6
# Loop bodies and ``if`` arms looked into below the outermost cut.
_MAX_DEPTH = 6

# Cut costs, in one integer scale. A decision point left in the function
# outweighs everything else; then the interface, the cut hints, the count.
_KEPT_DECISION = 200
_PER_STAGE = 20
_PER_NAME = 6
_UNHINTED_CUT = 8
_BLANK_LINE_CUT = 4
_CROSSED_BANNER = 6

# A timer's start and stop by label, in any language's call syntax.
_TIMER_START = re.compile(r"""\.start\(\s*["']([^"'\n]+)["']""")
_TIMER_STOP = re.compile(r"""\.stop\(\s*["']([^"'\n]+)["']""")


@dataclass(frozen=True)
class StagePlan:
    """The stages in source order, the names carried on the parameter
    object (empty: plain parameters), and per stage the names it reads that
    the function imports outside it, which the helper must import itself;
    ``moved_imports`` are those nothing left in the function reads.
    ``reason`` says why there are no stages; None when there are.
    ``refused`` names why a stage that cleared every other check was not
    offered (:data:`OUTER_WRITE`, :data:`SWITCH_ARM_WRITE`, and the
    exception-flow reasons of :mod:`raises`)."""

    stages: tuple[Extraction, ...] = ()
    context: tuple[str, ...] = ()
    imports: tuple[tuple[str, ...], ...] = ()
    moved_imports: frozenset[str] = frozenset()
    reason: str | None = None
    refused: frozenset[str] = frozenset()

    @property
    def ccn_removed(self) -> int:
        return sum(x.ccn_removed for x in self.stages)

    @property
    def slice_nloc(self) -> int:
        return sum(x.slice_nloc for x in self.stages)


def find_stages(
    analysis: FunctionAnalysis,
    lmap: LanguageNodeMap,
    receiver: Receiver | None = None,
    *,
    max_returns: int = 3,
    prefixes: dict[int, _Prefix] | None = None,
) -> StagePlan:
    """The staged split of *analysis*, or a :class:`StagePlan` with only a
    ``reason`` when none composes. *max_returns* caps a stage's outputs (a
    language whose call cannot unpack a tuple passes 1). *prefixes* are the
    per-block statement metrics :func:`slice.find_extractions` already
    walked; a block missing from them is walked here."""
    fn_node = analysis.fn_node
    if fn_node is None or fn_node.has_error:
        return StagePlan(reason="no_tree")
    body = fn_node.child_by_field_name("body")
    if body is None:
        return StagePlan(reason="no_tree")
    blocks = _Blocks(analysis, lmap, receiver, prefixes if prefixes is not None else {})
    facts = _FunctionFacts(analysis, lmap)
    first: StagePlan | None = None
    best: StagePlan | None = None
    for block, loops in _descent(_unwrap_container(body, lmap.block_kinds), blocks, lmap):
        # A block cannot lift more decision points than it holds.
        if best is not None and blocks.code(block)[1].decisions[-1] <= best.ccn_removed:
            break
        plan = _plan_block(analysis, lmap, receiver, blocks, facts, block, loops, max_returns)
        first = first or plan
        # A deeper level wins only by lifting more: ties keep the outer cut.
        if plan.stages and (best is None or plan.ccn_removed > best.ccn_removed):
            best = plan
    return replace(best or first or StagePlan(reason="no_tree"), refused=frozenset(facts.refused))


def _plan_block(
    analysis: FunctionAnalysis,
    lmap: LanguageNodeMap,
    receiver: Receiver | None,
    blocks: _Blocks,
    facts: _FunctionFacts,
    block: Node,
    loops: tuple[Node, ...],
    max_returns: int,
) -> StagePlan:
    """The staged split of *block*, nested in *loops* (outermost first)."""
    code, pre = blocks.code(block)
    if len(code) < 2:
        return StagePlan(reason="one_statement")
    cutter = _Cutter(facts, lmap, receiver, block, code, pre, max_returns, loops)
    stages = cutter.cut()
    if len(stages) < 2:
        return StagePlan(reason="fewer_than_two_stages")
    context = _context(stages, facts.def_lines, receiver)
    if not _inputs_held(stages, facts.def_lines):
        return StagePlan(reason="composition")
    composed = _composes(analysis, stages, context)
    if composed is None:
        return StagePlan(reason="composition")
    imports, moved = composed
    return StagePlan(stages=stages, context=context, imports=imports, moved_imports=moved)


class _Blocks:
    """Each block's code statements and their prefix sums, read from the
    slicer's walk (*prefixes*) and walked only for a block it skipped."""

    def __init__(
        self,
        analysis: FunctionAnalysis,
        lmap: LanguageNodeMap,
        receiver: Receiver | None,
        prefixes: dict[int, _Prefix],
    ) -> None:
        self.analysis, self.lmap, self.prefixes = analysis, lmap, prefixes
        self.scan = _scan_for(lmap, receiver, analysis.fn_node)

    def code(self, block: Node) -> tuple[list[Node], _Prefix]:
        """The block's statements without comments or a leading docstring,
        and their metrics as prefix sums."""
        named = block.named_children
        keep = [k for k, c in enumerate(named) if not is_comment(c)]
        if keep and is_string_stmt(named[keep[0]]):
            keep = keep[1:]
        full = self.prefixes.get(block.id)
        if full is None:
            lines = _function_lines(self.analysis.fn_node)
            full = self.prefixes[block.id] = _block_prefix(named, self.scan, lines, self.lmap)
        return [named[k] for k in keep], _select(full, keep)


def _select(pre: _Prefix, keep: list[int]) -> _Prefix:
    """*pre* over the statements at *keep* only."""
    out = _Prefix([0], [0], [0], [0], [0], [0], [], [0])
    sums = ("decisions", "jumps", "awaits", "nested", "code", "receiver", "exits")
    for k in keep:
        for name in sums:
            series = getattr(out, name)
            full = getattr(pre, name)
            series.append(series[-1] + full[k + 1] - full[k])
        out.assigns.append(pre.assigns[k])
    return out


def _descent(
    root: Node, blocks: _Blocks, lmap: LanguageNodeMap
) -> list[tuple[Node, tuple[Node, ...]]]:
    """The blocks a staged split may cut, outermost first, each with the loops
    holding it: the body under its ``try`` / ``with`` wrappers (module
    docstring), then, while one statement holds most of a block's decision
    points, the loop body or ``if`` arm it puts them in."""
    wrappers = lmap.try_kinds | lmap.with_kinds
    block = root
    while (step := _dominant_arm(block, blocks, lmap, wrappers)) is not None:
        block = step[0]
    chain: list[tuple[Node, tuple[Node, ...]]] = [(block, ())]
    loops: tuple[Node, ...] = ()
    nested = wrappers | lmap.loop_kinds | lmap.if_kinds
    while len(chain) <= _MAX_DEPTH and (step := _dominant_arm(block, blocks, lmap, nested)):
        block, holder = step
        if holder.type in lmap.loop_kinds:
            loops = (*loops, holder)
        chain.append((block, loops))
    return chain


def _dominant_arm(
    block: Node, blocks: _Blocks, lmap: LanguageNodeMap, kinds: frozenset[str]
) -> tuple[Node, Node] | None:
    """The block inside *block*'s most complex statement (of *kinds*) holding
    at least ``_WRAPPER_SHARE`` of *block*'s decision points, with that
    statement; None when there is none."""
    code, pre = blocks.code(block)
    per = [pre.decisions[k + 1] - pre.decisions[k] for k in range(len(code))]
    total = sum(per)
    if not total:
        return None
    top = code[max(range(len(code)), key=per.__getitem__)]
    if top.type not in kinds:
        return None
    arms = [_unwrap_container(a, lmap.block_kinds) for a in _arms(top, lmap)]
    arms = [a for a in arms if a.type in lmap.block_kinds]
    if not arms:
        return None
    inner = max(arms, key=lambda a: blocks.code(a)[1].decisions[-1])
    if blocks.code(inner)[1].decisions[-1] < _WRAPPER_SHARE * total:
        return None
    return inner, top


def _arms(st: Node, lmap: LanguageNodeMap) -> list[Node]:
    """The statement blocks *st* runs: a wrapper's or loop's body, each arm of
    an ``if`` (Python ``elif`` / ``else`` clauses, a TS / JS ``else``)."""
    if st.type not in lmap.if_kinds:
        body = st.child_by_field_name("body")
        return [body] if body is not None else []
    out = [st.child_by_field_name("consequence")]
    for clause in st.children:
        if clause.type == "elif_clause":
            out.append(clause.child_by_field_name("consequence"))
        elif clause.type == "else_clause":
            out.append(clause.child_by_field_name("body") or next(iter(clause.named_children), None))
    return [a for a in out if a is not None]


class _FunctionFacts:
    """The per-function facts every block's cut reads, built once."""

    def __init__(self, analysis: FunctionAnalysis, lmap: LanguageNodeMap) -> None:
        self.analysis = analysis
        def_use = analysis.def_use
        self.def_lines, self.use_lines = _var_lines(def_use)
        self.declared_first = _declared_before_read(def_use)
        self.valueless = _valueless_lines(def_use)
        self.hoisted = _hoisted_bindings(self.def_lines, self.use_lines)
        self.decl_lines = _declaration_lines(def_use)
        self.shared = _closure_state(def_use, self.def_lines, self.use_lines)
        self.outer = def_use.outer
        self.arm_writes = _arm_writes(analysis, lmap)
        self.refused: set[str] = set()
        self.reads = _reads_observing(analysis, self.def_lines)
        self.assigned = DefiniteAssignment(analysis)
        self.inner_bindings = _inner_bindings(def_use)
        self.rereads = _rereads(analysis)
        self.import_lines: dict[str, list[int]] = {}
        for d in def_use.definitions:
            if d.imports:
                self.import_lines.setdefault(d.var, []).append(d.line)


class _Cutter:
    """The candidate stages of one block and the cut that keeps the best."""

    def __init__(
        self,
        facts: _FunctionFacts,
        lmap: LanguageNodeMap,
        receiver: Receiver | None,
        block: Node,
        code: list[Node],
        pre: _Prefix,
        max_returns: int,
        loops: tuple[Node, ...] = (),
    ) -> None:
        self.lmap, self.receiver, self.block, self.code = lmap, receiver, block, code
        self.max_returns = max_returns
        self.def_lines, self.use_lines = facts.def_lines, facts.use_lines
        self.declared_first = facts.declared_first
        self.valueless = facts.valueless
        self.hoisted = facts.hoisted
        self.decl_lines = facts.decl_lines
        self.shared = facts.shared
        self.outer = facts.outer
        self.arm_writes = facts.arm_writes
        self.refused = facts.refused
        self.reads = facts.reads
        self.cfg = facts.analysis.cfg
        self.pre = pre
        self._assigns: dict[tuple[int, str], bool] = {}
        self.cut_cost = _cut_costs(code)
        self.banners = [c == 0 for c in self.cut_cost]
        self.timer_pairs = _timer_pairs(code)
        self.guards = [_guard_names(st, lmap) for st in code]
        self.assigned = facts.assigned
        self.inner_bindings = facts.inner_bindings
        self.rereads, self.import_lines = facts.rereads, facts.import_lines
        # In a loop body: a break / continue leaving a stage would leave the
        # helper instead, and a value carried to the next iteration must come
        # back out of the stage that writes it.
        self.escapes = [escapes(st, lmap) for st in code] if loops else [False] * len(code)
        self.resources = _scoped_resource_indexes(code, lmap)
        self.carried = carried(facts.analysis, loops, facts.def_lines) if loops else frozenset()

    def _sum(self, series: list[int], i: int, j: int) -> int:
        return series[j + 1] - series[i]

    def cut(self) -> tuple[Extraction, ...]:
        """The lowest-cost split of the block (module docstring, Cuts). Each
        run ``i..j`` is weighed once, by the prefix ending at ``j``."""
        n = len(self.code)
        cost: list[int] = [0] * (n + 1)
        back: list[tuple[int, Extraction | None]] = [(0, None)] * (n + 1)
        for k in range(1, n + 1):
            cost[k], back[k] = self._best_ending_at(k, cost)
        stages: list[Extraction] = []
        k = n
        while k > 0:
            k, stage = back[k]
            if stage is not None:
                stages.append(stage)
        return tuple(reversed(stages))

    def _best_ending_at(
        self, k: int, cost: list[int]
    ) -> tuple[int, tuple[int, Extraction | None]]:
        """The cheapest split of statements ``0..k-1``: the last one kept in
        the function, or the last stage ``i..k-1`` after the best split of
        ``0..i-1``."""
        pre = self.pre
        kept = self._sum(pre.decisions, k - 1, k - 1)
        best = cost[k - 1] + _KEPT_DECISION * kept + self._sum(pre.code, k - 1, k - 1)
        choice: tuple[int, Extraction | None] = (k - 1, None)
        for i in range(k - 1, -1, -1):
            if (
                self._sum(pre.decisions, i, k - 1) + 1 > STAGE_MAX_CCN
                or self._sum(pre.code, i, k - 1) > STAGE_MAX_NLOC
            ):
                break
            stage = self._stage(i, k - 1)
            if stage is None:
                continue
            c = cost[i] + _PER_STAGE + _PER_NAME * (len(stage.params) + len(stage.returns))
            c += 0 if i == 0 else self.cut_cost[i]
            c += _CROSSED_BANNER * sum(self.banners[i + 1 : k])
            if c < best:
                best, choice = c, (i, stage)
        return best, choice

    def _stage(self, i: int, j: int) -> Extraction | None:
        """Statements ``i..j`` as a stage, or None when they cannot be one
        (code no path reaches included: nothing is known to be bound there)."""
        if not self._shape_allows(i, j):
            return None
        span = self.code[i : j + 1]
        s, e = span[0].start_point[0] + 1, span[-1].end_point[0] + 1
        if any(s <= ln <= e for v in self.rereads for ln in self.def_lines.get(v, ())):
            return None
        params, returns = _infer_in_out(
            self.def_lines,
            self.use_lines,
            s,
            e,
            self.declared_first,
            reads=self.reads,
            valueless=self.valueless,
        )
        returns = self._own_bindings_dropped(span, s, e, returns)
        if self.carried:
            written = {v for v in self.carried if any(s <= ln <= e for ln in self.def_lines[v])}
            returns = tuple(sorted(set(returns) | written))
        params = self._with_inouts(i, j, s, params, returns)
        if params is None or len(returns) > self.max_returns:
            return None
        bound = self.assigned.before(s, e)
        if bound is None or not set(params) <= bound:
            return None
        if self._guard_needed_after(i, j, e) or not self._signature_holds(
            span, s, e, params, returns
        ):
            return None
        pre = self.pre
        uses, assigns = _receiver_facts(self.receiver, pre, i, j, 0)
        return Extraction(
            start_line=s,
            end_line=e,
            params=params,
            returns=returns,
            slice_nloc=self._sum(pre.code, i, j),
            ccn_removed=self._sum(pre.decisions, i, j),
            needs_async=self._sum(pre.awaits, i, j) > 0,
            uses_receiver=uses,
            receiver_assigns=assigns,
        )

    def _shape_allows(self, i: int, j: int) -> bool:
        """The checks prefix sums answer: size, no return / yield, no named
        nested function, no break / continue leaving the stage, and no timer
        started on one side of the cut and stopped on the other."""
        pre = self.pre
        if self._sum(pre.decisions, i, j) < _STAGE_MIN_DECISIONS:
            return False
        if self._sum(pre.code, i, j) < _MIN_SLICE_NLOC:
            return False
        if self._sum(pre.exits, i, j) or self._sum(pre.nested, i, j):
            return False
        if any(self.escapes[i : j + 1]):
            return False
        if _disposes_early(self.resources, i, j, pre.code):
            return False
        return not any((i <= a <= j) != (i <= b <= j) for a, b in self.timer_pairs)

    def _own_bindings_dropped(
        self, span: list[Node], s: int, e: int, returns: tuple[str, ...]
    ) -> tuple[str, ...]:
        """*returns* without the names the stage only declares inside a nested
        block (a loop's ``const``): those bindings end with the block."""
        top = [
            (st.start_point[0] + 1, st.end_point[0] + 1)
            for st in span
            if st.type in self.lmap.local_decl_kinds
        ]

        def scoped(var: str) -> bool:
            lines = [(ln, inner) for ln, inner in self.inner_bindings.get(var, ()) if s <= ln <= e]
            return bool(lines) and all(
                inner and not any(lo <= ln <= hi for lo, hi in top) for ln, inner in lines
            )

        return tuple(r for r in returns if not scoped(r))

    def _guard_needed_after(self, i: int, j: int, e: int) -> bool:
        """Whether a guard in statements ``i..j`` names a value read after *e*."""
        return any(
            any(ln > e for name in names for ln in self.use_lines.get(name, ()))
            for names in self.guards[i : j + 1]
            if names
        )

    def _signature_holds(
        self,
        span: list[Node],
        s: int,
        e: int,
        params: tuple[str, ...],
        returns: tuple[str, ...],
    ) -> bool:
        """The single-span slicer's own refusals: a hoisted binding, a
        declaration the code after the stage still needs, a local a closure
        shares across the stage's edge. Then those a stage names in
        ``refused``: a ``global`` write that cannot take its declaration
        along, a cut that changes exception flow (:mod:`raises`), a
        ``switch`` / ``match`` arm write the interface depends on."""
        if any(s <= first_def <= e and first_use < s for first_def, first_use in self.hoisted):
            return False
        if _declaration_escapes(
            s, e, returns, self.decl_lines, self.def_lines, self.use_lines, self.declared_first
        ):
            return False
        if self.shared is not None and _closure_state_crosses(
            self.shared, span, s, e, self.block, self.lmap
        ):
            return False
        reason = self._interface_refusal(s, e, params, returns)
        if reason is not None:
            self.refused.add(reason)
        return reason is None

    def _interface_refusal(
        self, s: int, e: int, params: tuple[str, ...], returns: tuple[str, ...]
    ) -> str | None:
        if _outer_write_escapes(s, e, self.outer, self.def_lines):
            return OUTER_WRITE
        reason = exception_flow_refusal(self.cfg, s, e, self.reads, self.def_lines)
        if reason is not None:
            return reason
        if self._arm_write_crosses(s, e, params, returns):
            return SWITCH_ARM_WRITE
        return None

    def _arm_write_crosses(
        self, s: int, e: int, params: tuple[str, ...], returns: tuple[str, ...]
    ) -> bool:
        """Whether a ``switch`` / ``match`` arm's write of a name live across
        the stage's edge can decide its interface. The CFG keeps the
        statement whole, so liveness takes the write as certain and hides
        the value from before it: a read after the arm in the stage misses an
        input, a stage before the arm misses an output, an input bound only
        by the arm looks bound. Temporary: remove once the CFG splits arms
        (#3451)."""
        taken = set(params)
        for lo, hi, names in self.arm_writes:
            for var in names:
                defs = self.def_lines.get(var, ())
                uses = self.use_lines.get(var, ())
                if s <= lo and hi <= e:
                    hazard = var not in taken and any(d < s for d in defs) and any(
                        hi < u <= e for u in uses
                    )
                elif hi < s:
                    hazard = var in taken and not any(d < lo for d in defs)
                else:
                    hazard = var not in returns and any(s <= d <= e for d in defs) and any(
                        u > hi for u in uses
                    )
                if hazard:
                    return True
        return False

    def _with_inouts(
        self, i: int, j: int, s: int, params: tuple[str, ...], returns: tuple[str, ...]
    ) -> tuple[str, ...] | None:
        """*params* plus each output some path does not write, which the stage
        must then take in to hand back; None when such an output has no value
        before the stage."""
        extra = []
        for var in returns:
            if var in params or any(self._writes(k, var) for k in range(i, j + 1)):
                continue
            if self._import_fallback(var, i, j):
                continue
            if not any(ln < s for ln in self.def_lines.get(var, ())):
                return None
            extra.append(var)
        return tuple(sorted((*params, *extra))) if extra else params

    def _import_fallback(self, var: str, i: int, j: int) -> bool:
        """Whether one of statements ``i..j`` is a ``try`` whose body imports
        *var* and whose every handler assigns it (``try: from m import x`` /
        ``except ImportError: x = None``): bound on every path the stage
        completes. A handler that only passes leaves it unbound."""
        for st in self.code[i : j + 1]:
            body = st.child_by_field_name("body") if st.type in self.lmap.try_kinds else None
            handlers = [c for c in st.children if c.type in self.lmap.catch_kinds]
            if body is None or not handlers or not _holds_line(body, self.import_lines.get(var, ())):
                continue
            if all(_holds_line(h, self.def_lines.get(var, ())) for h in handlers):
                return True
        return False

    def _writes(self, k: int, var: str) -> bool:
        """Whether statement *k* writes *var* on every path through it (the
        slicer's proof), once per statement and name: a run proves it when any
        of its statements does."""
        key = (k, var)
        if key not in self._assigns:
            defs = self.def_lines.get(var, [])
            self._assigns[key] = _stmt_assigns(self.code[k], var, defs, self.lmap)
        return self._assigns[key]


def _rereads(analysis: FunctionAnalysis) -> frozenset[str]:
    """Names a multi-line statement writes and also reads on a later line of
    itself (``msgs = [\\n m for m in msgs]``). Lines order its write before
    those reads, so neither the inputs nor the outputs of a stage writing one
    can be read off lines (a known limit of the def / use line model)."""
    out: set[str] = set()
    for block in analysis.cfg.blocks:
        bdu = analysis.def_use.block(block.id)
        if bdu is None:
            continue
        for st in block.statements:
            if st.is_head or st.end_line == st.start_line:
                continue
            for d in bdu.defs:
                if st.start_line <= d.line <= st.end_line and any(
                    u.name == d.var and d.line < u.line <= st.end_line and not u.echo
                    for u in bdu.uses
                ):
                    out.add(d.var)
    return frozenset(out)


def _arm_writes(
    analysis: FunctionAnalysis, lmap: LanguageNodeMap
) -> tuple[tuple[int, int, frozenset[str]], ...]:
    """Per ``switch`` / ``match`` statement the flow graph keeps whole, its
    lines and the names an arm writes without declaring them."""
    if not lmap.switch_kinds:
        return ()
    found = [
        (st.start_line, st.end_line)
        for block in analysis.cfg.blocks
        for st in block.statements
        if st.kind in lmap.switch_kinds
    ]
    out = []
    for lo, hi in found:
        names = frozenset(
            d.var for d in analysis.def_use.definitions if lo <= d.line <= hi and not d.declares
        )
        if names:
            out.append((lo, hi, names))
    return tuple(out)


def _holds_line(node: Node, lines: list[int]) -> bool:
    lo, hi = node.start_point[0] + 1, node.end_point[0] + 1
    return any(lo <= ln <= hi for ln in lines)


def _timer_pairs(code: list[Node]) -> list[tuple[int, int]]:
    """``(a, b)``: statement ``a`` starts a timer (``timings.start("x")``)
    that statement ``b`` stops, the first such at or after ``a``."""
    starts: list[set[str]] = []
    stops: list[set[str]] = []
    for st in code:
        text = (st.text or b"").decode("utf-8", "replace")
        starts.append(set(_TIMER_START.findall(text)))
        stops.append(set(_TIMER_STOP.findall(text)))
    pairs = []
    for a, labels in enumerate(starts):
        for label in labels:
            b = next((b for b in range(a, len(code)) if label in stops[b]), None)
            if b is not None:
                pairs.append((a, b))
    return pairs


def _cut_costs(code: list[Node]) -> list[int]:
    """What cutting before each statement costs: nothing under a banner
    comment or a ``timed()`` label, less after a blank line."""
    out = []
    for n, (st, comment) in enumerate(section_heads(code)):
        prev = code[n - 1] if n else None
        blank = prev is not None and st.start_point[0] - prev.end_point[0] > 1
        out.append(0 if comment else _BLANK_LINE_CUT if blank else _UNHINTED_CUT)
    return out


def _guard_names(st: Node, lmap: LanguageNodeMap) -> frozenset[str]:
    """The names a guard that raises tests (Python ``if not x: raise E``, TS /
    JS ``if (!x) throw e;``). Type checkers narrow ``x`` after it, which a
    helper holding it would undo."""
    condition = exiting_guard(st, lmap, lmap.raise_kinds)
    return frozenset(_identifiers(condition)) if condition is not None else frozenset()


def _inner_bindings(def_use: FunctionDefUse) -> dict[str, list[tuple[int, bool]]]:
    """Per name, each write's line and whether it opens a block-scoped
    binding (``let`` / ``const`` / a loop's declared variable)."""
    out: dict[str, list[tuple[int, bool]]] = {}
    for d in def_use.definitions:
        out.setdefault(d.var, []).append((d.line, d.declares))
    return out


def _context(
    stages: tuple[Extraction, ...], def_lines: dict[str, list[int]], receiver: Receiver | None
) -> tuple[str, ...]:
    """The values for one parameter object: read by two or more stages, never
    an output, not written again once the first stage starts, and not the
    receiver (a method reaches its instance as itself, not off an object)."""
    first = stages[0].start_line
    own = receiver.names if receiver is not None else frozenset()
    outs = {r for x in stages for r in x.returns}
    counts = Counter(p for x in stages for p in x.params)
    shared = sorted(
        var
        for var, n in counts.items()
        if n >= 2
        and var not in outs
        and var not in own
        and all(ln < first for ln in def_lines.get(var, ()))
    )
    return tuple(shared) if len(shared) > _CONTEXT_OVER else ()


def _inputs_held(stages: tuple[Extraction, ...], def_lines: dict[str, list[int]]) -> bool:
    """Whether the function holds every stage input at that stage's call:
    written outside every stage, or handed back by an earlier one. An input
    only a later stage hands back (or none) would be unbound at the call; a
    loop-carried one is also written before the loop, outside every stage."""

    def inside(line: int) -> bool:
        return any(x.start_line <= line <= x.end_line for x in stages)

    earlier: set[str] = set()
    for x in stages:
        for p in x.params:
            if p not in earlier and all(inside(ln) for ln in def_lines.get(p, ())):
                return False
        earlier.update(x.returns)
    return True


def _composes(
    analysis: FunctionAnalysis, stages: tuple[Extraction, ...], context: tuple[str, ...]
) -> tuple[tuple[tuple[str, ...], ...], frozenset[str]] | None:
    """Per stage, the function-local imports it reads from outside itself,
    and those no read left in the function needs; None when some read would
    no longer see the value it saw (module docstring, Composition), by the
    definitions each read observes."""
    starts = [x.start_line for x in stages]
    ins = [set(x.params) | set(context) for x in stages]
    outs = [set(x.returns) for x in stages]
    imported: list[set[str]] = [set() for _ in stages]
    kept: set[str] = set()

    def stage_at(line: int) -> int | None:
        k = bisect_right(starts, line) - 1
        return k if k >= 0 and line <= stages[k].end_line else None

    definitions = analysis.reaching.definitions
    for use, seen in observed_definitions(analysis.def_use, analysis.reaching):
        if use.echo:
            continue
        m = stage_at(use.line)
        for d in (definitions[i] for i in seen):
            k = stage_at(d.line)
            if d.imports:
                if m is None:
                    kept.add(use.name)
                elif k != m:
                    imported[m].add(use.name)
                continue
            if k == m:
                continue
            if (m is not None and use.name not in ins[m]) or (
                k is not None and use.name not in outs[k]
            ):
                return None
    moved = frozenset().union(*imported) - kept
    return tuple(tuple(sorted(names)) for names in imported), moved


__all__ = [
    "OUTER_WRITE",
    "STAGE_MAX_CCN",
    "STAGE_MAX_NLOC",
    "SWITCH_ARM_WRITE",
    "StagePlan",
    "find_stages",
]
