"""Definite assignment: the names written on every path to a point.

Reaching definitions (:mod:`reaching`) is a *may* analysis: a read sees a
write if some path carries it there. Two consumers need the opposite, *must*:
the perf pass, to prove a read in a loop cannot see a previous iteration's
write, and a staged Extract Method plan, whose helper parameter must be bound
on every path to the call (a name bound only under ``if outline is not None:``
raises at a call the original code, reading it under the same condition,
never made).

Forward, intersection over predecessors (:func:`must_defined_in`)::

    IN[start] = the seed;  IN[b] = intersection over p in pred(b) of OUT[p]
    OUT[b]    = IN[b] union names written in b
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Collection
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .analyze import FunctionAnalysis
    from .cfg import CFG
    from .defuse import FunctionDefUse


def must_defined_in(
    cfg: CFG,
    def_use: FunctionDefUse,
    blocks: Collection[int],
    start_id: int,
    *,
    seed: frozenset[str] = frozenset(),
    edge: Callable[[int, int], bool] | None = None,
    region_start: Callable[[int], int | None] | None = None,
) -> dict[int, frozenset[str]]:
    """Per block of *blocks*, the names written on every path from
    *start_id* (which begins with *seed*) to its entry. Only predecessors in
    *blocks* that *edge* accepts count.

    Solved as a round fixpoint rather than one sweep in block-id
    order: id order is *not* a topological order (``_build_if`` allocates the
    join before either arm), so one ordered pass reached joins before their
    arms. A handler is entered by an exception that may have escaped any
    statement of the protected region, including its first, so it inherits
    what was defined *before* the region, not after it: its predecessor's
    entry state, plus that block's writes above the region's first line when
    *region_start* names it for the handler. A block no counted predecessor
    reaches claims nothing.

    The lattice descends from "everything", so a partial state is larger than
    the answer; if it somehow does not converge inside the bound, nothing is
    reported defined, which can only refuse.
    """
    members = frozenset(blocks)
    written: dict[int, frozenset[str]] = {}
    for bid in members:
        bdu = def_use.block(bid)
        written[bid] = frozenset(d.var for d in bdu.defs) if bdu is not None else frozenset()
    universe: frozenset[str] = seed.union(*written.values())
    if not universe:
        return dict.fromkeys(members, frozenset())
    preds = {
        bid: tuple(
            p
            for p in cfg.block(bid).predecessors
            if p in members and (edge is None or edge(p, bid))
        )
        for bid in members
    }
    starts = {bid: region_start(bid) if region_start else None for bid in members}
    above = {
        (p, bid): frozenset(d.var for d in def_use.block(p).defs if d.line < starts[bid])
        for bid in members
        if starts[bid] is not None
        for p in preds[bid]
        if def_use.block(p) is not None
    }
    defined_in: dict[int, frozenset[str]] = dict.fromkeys(members, frozenset())
    defined_out: dict[int, frozenset[str]] = dict.fromkeys(members, universe)
    defined_out[start_id] = seed | written[start_id]
    ordered = sorted(members)
    # Descending a lattice of height |universe| over |blocks| cells, so this is
    # a generous ceiling on a monotone iteration, not a tuning parameter.
    for _round in range((len(universe) + 1) * len(ordered) + 1):
        changed = False
        for bid in ordered:
            if bid == start_id:
                din = seed
            else:
                entry_state = cfg.block(bid).kind == "handler"
                states = [
                    defined_in[p] | above.get((p, bid), frozenset())
                    if entry_state
                    else defined_out[p]
                    for p in preds[bid]
                ]
                din = frozenset.intersection(*states) if states else frozenset()
            dout = din | written[bid]
            if din != defined_in[bid] or dout != defined_out[bid]:
                defined_in[bid], defined_out[bid] = din, dout
                changed = True
        if not changed:
            return defined_in
    return dict.fromkeys(members, frozenset())


class DefiniteAssignment:
    """:func:`must_defined_in` over a whole function from its entry (seeded
    with its parameters), asked at many lines."""

    def __init__(self, analysis: FunctionAnalysis) -> None:
        cfg, def_use = analysis.cfg, analysis.def_use
        self._def_use = def_use
        # Only reachable blocks: one no path enters (code after a ``return``)
        # carries nothing and would empty every join it feeds.
        reachable = cfg.reachable_ids()
        self._in = must_defined_in(
            cfg,
            def_use,
            reachable,
            cfg.entry_id,
            seed=frozenset(p.name for p in def_use.params),
            region_start=_try_starts(analysis),
        )
        heads = sorted(
            (st.start_line, b.id) for b in cfg.blocks if b.id in reachable for st in b.statements
        )
        self._lines = [line for line, _ in heads]
        self._blocks = [bid for _, bid in heads]

    def before(self, line: int) -> frozenset[str] | None:
        """The names written on every path to the first recorded statement at
        or after *line*; None when no statement follows."""
        k = bisect_left(self._lines, line)
        if k == len(self._lines):
            return None
        start, bid = self._lines[k], self._blocks[k]
        bdu = self._def_use.block(bid)
        earlier = frozenset(d.var for d in (bdu.defs if bdu else ()) if d.line < start)
        return self._in[bid] | earlier


def _try_starts(analysis: FunctionAnalysis) -> Callable[[int], int | None]:
    """For a handler block, the first line of the innermost ``try`` holding it."""
    tries: list[tuple[int, int]] = []
    stack = [analysis.fn_node] if analysis.fn_node is not None else []
    while stack:
        node = stack.pop()
        if "try" in node.type:
            tries.append((node.start_point[0] + 1, node.end_point[0] + 1))
        stack.extend(node.children)
    cfg = analysis.cfg

    def start(bid: int) -> int | None:
        block = cfg.block(bid)
        if block.kind != "handler" or not block.statements:
            return None
        line = block.statements[0].start_line
        holding = [(hi - lo, lo) for lo, hi in tries if lo < line <= hi]
        return min(holding)[1] if holding else None

    return start


__all__ = ["DefiniteAssignment", "must_defined_in"]
