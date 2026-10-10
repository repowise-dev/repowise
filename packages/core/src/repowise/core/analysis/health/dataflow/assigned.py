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
    from_try_source: bool = False,
) -> dict[int, frozenset[str]]:
    """Per block of *blocks*, the names written on every path from
    *start_id* (which begins with *seed*) to its entry. Only predecessors in
    *blocks* that *edge* accepts count.

    Solved as a round fixpoint rather than one sweep in block-id
    order: id order is *not* a topological order (``_build_if`` allocates the
    join before either arm), so one ordered pass reached joins before their
    arms. A handler is entered by an exception that may have escaped any
    statement of the protected region, including its first, so it inherits
    what was defined *before* the region, not after it: each predecessor's
    entry state. With *from_try_source*, the region's source block (the
    handler's lowest-id predecessor: the CFG creates a ``try`` body's blocks
    after the block it starts from) gives its exit state instead, which is
    exactly the state before the region. A block no counted predecessor
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
    preds = _preds(cfg, members, edge, from_try_source)
    defined_in: dict[int, frozenset[str]] = dict.fromkeys(members, frozenset())
    defined_out: dict[int, frozenset[str]] = dict.fromkeys(members, universe)
    defined_out[start_id] = seed | written[start_id]
    ordered = sorted(members)
    # Descending a lattice of height |universe| over |blocks| cells, so this is
    # a generous ceiling on a monotone iteration, not a tuning parameter.
    for _round in range((len(universe) + 1) * len(ordered) + 1):
        changed = False
        for bid in ordered:
            din = seed if bid == start_id else _block_in(preds[bid], defined_in, defined_out)
            dout = din | written[bid]
            if din != defined_in[bid] or dout != defined_out[bid]:
                defined_in[bid], defined_out[bid] = din, dout
                changed = True
        if not changed:
            return defined_in
    return dict.fromkeys(members, frozenset())


def _preds(
    cfg: CFG,
    members: frozenset[int],
    edge: Callable[[int, int], bool] | None,
    from_try_source: bool,
) -> dict[int, tuple[tuple[int, bool], ...]]:
    """Per block, its counted predecessors, each with whether its exit state
    (True) or its entry state (False) flows in: a handler takes entry states,
    except from the region's source block under *from_try_source*."""
    out: dict[int, tuple[tuple[int, bool], ...]] = {}
    for bid in members:
        found = [
            p for p in cfg.block(bid).predecessors if p in members and (edge is None or edge(p, bid))
        ]
        handler = cfg.block(bid).kind == "handler"
        source = min(found) if handler and from_try_source and found else None
        out[bid] = tuple((p, not handler or p == source) for p in found)
    return out


def _block_in(
    preds: tuple[tuple[int, bool], ...],
    defined_in: dict[int, frozenset[str]],
    defined_out: dict[int, frozenset[str]],
) -> frozenset[str]:
    """The intersection over *preds* of the states that flow in; nothing for
    a block no counted predecessor reaches."""
    states = [defined_out[p] if exit_state else defined_in[p] for p, exit_state in preds]
    return frozenset.intersection(*states) if states else frozenset()


class DefiniteAssignment:
    """:func:`must_defined_in` over a whole function from its entry (seeded
    with its parameters), asked at many lines. Solved on the first question,
    so a function no candidate asks about costs nothing."""

    def __init__(self, analysis: FunctionAnalysis) -> None:
        self._analysis = analysis
        self._in: dict[int, frozenset[str]] | None = None
        self._lines: list[int] = []
        self._blocks: list[int] = []

    def _solve(self) -> dict[int, frozenset[str]]:
        cfg, def_use = self._analysis.cfg, self._analysis.def_use
        # Only reachable blocks: one no path enters (code after a ``return``)
        # carries nothing and would empty every join it feeds.
        reachable = cfg.reachable_ids()
        heads = sorted(
            (st.start_line, b.id) for b in cfg.blocks if b.id in reachable for st in b.statements
        )
        self._lines = [line for line, _ in heads]
        self._blocks = [bid for _, bid in heads]
        return must_defined_in(
            cfg,
            def_use,
            reachable,
            cfg.entry_id,
            seed=frozenset(p.name for p in def_use.params),
            from_try_source=True,
        )

    def before(self, line: int, end: int | None = None) -> frozenset[str] | None:
        """The names written on every path to the first reachable statement at
        or after *line*; None when none follows, or none starts by *end* (the
        lines are code no path reaches)."""
        if self._in is None:
            self._in = self._solve()
        k = bisect_left(self._lines, line)
        if k == len(self._lines) or (end is not None and self._lines[k] > end):
            return None
        start, bid = self._lines[k], self._blocks[k]
        bdu = self._analysis.def_use.block(bid)
        earlier = frozenset(d.var for d in (bdu.defs if bdu else ()) if d.line < start)
        return self._in[bid] | earlier


__all__ = ["DefiniteAssignment", "must_defined_in"]
