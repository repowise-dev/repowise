"""Reaching-definitions fixpoint over a function CFG.

A classic forward, may-analysis worklist dataflow, fully language-agnostic: it
needs only the CFG shape (:mod:`cfg`) and the per-block def facts
(:mod:`defuse`). For each program point it computes which definitions (write
sites) may reach it along some path.

Per block ``b`` with predecessors ``pred(b)``::

    GEN[b]  = the last definition of each variable written in b
    KILL[b] = every definition of any variable b writes, except GEN[b]
    IN[b]   = union over p in pred(b) of OUT[p]
    OUT[b]  = GEN[b] union (IN[b] minus KILL[b])

Definition sets are integer sets keyed by :attr:`defuse.Definition.index`, so
the lattice is finite and the worklist always converges in a bounded number of
rounds. A convergence guard caps the iterations anyway and reports
``converged=False`` if it is ever tripped, so a pathological input degrades to
silence (the caller drops the result) rather than spinning.

The result is deterministic: identical CFG + def facts yield identical IN/OUT
sets regardless of worklist scheduling.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cfg import CFG
    from .defuse import Definition, FunctionDefUse


@dataclass
class ReachingDefinitions:
    """IN / OUT reaching-definition sets per block (def indices).

    ``definitions`` is the function's write sites (index-aligned), so a def
    index can be resolved back to its variable and line. ``converged`` is False
    only when the iteration guard tripped, in which case the sets are partial
    and the caller should treat the function as having no signal.
    """

    in_sets: dict[int, frozenset[int]]
    out_sets: dict[int, frozenset[int]]
    definitions: list[Definition]
    converged: bool = True

    def reaching_in(self, block_id: int) -> list[Definition]:
        """Definitions reaching the *start* of *block_id*, ordered by index."""
        idx = self.in_sets.get(block_id, frozenset())
        return [self.definitions[i] for i in sorted(idx)]

    def reaching_out(self, block_id: int) -> list[Definition]:
        """Definitions reaching the *end* of *block_id*, ordered by index."""
        idx = self.out_sets.get(block_id, frozenset())
        return [self.definitions[i] for i in sorted(idx)]


def compute_reaching(
    cfg: CFG,
    fdu: FunctionDefUse,
    *,
    max_iterations: int | None = None,
) -> ReachingDefinitions:
    """Run the reaching-definitions fixpoint over *cfg* with *fdu*'s def facts."""
    # All definitions of each variable -> the KILL sets.
    defs_by_var: dict[str, set[int]] = {}
    for d in fdu.definitions:
        defs_by_var.setdefault(d.var, set()).add(d.index)

    gen: dict[int, frozenset[int]] = {}
    kill: dict[int, frozenset[int]] = {}
    for block in cfg.blocks:
        bdu = fdu.block(block.id)
        block_defs = bdu.defs if bdu is not None else []
        # GEN: the last definition of each variable written in this block.
        last_per_var: dict[str, int] = {}
        for d in block_defs:
            last_per_var[d.var] = d.index
        gen_b = frozenset(last_per_var.values())
        killed: set[int] = set()
        for var in last_per_var:
            killed |= defs_by_var.get(var, set())
        gen[block.id] = gen_b
        kill[block.id] = frozenset(killed) - gen_b

    in_sets: dict[int, frozenset[int]] = {b.id: frozenset() for b in cfg.blocks}
    out_sets: dict[int, frozenset[int]] = {b.id: gen[b.id] for b in cfg.blocks}

    # A finite may-analysis converges in O(blocks) rounds; the cap is generous
    # and only a pathological CFG could trip it (-> degrade to silence).
    cap = max_iterations if max_iterations is not None else (len(cfg.blocks) + 2) ** 2 + 16
    worklist: deque[int] = deque(b.id for b in cfg.blocks)
    queued = {b.id for b in cfg.blocks}
    iterations = 0
    converged = True

    while worklist:
        if iterations > cap:
            converged = False
            break
        iterations += 1
        bid = worklist.popleft()
        queued.discard(bid)
        block = cfg.block(bid)

        in_b: frozenset[int] = frozenset()
        for pred in block.predecessors:
            in_b |= out_sets[pred]
        in_sets[bid] = in_b

        out_b = gen[bid] | (in_b - kill[bid])
        if out_b != out_sets[bid]:
            out_sets[bid] = out_b
            for succ in block.successors:
                if succ not in queued:
                    worklist.append(succ)
                    queued.add(succ)

    return ReachingDefinitions(
        in_sets=in_sets,
        out_sets=out_sets,
        definitions=fdu.definitions,
        converged=converged,
    )
