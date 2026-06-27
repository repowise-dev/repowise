"""End-to-end dataflow analysis: CFG + def/use + reaching definitions.

The public entry points compose the three language-agnostic stages (CFG build,
def/use aggregation, reaching-definitions fixpoint) behind a single call,
dispatching the only language-specific piece -- the def/use dialect -- through
the registry. :func:`analyze_function` runs one function; :func:`analyze_file`
applies the same flagged-only gate as the CFG harness so the pass stays within
budget, building full dataflow only for functions a structural biomarker already
flagged.

Every failure mode degrades to silence: an unmapped language (no dialect),
a CFG size-guard trip, or a non-converged fixpoint yields no analysis for that
function, never a raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from ..complexity.ast_utils import _collect_function_nodes, _find_function_entry_name
from .cfg import CFG, CFGGuardTrippedError, build_cfg
from .defuse import FunctionDefUse, compute_def_use
from .dialects.base import get_defuse_dialect
from .gating import is_flagged
from .parsing import function_metrics, parse_source
from .reaching import ReachingDefinitions, compute_reaching

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap

log = structlog.get_logger(__name__)


@dataclass
class FunctionAnalysis:
    """A function's CFG, def/use facts, and reaching definitions."""

    name: str
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    ccn: int
    nloc: int
    cfg: CFG
    def_use: FunctionDefUse
    reaching: ReachingDefinitions
    # The function's tree-sitter node, retained so dataflow consumers (the
    # Extract Method slicer) can re-walk its statement structure. Typed ``Any``
    # to keep the model free of a tree-sitter import; the node keeps its parse
    # tree alive while referenced, so it stays valid after analysis returns.
    fn_node: Any = None


@dataclass
class FileAnalysisStats:
    """Counters proving the flagged-only gate held over a file."""

    functions_seen: int = 0
    functions_analyzed: int = 0
    guard_tripped: int = 0
    not_converged: int = 0


@dataclass
class FileAnalysisResult:
    functions: list[FunctionAnalysis] = field(default_factory=list)
    stats: FileAnalysisStats = field(default_factory=FileAnalysisStats)


def analyze_function(
    fn_node: Node,
    language: str,
    lmap: LanguageNodeMap,
) -> tuple[CFG, FunctionDefUse, ReachingDefinitions] | None:
    """Build CFG + def/use + reaching definitions for one function node.

    Returns ``None`` when the language has no def/use dialect, the CFG size
    guard trips, or the fixpoint fails to converge (degrade to silence).
    """
    dialect = get_defuse_dialect(language)
    if dialect is None:
        return None
    try:
        cfg = build_cfg(fn_node, lmap)
    except CFGGuardTrippedError:
        return None
    def_use = compute_def_use(cfg, fn_node, lmap, dialect)
    reaching = compute_reaching(cfg, def_use)
    if not reaching.converged:
        return None
    return cfg, def_use, reaching


def analyze_file(
    abs_path: str,
    language: str,
    source: bytes,
    *,
    flagged_only: bool = True,
) -> FileAnalysisResult:
    """Parse *source* once and run full dataflow analysis on flagged functions.

    Mirrors :func:`gating.build_cfgs_for_file` but carries the analysis through
    def/use and reaching definitions. Returns an empty result (never raises) on
    any parse failure or unmapped language.
    """
    result = FileAnalysisResult()
    parsed = parse_source(abs_path, language, source)
    if parsed is None:
        return result
    root, lmap = parsed

    dialect = get_defuse_dialect(language)
    if dialect is None:
        # No def/use support for this language: count what we saw, build nothing.
        result.stats.functions_seen = len(_collect_function_nodes(root, lmap))
        return result

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
            log.debug("dataflow_analyze_build_failed", path=abs_path, error=str(exc))
            continue

        cfg.function_name = _find_function_entry_name(fn_node, lmap)
        cfg.function_start_line = fn_node.start_point[0] + 1
        def_use = compute_def_use(cfg, fn_node, lmap, dialect)
        reaching = compute_reaching(cfg, def_use)
        if not reaching.converged:
            result.stats.not_converged += 1
            continue

        result.stats.functions_analyzed += 1
        result.functions.append(
            FunctionAnalysis(
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
        )

    return result
