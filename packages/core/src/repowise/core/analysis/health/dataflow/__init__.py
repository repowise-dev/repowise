"""Intra-procedural dataflow layer for the health pass.

This subpackage builds, per function, a control-flow graph (CFG), a def/use
classification, and reaching-definitions sets -- the semantic foundation for
dataflow-aware refactoring signals (Extract Method) and cross-layer consumers.
It is deliberately standalone: nothing here is wired into the health engine yet,
so the layer can be built and measured in isolation before it joins a hot path.

**Layering (language-agnostic core, per-language dialects).**

- :mod:`cfg` -- statement sequencer, basic-block splitter, CFG builder.
- :mod:`defuse` -- per-block def/use aggregation over the CFG, delegating the
  read-vs-write classification to a language :class:`DefUseDialect`.
- :mod:`reaching` -- the reaching-definitions worklist fixpoint (pure graph +
  def facts; no language knowledge).
- :mod:`dialects` -- the ``DefUseDialect`` registry; one module per language,
  zero core edits to add one.
- :mod:`analyze` -- composes the three stages behind one call.
- :mod:`gating` -- the flagged-only predicate and the CFG-only file harness.

**Performance contract (load-bearing).** Full dataflow is built ONLY for a
function a structural biomarker already flagged (``large_method`` /
``brain_method`` / ``complex_method``). The pass takes a pre-filtered
flagged-function set, never the full function corpus -- see
:func:`gating.is_flagged_function`, :func:`gating.build_cfgs_for_file`, and
:func:`analyze.analyze_file`. A CFG size guard caps pathological functions and
the reaching fixpoint has a convergence guard; any per-function failure (unmapped
language, guard trip, non-convergence) degrades to silence, never a raise.
"""

from __future__ import annotations

from .analyze import (
    FileAnalysisResult,
    FileAnalysisStats,
    FunctionAnalysis,
    analyze_file,
    analyze_function,
)
from .cfg import CFG, BasicBlock, Statement, build_cfg
from .defuse import BlockDefUse, Definition, FunctionDefUse, compute_def_use
from .dialects.base import (
    DEFUSE_DIALECTS,
    DefUseDialect,
    Occurrence,
    StatementDefUse,
    get_defuse_dialect,
)
from .gating import (
    CFGPassStats,
    FileCFGResult,
    FunctionCFG,
    build_cfgs_for_file,
    is_flagged,
    is_flagged_function,
)
from .reaching import ReachingDefinitions, compute_reaching
from .slice import Extraction, find_extractions

__all__ = [
    "CFG",
    "DEFUSE_DIALECTS",
    "BasicBlock",
    "BlockDefUse",
    "CFGPassStats",
    "DefUseDialect",
    "Definition",
    "Extraction",
    "FileAnalysisResult",
    "FileAnalysisStats",
    "FileCFGResult",
    "FunctionAnalysis",
    "FunctionCFG",
    "FunctionDefUse",
    "Occurrence",
    "ReachingDefinitions",
    "Statement",
    "StatementDefUse",
    "analyze_file",
    "analyze_function",
    "build_cfg",
    "build_cfgs_for_file",
    "compute_def_use",
    "compute_reaching",
    "find_extractions",
    "get_defuse_dialect",
    "is_flagged",
    "is_flagged_function",
]
