"""Staged Extract Method: split a brain method into helpers called in order.

One helper cannot take a function of CCN 70 under the bar: the slicer's best
span is a small share of it. This module cuts the function's *effective body*
into K contiguous stages, each a helper the function then calls in sequence.

- **Effective body.** A ``try`` / ``with`` statement holding most of a block's
  decision points is a wrapper: its body is cut instead, and the wrapper (with
  its ``except`` / ``finally`` and anything around it) stays in the function.
- **Stages.** Each is a run of that body's statements with ``CCN <= 10`` and
  ``NLOC <= 60``, at least two decision points, and no ``return`` / ``yield``
  (a ``raise`` propagates from a helper unchanged; the body is never inside a
  loop, so a ``break`` / ``continue`` cannot leave the stage). Its inputs and
  outputs come from the same line liveness as a single span; an output not
  written on every path is also passed in (``inout``), so a skipped write hands
  back the value it came with. Every input must be written on every path into
  the stage (:mod:`assigned`): one bound only under a condition would raise
  at the call. A name a stage declares in a nested block (a loop's ``const``)
  is the stage's own, never an output. A guard (``if not x: raise``) whose
  names the code after the stage reads stays in the function: type checkers
  narrow ``x`` from it.
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
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..complexity.nloc import is_string_stmt
from .assigned import DefiniteAssignment
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
    _function_lines,
    _hoisted_bindings,
    _identifiers,
    _infer_in_out,
    _Prefix,
    _reads_observing,
    _receiver_facts,
    _scan_for,
    _stmt_assigns,
    _unwrap_container,
    _var_lines,
)
from .span import is_comment, section_heads

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
# A wrapper holding this share of a block's decision points is unwrapped.
_WRAPPER_SHARE = 0.6

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
    ``reason`` says why there are no stages; None when there are."""

    stages: tuple[Extraction, ...] = ()
    context: tuple[str, ...] = ()
    imports: tuple[tuple[str, ...], ...] = ()
    moved_imports: frozenset[str] = frozenset()
    reason: str | None = None

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
    block = _effective_block(_unwrap_container(body, lmap.block_kinds), blocks, lmap)
    code, pre = blocks.code(block)
    if len(code) < 2:
        return StagePlan(reason="one_statement")
    cutter = _Cutter(analysis, lmap, receiver, block, code, pre, max_returns)
    stages = cutter.cut()
    if len(stages) < 2:
        return StagePlan(reason="fewer_than_two_stages")
    context = _context(stages, cutter.def_lines, receiver)
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


def _effective_block(block: Node, blocks: _Blocks, lmap: LanguageNodeMap) -> Node:
    """*block*, or the body of the ``try`` / ``with`` wrapper holding most of
    its decision points, repeatedly."""
    wrappers = lmap.try_kinds | lmap.with_kinds
    while True:
        code, pre = blocks.code(block)
        per = [pre.decisions[k + 1] - pre.decisions[k] for k in range(len(code))]
        total = sum(per)
        if not total:
            return block
        top = code[max(range(len(code)), key=per.__getitem__)]
        inner = top.child_by_field_name("body") if top.type in wrappers else None
        if inner is None:
            return block
        inner = _unwrap_container(inner, lmap.block_kinds)
        if blocks.code(inner)[1].decisions[-1] < _WRAPPER_SHARE * total:
            return block
        block = inner


class _Cutter:
    """The candidate stages of one block and the cut that keeps the best."""

    def __init__(
        self,
        analysis: FunctionAnalysis,
        lmap: LanguageNodeMap,
        receiver: Receiver | None,
        block: Node,
        code: list[Node],
        pre: _Prefix,
        max_returns: int,
    ) -> None:
        self.lmap, self.receiver, self.block, self.code = lmap, receiver, block, code
        self.max_returns = max_returns
        def_use = analysis.def_use
        self.def_lines, self.use_lines = _var_lines(def_use)
        self.declared_first = _declared_before_read(def_use)
        self.hoisted = _hoisted_bindings(self.def_lines, self.use_lines)
        self.decl_lines = _declaration_lines(def_use)
        self.shared = _closure_state(def_use, self.def_lines, self.use_lines)
        self.reads = _reads_observing(analysis, self.def_lines)
        self.pre = pre
        self._assigns: dict[tuple[int, str], bool] = {}
        self.cut_cost = _cut_costs(code)
        self.banners = [c == 0 for c in self.cut_cost]
        self.timer_pairs = _timer_pairs(code)
        self.guards = [_guard_names(st, lmap) for st in code]
        self.assigned = DefiniteAssignment(analysis)
        self.inner_bindings = _inner_bindings(def_use)

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
        params, returns = _infer_in_out(
            self.def_lines, self.use_lines, s, e, self.declared_first, reads=self.reads
        )
        returns = self._own_bindings_dropped(span, s, e, returns)
        params = self._with_inouts(i, j, s, params, returns)
        if params is None or len(returns) > self.max_returns:
            return None
        bound = self.assigned.before(s, e)
        if bound is None or not set(params) <= bound:
            return None
        if not self._signature_holds(span, s, e, returns) or self._guard_needed_after(i, j, e):
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
        nested function, and no timer started on one side of the cut and
        stopped on the other."""
        pre = self.pre
        if self._sum(pre.decisions, i, j) < _STAGE_MIN_DECISIONS:
            return False
        if self._sum(pre.code, i, j) < _MIN_SLICE_NLOC:
            return False
        if self._sum(pre.exits, i, j) or self._sum(pre.nested, i, j):
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

    def _signature_holds(self, span: list[Node], s: int, e: int, returns: tuple[str, ...]) -> bool:
        """The single-span slicer's own refusals: a hoisted binding, a
        declaration the code after the stage still needs, a local a closure
        shares across the stage's edge."""
        if any(s <= first_def <= e and first_use < s for first_def, first_use in self.hoisted):
            return False
        if _declaration_escapes(
            s, e, returns, self.decl_lines, self.def_lines, self.use_lines, self.declared_first
        ):
            return False
        return self.shared is None or not _closure_state_crosses(
            self.shared, span, s, e, self.block, self.lmap
        )

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
            if not any(ln < s for ln in self.def_lines.get(var, ())):
                return None
            extra.append(var)
        return tuple(sorted((*params, *extra))) if extra else params


    def _writes(self, k: int, var: str) -> bool:
        """Whether statement *k* writes *var* on every path through it (the
        slicer's proof), once per statement and name: a run proves it when any
        of its statements does."""
        key = (k, var)
        if key not in self._assigns:
            defs = self.def_lines.get(var, [])
            self._assigns[key] = _stmt_assigns(self.code[k], var, defs, self.lmap)
        return self._assigns[key]


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
    """The names a guard statement tests: an ``if`` with no else arm whose
    last statement is itself a raise (Python ``if not x: raise E``) or throw
    (TS / JS ``if (!x) throw e;`` or ``{ ...; throw e; }``). Type checkers
    narrow ``x`` after such a guard, which a helper holding it would undo.
    Empty for anything else, a raise nested deeper included."""
    if st.type not in lmap.if_kinds or st.child_by_field_name("alternative") is not None:
        return frozenset()
    if any(c.type in ("else_clause", "elif_clause") for c in st.children):
        return frozenset()
    body = st.child_by_field_name("consequence")
    if body is None:
        return frozenset()
    stmts = [c for c in body.named_children if not is_comment(c)]
    # ``if (!x) throw e;`` has the throw itself as its consequence.
    last = body if body.type in lmap.raise_kinds or not stmts else stmts[-1]
    if last.type not in lmap.raise_kinds:
        return frozenset()
    condition = st.child_by_field_name("condition")
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


__all__ = ["STAGE_MAX_CCN", "STAGE_MAX_NLOC", "StagePlan", "find_stages"]
