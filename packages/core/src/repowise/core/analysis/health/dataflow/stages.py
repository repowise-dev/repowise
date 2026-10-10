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
  back the value it came with.
- **Cuts.** A small dynamic program over the statement boundaries: lift as many
  decision points as possible, then prefer narrow interfaces (fewest names
  crossing a cut), cuts at a banner comment or a ``timed()`` label, and fewer
  stages. Deterministic: ties keep the earliest cut.
- **Context.** When more than five values are read by two or more stages and
  never rebound once the first stage starts, they travel on one parameter
  object instead of as separate parameters.
- **Composition.** Every read's reaching definitions are checked against the
  plan: a value defined outside a stage and read inside it must be one of its
  inputs, and one defined in a stage and read outside it one of its outputs.
  A plan that fails is refused whole.

Function-local imports are not inputs: a helper re-imports a name.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..complexity.nloc import is_string_stmt
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
    _infer_in_out,
    _outs_definitely_assigned,
    _receiver_facts,
    _scan_for,
    _span_kinds,
    _span_metrics,
    _unwrap_container,
    _var_lines,
)
from .span import is_comment, section_heads

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap
    from .analyze import FunctionAnalysis
    from .defuse import Definition
    from .dialects.base import Occurrence, Receiver
    from .slice import _Prefix, _Scan

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


@dataclass(frozen=True)
class StagePlan:
    """The stages in source order, the names carried on the parameter
    object (empty: plain parameters), and per stage the names it reads that
    the function imports outside it, which the helper must import itself.
    ``reason`` says why there are no stages; None when there are."""

    stages: tuple[Extraction, ...] = ()
    context: tuple[str, ...] = ()
    imports: tuple[tuple[str, ...], ...] = ()
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
) -> StagePlan:
    """The staged split of *analysis*, or a :class:`StagePlan` with only a
    ``reason`` when none composes. *max_returns* caps a stage's outputs (a
    language whose call cannot unpack a tuple passes 1)."""
    fn_node = analysis.fn_node
    if fn_node is None or fn_node.has_error:
        return StagePlan(reason="no_tree")
    body = fn_node.child_by_field_name("body")
    if body is None:
        return StagePlan(reason="no_tree")
    scan = _scan_for(lmap, receiver, fn_node)
    # Only an exit from the function, or a yield, cannot cross into a helper.
    stage_scan = scan._replace(jumps=lmap.return_kinds | lmap.yield_kinds, kinds=None)
    stage_scan = stage_scan._replace(kinds=_span_kinds(stage_scan))
    block = _effective_block(_unwrap_container(body, lmap.block_kinds), stage_scan, lmap)
    code = _code(block)
    if len(code) < 2:
        return StagePlan(reason="one_statement")
    cutter = _Cutter(analysis, lmap, receiver, block, code, stage_scan, max_returns)
    stages = cutter.cut()
    if len(stages) < 2:
        return StagePlan(reason="fewer_than_two_stages")
    context = _context(stages, cutter.def_lines)
    imports = _composes(analysis, stages, context)
    if imports is None:
        return StagePlan(reason="composition")
    return StagePlan(stages=stages, context=context, imports=imports)


def _code(block: Node) -> list[Node]:
    """The block's statements, without comments or a leading docstring."""
    out = [c for c in block.named_children if not is_comment(c)]
    if out and is_string_stmt(out[0]):
        out = out[1:]
    return out


def _effective_block(block: Node, scan: _Scan, lmap: LanguageNodeMap) -> Node:
    """*block*, or the body of the ``try`` / ``with`` wrapper holding most of
    its decision points, repeatedly."""
    wrappers = lmap.try_kinds | lmap.with_kinds
    while True:
        code = _code(block)
        per = [_span_metrics([st], scan).decisions for st in code]
        total = sum(per)
        if not total:
            return block
        top = code[max(range(len(code)), key=per.__getitem__)]
        inner = top.child_by_field_name("body") if top.type in wrappers else None
        if inner is None:
            return block
        inner = _unwrap_container(inner, lmap.block_kinds)
        if _span_metrics(_code(inner), scan).decisions < _WRAPPER_SHARE * total:
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
        scan: _Scan,
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
        self.pre: _Prefix = _block_prefix(code, scan, _function_lines(analysis.fn_node), lmap)
        self.hints = [bool(comment) for _st, comment in section_heads(code)]

    def _sum(self, series: list[int], i: int, j: int) -> int:
        return series[j + 1] - series[i]

    def cut(self) -> tuple[Extraction, ...]:
        """The lowest-cost split of the block (module docstring, Cuts)."""
        n, pre = len(self.code), self.pre
        cost: list[int] = [0] + [0] * n
        back: list[tuple[int, Extraction | None]] = [(0, None)] * (n + 1)
        for k in range(1, n + 1):
            kept = self._sum(pre.decisions, k - 1, k - 1)
            cost[k] = cost[k - 1] + _KEPT_DECISION * kept + self._sum(pre.code, k - 1, k - 1)
            back[k] = (k - 1, None)
            for i in range(k - 1, -1, -1):
                if (
                    self._sum(pre.decisions, i, k - 1) + 1 > STAGE_MAX_CCN
                    or self._sum(pre.code, i, k - 1) > STAGE_MAX_NLOC
                ):
                    break
                stage = self._stage(i, k - 1)
                if stage is None:
                    continue
                width = len(stage.params) + len(stage.returns)
                c = cost[i] + _PER_STAGE + _PER_NAME * width
                c += 0 if self.hints[i] or i == 0 else _UNHINTED_CUT
                if c < cost[k]:
                    cost[k], back[k] = c, (i, stage)
        stages: list[Extraction] = []
        k = n
        while k > 0:
            k, stage = back[k]
            if stage is not None:
                stages.append(stage)
        return tuple(reversed(stages))

    def _stage(self, i: int, j: int) -> Extraction | None:
        """Statements ``i..j`` as a stage, or None when they cannot be one."""
        pre = self.pre
        decisions = self._sum(pre.decisions, i, j)
        nloc = self._sum(pre.code, i, j)
        if decisions < _STAGE_MIN_DECISIONS or nloc < _MIN_SLICE_NLOC:
            return None
        if self._sum(pre.jumps, i, j) or self._sum(pre.nested, i, j):
            return None
        span = self.code[i : j + 1]
        s, e = span[0].start_point[0] + 1, span[-1].end_point[0] + 1
        params, returns = _infer_in_out(self.def_lines, self.use_lines, s, e, self.declared_first)
        params = self._with_inouts(span, s, params, returns)
        if params is None or len(returns) > self.max_returns:
            return None
        if any(s <= first_def <= e and first_use < s for first_def, first_use in self.hoisted):
            return None
        if _declaration_escapes(
            s, e, returns, self.decl_lines, self.def_lines, self.use_lines, self.declared_first
        ):
            return None
        if self.shared is not None and _closure_state_crosses(
            self.shared, span, s, e, self.block, self.lmap
        ):
            return None
        uses, assigns = _receiver_facts(self.receiver, pre, i, j, 0)
        return Extraction(
            start_line=s,
            end_line=e,
            params=params,
            returns=returns,
            slice_nloc=nloc,
            ccn_removed=decisions,
            needs_async=self._sum(pre.awaits, i, j) > 0,
            uses_receiver=uses,
            receiver_assigns=assigns,
        )

    def _with_inouts(
        self, span: list[Node], s: int, params: tuple[str, ...], returns: tuple[str, ...]
    ) -> tuple[str, ...] | None:
        """*params* plus each output some path does not write, which the stage
        must then take in to hand back; None when such an output has no value
        before the stage."""
        extra = []
        for var in returns:
            if var in params or _outs_definitely_assigned(span, (var,), self.def_lines, self.lmap):
                continue
            if not any(ln < s for ln in self.def_lines.get(var, ())):
                return None
            extra.append(var)
        return tuple(sorted((*params, *extra))) if extra else params


def _context(stages: tuple[Extraction, ...], def_lines: dict[str, list[int]]) -> tuple[str, ...]:
    """The values for one parameter object: read by two or more stages, never
    an output, and not written again once the first stage starts."""
    first = stages[0].start_line
    outs = {r for x in stages for r in x.returns}
    counts = Counter(p for x in stages for p in x.params)
    shared = sorted(
        var
        for var, n in counts.items()
        if n >= 2 and var not in outs and all(ln < first for ln in def_lines.get(var, ()))
    )
    return tuple(shared) if len(shared) > _CONTEXT_OVER else ()


def _composes(
    analysis: FunctionAnalysis, stages: tuple[Extraction, ...], context: tuple[str, ...]
) -> tuple[tuple[str, ...], ...] | None:
    """Per stage, the function-local imports it reads from outside itself,
    or None when some read would no longer see the value it saw (module
    docstring, Composition), by the reaching definitions of each read."""
    starts = [x.start_line for x in stages]
    ins = [set(x.params) | set(context) for x in stages]
    outs = [set(x.returns) for x in stages]
    imported: list[set[str]] = [set() for _ in stages]

    def stage_at(line: int) -> int | None:
        k = bisect_right(starts, line) - 1
        return k if k >= 0 and line <= stages[k].end_line else None

    reaching, def_use = analysis.reaching, analysis.def_use
    for block_id, bdu in def_use.blocks.items():
        entering = reaching.in_sets.get(block_id, frozenset())
        order = _statement_order(analysis, block_id)
        for use in bdu.uses:
            if use.echo:
                continue
            m = stage_at(use.line)
            for d in _reaching_defs(use, order, entering, bdu.defs, def_use.definitions):
                k = stage_at(d.line)
                if k == m:
                    continue
                if d.imports:
                    if m is not None:
                        imported[m].add(use.name)
                    continue
                if (m is not None and use.name not in ins[m]) or (
                    k is not None and use.name not in outs[k]
                ):
                    return None
    return tuple(tuple(sorted(names)) for names in imported)


def _statement_order(analysis: FunctionAnalysis, block_id: int) -> Callable[[int], float]:
    """A line's statement position in the CFG block: the innermost statement
    holding it, so two lines of one multi-line statement share a position."""
    try:
        ranges = [(s.start_line, s.end_line) for s in analysis.cfg.block(block_id).statements]
    except (KeyError, IndexError):
        ranges = []

    def position(line: int) -> float:
        holding = [(hi - lo, n) for n, (lo, hi) in enumerate(ranges) if lo <= line <= hi]
        if holding:
            return min(holding)[1]
        # Not on a statement line: after every statement that starts above it.
        return sum(lo <= line for lo, _hi in ranges) - 0.5

    return position


def _reaching_defs(
    use: Occurrence,
    order: Callable[[int], float],
    entering: frozenset[int],
    block_defs: list[Definition],
    definitions: list[Definition],
) -> list[Definition]:
    """The definitions of the read's name reaching it inside its block: the
    block's last write in an earlier statement, else those entering the
    block. A write in the read's own statement comes after the read
    (``x += 1``, ``x = f(
 x)``)."""
    at = order(use.line)
    local = [d for d in block_defs if d.var == use.name and order(d.line) < at]
    if local:
        return [local[-1]]
    return [definitions[i] for i in sorted(entering) if definitions[i].var == use.name]


__all__ = ["STAGE_MAX_CCN", "STAGE_MAX_NLOC", "StagePlan", "find_stages"]
