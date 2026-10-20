"""Reaching-definitions fixpoint over a function CFG.

A classic forward, may-analysis worklist dataflow, fully language-agnostic: it
needs only the CFG shape (:mod:`cfg`) and the per-block def facts
(:mod:`defuse`). For each program point it computes which definitions (write
sites) may reach it along some path.

Per block ``b`` with predecessors ``pred(b)``::

    GEN[b]  = each variable's definitions in b from its last certain one on
    KILL[b] = every definition of any variable b certainly writes, except GEN[b]
    IN[b]   = union over p in pred(b) of OUT[p]
              (IN[p] union every def in p, on a raise edge p -> b)
    OUT[b]  = GEN[b] union (IN[b] minus KILL[b])

A may-def (:attr:`defuse.Definition.may`, a write inside a ``switch`` arm the
CFG keeps as one statement) adds itself to GEN and kills nothing: the value
from before it is still the one a read sees on the paths that skip the arm.

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
    from collections.abc import Iterable, Iterator

    from .cfg import CFG
    from .defuse import Definition, FunctionDefUse
    from .dialects.base import Occurrence


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


def _block_facts(
    cfg: CFG, fdu: FunctionDefUse
) -> tuple[dict[int, frozenset[int]], dict[int, frozenset[int]], dict[int, frozenset[int]]]:
    """GEN, KILL and every definition written, per block."""
    # All definitions of each variable -> the KILL sets.
    defs_by_var: dict[str, set[int]] = {}
    for d in fdu.definitions:
        defs_by_var.setdefault(d.var, set()).add(d.index)

    gen: dict[int, frozenset[int]] = {}
    kill: dict[int, frozenset[int]] = {}
    every: dict[int, frozenset[int]] = {}
    for block in cfg.blocks:
        bdu = fdu.block(block.id)
        block_defs = bdu.defs if bdu is not None else []
        # GEN: each variable's definitions from its last certain one on. A
        # may-def (``Definition.may``) adds itself and hides nothing, so a
        # block kills a variable's other definitions only when it holds a
        # certain write of it.
        open_per_var: dict[str, list[int]] = {}
        certain: set[str] = set()
        for d in block_defs:
            if d.may:
                open_per_var.setdefault(d.var, []).append(d.index)
            else:
                open_per_var[d.var] = [d.index]
                certain.add(d.var)
        gen_b = frozenset(i for indices in open_per_var.values() for i in indices)
        killed: set[int] = set()
        for var in certain:
            killed |= defs_by_var.get(var, set())
        gen[block.id] = gen_b
        every[block.id] = frozenset(d.index for d in block_defs)
        kill[block.id] = frozenset(killed) - gen_b
    return gen, kill, every


def compute_reaching(
    cfg: CFG,
    fdu: FunctionDefUse,
    *,
    max_iterations: int | None = None,
) -> ReachingDefinitions:
    """Run the reaching-definitions fixpoint over *cfg* with *fdu*'s def facts."""
    gen, kill, every = _block_facts(cfg, fdu)
    raise_srcs = {src for src, _dst in cfg.raise_edges}

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
            # OUT[pred] is a subset of IN[pred] | every[pred].
            if (pred, bid) in cfg.raise_edges:
                in_b |= in_sets[pred] | every[pred]
            else:
                in_b |= out_sets[pred]
        # A raise edge carries IN, so its target moves even when OUT does not.
        in_moved = bid in raise_srcs and in_b != in_sets[bid]
        in_sets[bid] = in_b

        out_b = gen[bid] | (in_b - kill[bid])
        if out_b != out_sets[bid] or in_moved:
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


def observed_definitions(
    fdu: FunctionDefUse, reaching: ReachingDefinitions
) -> Iterator[tuple[Occurrence, list[int]]]:
    """Each read in *fdu* with the definition indices it may observe
    (:func:`_observe`)."""
    for bid, bdu in fdu.blocks.items():
        if not bdu.uses:
            continue
        in_by_var: dict[str, list[int]] = {}
        for i in reaching.in_sets.get(bid, frozenset()):
            in_by_var.setdefault(reaching.definitions[i].var, []).append(i)
        local_by_var: dict[str, list[Definition]] = {}
        for d in bdu.defs:
            local_by_var.setdefault(d.var, []).append(d)
        for use in bdu.uses:
            seen = _observe(local_by_var.get(use.name, []), in_by_var.get(use.name, ()), use.line)
            yield use, seen


def definitions_at(
    fdu: FunctionDefUse, reaching: ReachingDefinitions, block_id: int, var: str, line: int
) -> list[int]:
    """The definition indices of *var* a read at *line* in *block_id* may
    observe (:func:`_observe`)."""
    bdu = fdu.block(block_id)
    local = [d for d in bdu.defs if d.var == var] if bdu is not None else []
    entering = [
        i for i in reaching.in_sets.get(block_id, ()) if reaching.definitions[i].var == var
    ]
    return _observe(local, entering, line)


def _observe(local: list[Definition], entering: Iterable[int], line: int) -> list[int]:
    """A read at *line* observes the latest same-block definitions (*local*)
    strictly before it or, when there is none, every definition *entering* the
    block; plus any same-block definition at *line* itself, since lines alone
    cannot order a write and a read sharing one. A may-def
    (``Definition.may``) is observed and looked past: the read also sees what
    came before it, back to the latest line holding a certain write. Over-marking
    is the safe side for every consumer."""
    seen: list[int] = []
    settled_at: int | None = None
    for d in sorted((d for d in local if d.line < line), key=lambda d: -d.line):
        if settled_at is not None and d.line != settled_at:
            break
        seen.append(d.index)
        if not d.may:
            settled_at = d.line
    if settled_at is None:
        seen.extend(entering)
    seen.extend(d.index for d in local if d.line == line)
    return seen
