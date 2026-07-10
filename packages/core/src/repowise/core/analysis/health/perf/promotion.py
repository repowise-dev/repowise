"""Advisory -> asserted promotion of perf findings via intra-procedural dataflow.

Two perf markers are advisory *by construction* because a file-local pass cannot
prove the one thing that would make them certain - that the loop's iterations do
not depend on each other:

  * ``serial_await_in_loop`` - an awaited I/O call run once per iteration. The fix
    (fan out with ``gather`` / ``Promise.all``) is only valid when iteration i+1
    does not consume a value iteration i produced.
  * ``nested_loop_quadratic`` - a data-dependent loop nested in another. The
    "use a set/map lookup" advice presumes the inner loop is a genuine full scan,
    not an accumulation that carries state.

This module supplies exactly that missing proof, and only that. For a function
already carrying one of those advisory hits it runs the dataflow layer
(:func:`dataflow.analyze_function` - CFG + reaching definitions) over the *one*
loop the hit sits in and checks for a **loop-carried true dependence**: a use of
some variable, inside the loop, that can read a value a previous iteration wrote.
When none exists the iterations are provably independent and the hit is marked
``promoted`` (the biomarker then asserts rather than hedges). When the proof is
unavailable - no def/use dialect for the language, the CFG guard trips, the
fixpoint does not converge, no enclosing loop is found, or a genuine carried
dependence *is* present - the hit is left advisory. Precision over recall: a
false promotion is the one way to burn the perf pillar's trust, so every
uncertain case degrades to silence.

**Soundness of the carried-dependence check.** Using reaching definitions plus a
per-iteration must-def analysis, a loop use of ``v`` is *upward-exposed* (can see
a previous iteration's value) when, on some path from the loop header to the use,
``v`` is not redefined first. A loop carries a dependence iff some in-loop use is
upward-exposed to an in-loop definition of the same variable. The loop induction
variable is redefined at the header on every path, so it is never flagged; an
accumulator (``acc = acc + x``) reads before its same-iteration write, so it is.
The analysis tracks only local-variable data flow - attribute/field state, global
mutation, aliasing, and I/O ordering are out of scope - so it is a *necessary*
condition checker: it can only ever REFUSE to promote (find a possible
dependence), never invent independence where local flow shows a carry. That
directionality is what keeps the promotion sound.

**Budget.** The dataflow build runs ONLY for functions that already hold an
advisory hit - a strict subset of the perf scan, itself a subset of the walk. A
file with no such hit is never re-parsed. The same flagged-only discipline the
Extract Method consumer uses, keyed on the perf hit instead of a structural smell.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..complexity import FileComplexity
    from ..dataflow.cfg import CFG
    from ..dataflow.defuse import FunctionDefUse
    from ..dataflow.facts import FileDataflowCache
    from ..dataflow.reaching import ReachingDefinitions

log = structlog.get_logger(__name__)

# The dataflow layer and the complexity walker are imported lazily inside the
# functions below (never at module load): ``perf/__init__`` is pulled in while
# the complexity walker is still initialising (``perf_walk`` -> ``perf.dialects``),
# and the dataflow layer imports the biomarker detectors, so an eager import here
# would close an import cycle. The same lazy discipline ``perf.crossfn`` uses.

# The advisory markers this pass can turn into asserted findings. Both hedge on
# iteration independence, which the loop-carried-dependence proof settles.
PROMOTABLE_KINDS: frozenset[str] = frozenset({"serial_await_in_loop", "nested_loop_quadratic"})


def apply_perf_promotions(
    walked: Iterable[tuple[Any, FileComplexity]],
    dataflow: FileDataflowCache | None = None,
) -> None:
    """Mark provably-independent advisory perf hits ``promoted``, in place.

    Mirrors ``engine._apply_crossfn_perf``: it mutates each file's ``perf_hits``
    in place and is fully failure-isolated, so a promotion hiccup never blocks
    the health report. Must run AFTER the graph-dependent perf passes so the
    centrality-gated ``nested_loop_quadratic`` hits are present.

    *dataflow* is the pass-wide :class:`FileDataflowCache` the engine threads
    through every dataflow consumer, so a file this pass analyzes is parsed at
    most once across the whole health pass. A private cache is created when the
    caller supplies none (tests, standalone use).
    """
    if dataflow is None:
        from ..dataflow.facts import FileDataflowCache

        dataflow = FileDataflowCache()
    for pf, fcx in walked:
        try:
            promoted_lines = _promotable_lines_for_file(pf, fcx, dataflow)
        except Exception as exc:  # never let a single file break the pass
            log.debug(
                "perf_promotion_failed", path=getattr(pf.file_info, "path", "?"), error=str(exc)
            )
            continue
        if not promoted_lines:
            continue
        fcx.perf_hits = [
            replace(h, promoted=True)
            if (h.kind in PROMOTABLE_KINDS and h.line in promoted_lines and not h.promoted)
            else h
            for h in fcx.perf_hits
        ]


def _promotable_lines_for_file(
    pf: Any, fcx: FileComplexity, dataflow: FileDataflowCache
) -> set[int]:
    """The advisory-hit lines in *fcx* the dataflow proof clears for promotion.

    Returns an empty set (never raises up to the caller for the common cases)
    when the file has no promotable advisory hit, the language has no def/use
    dialect, or the parse fails - the documented degrade-to-silence outcomes,
    all realised inside the shared per-file service.
    """
    target_lines = {h.line for h in fcx.perf_hits if h.kind in PROMOTABLE_KINDS}
    if not target_lines:
        return set()

    fd = dataflow.get(pf.file_info.abs_path, pf.file_info.language)
    promoted: set[int] = set()
    for analysis in fd.analyses_covering(target_lines):
        # A guard trip / non-convergence never reaches here (the service
        # returns no analysis for that function -> the hit stays advisory).
        hits_here = {ln for ln in target_lines if analysis.start_line <= ln <= analysis.end_line}
        for line in hits_here:
            if _loop_iterations_independent(
                analysis.cfg, analysis.def_use, analysis.reaching, line
            ):
                promoted.add(line)
    return promoted


# ---------------------------------------------------------------------------
# The loop-carried-dependence proof
# ---------------------------------------------------------------------------


def _loop_iterations_independent(
    cfg: CFG, def_use: FunctionDefUse, reaching: ReachingDefinitions, line: int
) -> bool:
    """True iff the innermost loop containing *line* carries no data dependence.

    ``reaching`` is accepted for interface symmetry with the dataflow layer (and
    to assert the fixpoint converged); the proof itself is realised with a
    per-iteration must-def analysis over the CFG + def/use facts, which is what
    distinguishes an intra-iteration read from a cross-iteration one.
    """
    if not reaching.converged:
        return False
    loop = _innermost_loop_containing(cfg, line)
    if loop is None:
        return False  # no loop enclosing the hit -> nothing proven
    loop_blocks, header_id = loop

    # Variables written somewhere inside the loop body - only these can carry a
    # value from one iteration into the next.
    in_loop_defs: set[str] = {d.var for d in def_use.definitions if d.block_id in loop_blocks}
    if not in_loop_defs:
        return True  # nothing the loop writes -> nothing to carry

    defined_before = _must_defined_before_block(cfg, def_use, loop_blocks, header_id)

    for bid in loop_blocks:
        bdu = def_use.block(bid)
        if bdu is None:
            continue
        local_def_lines: dict[str, list[int]] = {}
        for d in bdu.defs:
            local_def_lines.setdefault(d.var, []).append(d.line)
        must_here = defined_before.get(bid, frozenset())
        for use in bdu.uses:
            v = use.name
            if v not in in_loop_defs:
                continue  # loop-invariant or param read -> not carried
            if v in must_here:
                continue  # redefined on every path before this block this iteration
            earlier = local_def_lines.get(v)
            if earlier and any(dl < use.line for dl in earlier):
                continue  # a same-block write strictly precedes the read (current iter)
            # Upward-exposed to an in-loop definition: the read can observe a
            # value a previous iteration wrote -> a loop-carried dependence.
            return False
    return True


def _innermost_loop_containing(cfg: CFG, line: int) -> tuple[frozenset[int], int] | None:
    """The (blocks, header_id) of the smallest natural loop covering *line*."""
    best: tuple[frozenset[int], int] | None = None
    for latch, header in cfg.back_edges():
        blocks = _natural_loop(cfg, latch, header)
        if not _blocks_cover_line(cfg, blocks, line):
            continue
        if best is None or len(blocks) < len(best[0]):
            best = (blocks, header)
    return best


def _natural_loop(cfg: CFG, latch: int, header: int) -> frozenset[int]:
    """The natural loop of back-edge ``latch -> header``.

    ``{header}`` plus every node that reaches *latch* without passing through
    *header* - the classic natural-loop set. Header's own predecessors are never
    traversed (it is seeded into the set first), so the pre-loop block and any
    outer structure stay out.
    """
    loop = {header}
    if latch not in loop:
        loop.add(latch)
        stack = [latch]
        while stack:
            n = stack.pop()
            for p in cfg.block(n).predecessors:
                if p not in loop:
                    loop.add(p)
                    stack.append(p)
    return frozenset(loop)


def _blocks_cover_line(cfg: CFG, blocks: frozenset[int], line: int) -> bool:
    """True if any statement in *blocks* spans *line* (1-indexed)."""
    for bid in blocks:
        for stmt in cfg.block(bid).statements:
            if stmt.start_line <= line <= stmt.end_line:
                return True
    return False


def _must_defined_before_block(
    cfg: CFG, def_use: FunctionDefUse, loop_blocks: frozenset[int], header_id: int
) -> dict[int, frozenset[str]]:
    """Variables guaranteed written before each loop block, within one iteration.

    A forward *must* (intersection) analysis over the loop's blocks, treating the
    header as the iteration start (nothing defined yet) and ignoring the back-edge
    - so it measures what a single pass from the header definitely writes before a
    given block. With back-edges removed the loop subgraph is a DAG whose block-id
    order is a valid topological order, so one pass in id order reaches the
    fixpoint. A use whose variable is in this set was redefined this iteration and
    cannot read a previous one.
    """
    defs_in_block: dict[int, frozenset[str]] = {}
    for bid in loop_blocks:
        bdu = def_use.block(bid)
        defs_in_block[bid] = frozenset(d.var for d in bdu.defs) if bdu is not None else frozenset()

    defined_in: dict[int, frozenset[str]] = {}
    defined_out: dict[int, frozenset[str]] = {}
    for block in cfg.blocks:  # emission (id) order
        bid = block.id
        if bid not in loop_blocks:
            continue
        if bid == header_id:
            din: frozenset[str] = frozenset()
        else:
            pred_outs = [
                defined_out[p]
                for p in block.predecessors
                if p in loop_blocks and p in defined_out and not _is_back_edge(cfg, p, bid)
            ]
            din = frozenset.intersection(*pred_outs) if pred_outs else frozenset()
        defined_in[bid] = din
        defined_out[bid] = din | defs_in_block[bid]
    return defined_in


def _is_back_edge(cfg: CFG, src: int, dst: int) -> bool:
    """True if ``src -> dst`` is a loop back-edge (into a loop header, src > dst)."""
    return cfg.block(dst).kind == "loop_header" and src > dst
