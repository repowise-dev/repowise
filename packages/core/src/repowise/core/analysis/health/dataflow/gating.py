"""Flagged-only gating + the file-level CFG harness.

The performance contract of the whole dataflow layer lives here. A CFG is
built **only** for a function that a structural biomarker already flagged --
``large_method`` (``nloc >= 60`` with some branching), ``complex_method``
(``ccn >= 9``), or ``brain_method`` (``nloc >= 70`` and ``ccn >= 9``, plus a
centrality gate the engine applies separately). The thresholds are imported
from the biomarker detectors so the gate never drifts from them.

:func:`build_cfgs_for_file` is the harness: it parses a file once, computes the
two cheap metrics the gate needs (``ccn`` and ``nloc``) per function reusing
the complexity walker's passes, and builds a CFG for the flagged subset only.
The :class:`CFGPassStats` it returns records ``functions_seen`` vs
``functions_built`` so a budget test can assert the pass never runs for an
un-flagged function. Any parse failure or per-function error degrades to
silence -- an empty result, never a raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from ..biomarkers.complex_method import ComplexMethodDetector
from ..biomarkers.large_method import LargeMethodDetector
from ..complexity.ast_utils import _collect_function_nodes, _find_function_entry_name
from .cfg import CFG, CFGGuardTrippedError, build_cfg
from .parsing import function_metrics, parse_source

log = structlog.get_logger(__name__)

# Pulled from the biomarker detectors so the gate stays in lockstep with the
# flags it mirrors. ``complex_method`` (ccn >= 9) already subsumes the
# ``brain_method`` ccn condition, so the predicate needs only these two shapes.
_COMPLEX_CCN = ComplexMethodDetector._CCN_THRESHOLD
_LARGE_NLOC = LargeMethodDetector._NLOC_THRESHOLD
_LARGE_CCN_FLOOR = LargeMethodDetector._CCN_FLOOR


def is_flagged(*, ccn: int, nloc: int) -> bool:
    """True if a function with these metrics is flagged by a structural smell.

    The union of the size/complexity biomarker triggers:

    - ``complex_method`` -- ``ccn >= 9`` (also covers ``brain_method``'s ccn).
    - ``large_method`` -- ``nloc >= 60`` with real branching (``ccn >= 3``).
    """
    if ccn >= _COMPLEX_CCN:
        return True
    return nloc >= _LARGE_NLOC and ccn >= _LARGE_CCN_FLOOR


def is_flagged_function(fc) -> bool:
    """:func:`is_flagged` over a walker ``FunctionComplexity`` record.

    ``fc`` is a ``complexity.models.FunctionComplexity``; it is untyped here to
    keep this leaf module free of a walker-models import.
    """
    return is_flagged(ccn=fc.ccn, nloc=fc.nloc)


@dataclass
class CFGPassStats:
    """Counters proving the flagged-only gate held for one file.

    ``functions_seen`` is every function the parse discovered;
    ``functions_built`` is the flagged subset a CFG was actually built for.
    ``guard_tripped`` counts functions skipped by the size cap. The budget test
    asserts ``functions_built`` equals the flagged count, never the total.
    """

    functions_seen: int = 0
    functions_built: int = 0
    guard_tripped: int = 0


@dataclass
class FunctionCFG:
    """A flagged function paired with its control-flow graph."""

    name: str
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    ccn: int
    nloc: int
    cfg: CFG


@dataclass
class FileCFGResult:
    """The CFGs built for one file's flagged functions, plus the pass stats."""

    functions: list[FunctionCFG] = field(default_factory=list)
    stats: CFGPassStats = field(default_factory=CFGPassStats)


def build_cfgs_for_file(
    abs_path: str,
    language: str,
    source: bytes,
    *,
    flagged_only: bool = True,
) -> FileCFGResult:
    """Parse *source* once and build a CFG for each flagged function.

    Returns an empty result (never raises) when the language is unsupported,
    the tree-sitter pack is missing, or parsing fails. ``flagged_only=False``
    is a test/measurement escape hatch that builds for every function; the
    default and the only production-intended mode is ``True``.
    """
    result = FileCFGResult()
    parsed = parse_source(abs_path, language, source)
    if parsed is None:
        return result
    root, lmap = parsed

    for fn_node in _collect_function_nodes(root, lmap):
        result.stats.functions_seen += 1
        metrics = function_metrics(fn_node, lmap, source)
        if metrics is None:
            continue
        ccn, nloc = metrics

        if flagged_only and not is_flagged(ccn=ccn, nloc=nloc):
            continue

        try:
            cfg = build_cfg(fn_node, lmap)
        except CFGGuardTrippedError:
            result.stats.guard_tripped += 1
            continue
        except Exception as exc:
            log.debug("dataflow_cfg_build_failed", path=abs_path, error=str(exc))
            continue

        cfg.function_name = _find_function_entry_name(fn_node, lmap)
        cfg.function_start_line = fn_node.start_point[0] + 1
        result.stats.functions_built += 1
        result.functions.append(
            FunctionCFG(
                name=cfg.function_name,
                start_line=cfg.function_start_line,
                end_line=fn_node.end_point[0] + 1,
                ccn=ccn,
                nloc=nloc,
                cfg=cfg,
            )
        )

    return result
