"""Intra-procedural dataflow layer for the health pass.

This subpackage builds a per-function control-flow graph (CFG) as the
foundation for dataflow-aware refactoring signals (Extract Method) and the
later def/use + reaching-definitions passes. It is deliberately standalone:
nothing here is wired into the health engine yet, so the layer can be built
and measured in isolation before it joins a hot path.

**Performance contract (load-bearing).** A CFG is built ONLY for a function a
structural biomarker already flagged (``large_method`` / ``brain_method`` /
``complex_method``). The pass takes a pre-filtered flagged-function set, never
the full function corpus -- see :func:`gating.is_flagged_function` and
:func:`gating.build_cfgs_for_file`. Building for every function could add a
double-digit percentage to the parse/walk layer, so we never do. A size guard
caps pathological functions (emit nothing past the cap), and any per-function
failure degrades to silence (no CFG, no raise).

Phase scope: Python only. The CFG builder is structural and largely
language-agnostic; the jump-node classification it relies on is Python-specific
for now and generalises behind a dialect in a later phase.
"""

from __future__ import annotations

from .cfg import CFG, BasicBlock, Statement, build_cfg
from .gating import (
    CFGPassStats,
    FileCFGResult,
    FunctionCFG,
    build_cfgs_for_file,
    is_flagged,
    is_flagged_function,
)

__all__ = [
    "CFG",
    "BasicBlock",
    "CFGPassStats",
    "FileCFGResult",
    "FunctionCFG",
    "Statement",
    "build_cfg",
    "build_cfgs_for_file",
    "is_flagged",
    "is_flagged_function",
]
