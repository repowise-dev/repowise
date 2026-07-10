"""Shared per-file dataflow service + the curated ``DataflowFacts`` summary.

Before this module each dataflow consumer (the Extract Method detector, the
perf-promotion pass) read and parsed its target file independently, so a file
hit by two gates was parsed twice on top of the complexity walker's own parse.
:class:`FileDataflow` ends that pattern: one lazy parse per file, one analysis
per function, every consumer reads the same memoized :class:`FunctionAnalysis`.
:class:`FileDataflowCache` shares those objects across consumers for the
lifetime of one health pass.

Laziness is the design's load-bearing property. Constructing a
:class:`FileDataflow` costs nothing: no I/O, no parse. The source is read and
parsed only when a consumer's gate actually fires (a promotable perf hit, a
method-level smell), which preserves the layer's flagged-only budget exactly --
an ungated file is never touched.

:class:`DataflowFacts` is the single curated summary shape downstream surfaces
(MCP tools, dead-code kinds, wiki context) derive from a
:class:`FunctionAnalysis`. It is derivation only -- every field is read off the
existing CFG / def-use / reaching primitives, no new analysis. Facts are raw
and deterministic; suppression policy (underscore conventions, size floors)
belongs to each consumer, not here.

Failure contract: everything degrades to silence. An unreadable file, an
unsupported language, a guard trip, or a non-converged fixpoint yields an empty
list or ``None``, never a raise.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ..complexity.ast_utils import _collect_function_nodes, _find_function_entry_name
from . import analyze as _analyze
from .analyze import FunctionAnalysis
from .dialects.base import get_defuse_dialect
from .gating import is_flagged
from .parsing import function_metrics, parse_source

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# The curated facts summary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeadStore:
    """A write whose value no subsequent read can observe."""

    var: str
    line: int  # 1-indexed


@dataclass(frozen=True)
class DataflowFacts:
    """A function's dataflow summary, derived from one :class:`FunctionAnalysis`.

    All name tuples are sorted and de-duplicated; ``params_read`` /
    ``params_unused`` keep parameter declaration order. Identical source yields
    identical facts (the determinism the CFG layer already guarantees).

    Scope caveats consumers must respect: the layer tracks local-variable data
    flow only. A "dead" store may still be observed by a closure, and ``reads``
    names free variables (globals, imports, attributes are out of scope of the
    def/use dialects).
    """

    name: str
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    params_read: tuple[str, ...]
    params_unused: tuple[str, ...]
    reads: tuple[str, ...]  # free names: read but never defined locally
    writes: tuple[str, ...]  # locals defined (parameters excluded)
    flows_out: tuple[str, ...]  # locals whose value can reach the function exit
    dead_stores: tuple[DeadStore, ...]
    unreachable_lines: tuple[int, ...]  # statement start lines in dead blocks
    block_count: int
    converged: bool


def derive_facts(analysis: FunctionAnalysis) -> DataflowFacts:
    """Read a :class:`DataflowFacts` summary off *analysis*'s primitives."""
    def_use = analysis.def_use
    cfg = analysis.cfg
    reaching = analysis.reaching

    param_names: list[str] = []
    for occ in def_use.params:
        if occ.name not in param_names:
            param_names.append(occ.name)
    param_set = set(param_names)

    use_names = {u.name for bdu in def_use.blocks.values() for u in bdu.uses}
    defined_names = {d.var for d in def_use.definitions}

    # Parameter seeds are the first len(params) definitions (compute_def_use
    # assigns their indices before any statement's) -- everything after is a
    # body write site.
    n_param_seeds = len(def_use.params)

    live = _live_definition_indices(analysis)
    dead_stores = tuple(
        DeadStore(var=d.var, line=d.line)
        for d in sorted(
            (d for d in def_use.definitions if d.index >= n_param_seeds and d.index not in live),
            key=lambda d: (d.line, d.var),
        )
    )

    exit_defs = reaching.in_sets.get(cfg.exit_id, frozenset())
    flows_out = tuple(sorted({def_use.definitions[i].var for i in exit_defs if i >= n_param_seeds}))

    reachable = cfg.reachable_ids()
    unreachable_lines = tuple(
        sorted(
            {
                stmt.start_line
                for block in cfg.blocks
                if block.id not in reachable
                for stmt in block.statements
            }
        )
    )

    return DataflowFacts(
        name=analysis.name,
        start_line=analysis.start_line,
        end_line=analysis.end_line,
        params_read=tuple(p for p in param_names if p in use_names),
        params_unused=tuple(p for p in param_names if p not in use_names),
        reads=tuple(sorted(use_names - defined_names)),
        writes=tuple(sorted(defined_names - param_set)),
        flows_out=flows_out,
        dead_stores=dead_stores,
        unreachable_lines=unreachable_lines,
        block_count=len(cfg.blocks),
        converged=reaching.converged,
    )


def _live_definition_indices(analysis: FunctionAnalysis) -> set[int]:
    """Definition indices some read can observe (conservative: over-marks live).

    A use of ``v`` at line ``L`` in block ``b`` observes:

    - the latest same-block definition(s) of ``v`` strictly before ``L``, or,
      when none exists, every definition of ``v`` reaching ``IN[b]``;
    - plus, conservatively, any same-block definition of ``v`` AT line ``L``
      (multiple statements on one line cannot be ordered by line numbers alone,
      so ambiguity counts as live -- fewer dead stores, never a false one).

    Anything the rules cannot prove observed stays potentially dead; the caller
    subtracts this set from all body definitions to get the dead stores.
    """
    def_use = analysis.def_use
    reaching = analysis.reaching
    live: set[int] = set()

    for bid, bdu in def_use.blocks.items():
        if not bdu.uses:
            continue
        in_defs = reaching.in_sets.get(bid, frozenset())
        in_by_var: dict[str, list[int]] = {}
        for i in in_defs:
            in_by_var.setdefault(reaching.definitions[i].var, []).append(i)
        local_by_var: dict[str, list] = {}
        for d in bdu.defs:
            local_by_var.setdefault(d.var, []).append(d)

        for use in bdu.uses:
            local = local_by_var.get(use.name, [])
            earlier = [d for d in local if d.line < use.line]
            if earlier:
                latest = max(d.line for d in earlier)
                live.update(d.index for d in earlier if d.line == latest)
            else:
                live.update(in_by_var.get(use.name, ()))
            live.update(d.index for d in local if d.line == use.line)

    return live


# ---------------------------------------------------------------------------
# The shared per-file service
# ---------------------------------------------------------------------------


class FileDataflow:
    """Lazy dataflow analyses for one file: parse once, analyze each function once.

    Construction is free. The first accessor that needs the AST triggers the
    (single) source read + parse; per-function analyses are memoized so a
    function requested by two consumers is analyzed exactly once. Every failure
    mode -- unreadable file, no def/use dialect, parse failure, guard trip,
    non-convergence -- degrades to an empty result for the affected scope.
    """

    def __init__(self, abs_path: str, language: str, source: bytes | None = None) -> None:
        self.abs_path = abs_path
        self.language = language
        self._source = source
        self._parsed = False
        self._root: Node | None = None
        self._lmap: LanguageNodeMap | None = None
        self._fn_nodes: list[Node] = []
        # Memos keyed by position in ``_fn_nodes`` (start lines can collide
        # for same-line lambdas; collection order is deterministic).
        self._metrics: dict[int, tuple[int, int] | None] = {}
        self._analyses: dict[int, FunctionAnalysis | None] = {}

    # -- consumer entry points ------------------------------------------------

    def flagged_analyses(self) -> list[FunctionAnalysis]:
        """Analyses for the structurally flagged functions, in source order.

        The Extract Method consumer's view: the same flagged-only gate (and the
        same skips on metric failure, guard trip, or non-convergence) as
        :func:`analyze.analyze_file`.
        """
        out: list[FunctionAnalysis] = []
        for idx, fn_node in enumerate(self._functions()):
            metrics = self._metrics_for(idx, fn_node)
            if metrics is None:
                continue
            ccn, nloc = metrics
            if not is_flagged(ccn=ccn, nloc=nloc):
                continue
            analysis = self._analysis_for(idx, fn_node)
            if analysis is not None:
                out.append(analysis)
        return out

    def analyses_covering(self, lines: Iterable[int]) -> list[FunctionAnalysis]:
        """Analyses for every function whose span contains one of *lines*.

        The perf-promotion consumer's view: no structural gate (the perf hit is
        the gate), nested functions covering the same line are all returned so
        the caller can try each enclosing scope.
        """
        wanted = set(lines)
        if not wanted:
            return []
        out: list[FunctionAnalysis] = []
        for idx, fn_node in enumerate(self._functions()):
            fstart = fn_node.start_point[0] + 1
            fend = fn_node.end_point[0] + 1
            if not any(fstart <= ln <= fend for ln in wanted):
                continue
            analysis = self._analysis_for(idx, fn_node)
            if analysis is not None:
                out.append(analysis)
        return out

    def analysis_at(self, start_line: int, name: str | None = None) -> FunctionAnalysis | None:
        """The analysis for the function starting at *start_line*.

        Start-line match first; *name* disambiguates same-line collisions and,
        when no function starts at *start_line* (bounds drifted), a unique name
        match is the fallback. ``None`` on any miss or analysis failure.
        """
        nodes = list(enumerate(self._functions()))
        by_line = [(i, n) for i, n in nodes if n.start_point[0] + 1 == start_line]
        if name and len(by_line) != 1:
            lmap = self._lmap
            named = [
                (i, n)
                for i, n in (by_line or nodes)
                if lmap is not None and _find_function_entry_name(n, lmap) == name
            ]
            if len(named) == 1:
                by_line = named
        if len(by_line) != 1:
            return None
        idx, fn_node = by_line[0]
        return self._analysis_for(idx, fn_node)

    # -- internals -------------------------------------------------------------

    def _functions(self) -> list[Node]:
        """The file's function nodes, parsing on first demand (empty on failure)."""
        if self._parsed:
            return self._fn_nodes
        self._parsed = True
        # No def/use dialect means no consumer can produce a result: skip the
        # read + parse entirely (the promotion pass's early-exit, generalized).
        if get_defuse_dialect(self.language) is None:
            return self._fn_nodes
        source = self._read_source()
        if source is None:
            return self._fn_nodes
        parsed = parse_source(self.abs_path, self.language, source)
        if parsed is None:
            return self._fn_nodes
        self._root, self._lmap = parsed
        self._fn_nodes = _collect_function_nodes(self._root, self._lmap)
        return self._fn_nodes

    def _read_source(self) -> bytes | None:
        if self._source is not None:
            return self._source
        try:
            self._source = Path(self.abs_path).read_bytes()
        except OSError:
            return None
        return self._source

    def _metrics_for(self, idx: int, fn_node: Node) -> tuple[int, int] | None:
        if idx not in self._metrics:
            if self._lmap is None or self._source is None:
                return None
            self._metrics[idx] = function_metrics(fn_node, self._lmap, self._source)
        return self._metrics[idx]

    def _analysis_for(self, idx: int, fn_node: Node) -> FunctionAnalysis | None:
        if idx in self._analyses:
            return self._analyses[idx]
        analysis = self._build_analysis(idx, fn_node)
        self._analyses[idx] = analysis
        return analysis

    def _build_analysis(self, idx: int, fn_node: Node) -> FunctionAnalysis | None:
        lmap = self._lmap
        if lmap is None:
            return None
        try:
            # Module-attribute call so test harnesses that patch
            # ``analyze.analyze_function`` keep observing every build.
            analyzed = _analyze.analyze_function(fn_node, self.language, lmap)
        except Exception as exc:
            log.debug("dataflow_facts_build_failed", path=self.abs_path, error=str(exc))
            return None
        if analyzed is None:
            return None
        cfg, def_use, reaching = analyzed
        cfg.function_name = _find_function_entry_name(fn_node, lmap)
        cfg.function_start_line = fn_node.start_point[0] + 1
        metrics = self._metrics_for(idx, fn_node)
        ccn, nloc = metrics if metrics is not None else (0, 0)
        return FunctionAnalysis(
            name=cfg.function_name,
            start_line=cfg.function_start_line,
            end_line=fn_node.end_point[0] + 1,
            ccn=ccn,
            nloc=nloc,
            cfg=cfg,
            def_use=def_use,
            reaching=reaching,
            fn_node=fn_node,
        )


class FileDataflowCache:
    """Shares one :class:`FileDataflow` per file across a health pass's consumers.

    Keyed by absolute path; entries are created lazily and hold no parse until
    a consumer gate fires. The cache lives for one pass and is dropped with it.
    """

    def __init__(self) -> None:
        self._by_path: dict[str, FileDataflow] = {}

    def get(self, abs_path: str, language: str) -> FileDataflow:
        fd = self._by_path.get(abs_path)
        if fd is None:
            fd = FileDataflow(abs_path, language)
            self._by_path[abs_path] = fd
        return fd
