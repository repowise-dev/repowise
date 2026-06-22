"""Tree-sitter walker → CCN, max nesting, cognitive complexity.

One AST pass per file. For each function/method discovered at the top
level (or nested directly inside a class body / impl block) we recurse
through its body, accumulating:

- **CCN** — McCabe cyclomatic complexity. Start at 1; +1 per branch /
  loop / case / catch / boolean operator.
- **max_nesting** — deepest stack of nesting-contributing nodes within
  the function body.
- **cognitive** — SonarSource-style weighted score: each nesting node
  adds ``1 + current_depth`` (so deeper nesting hurts more); boolean
  operators add a flat +1; jumps (``return``/``break``/``continue``)
  do not contribute (kept simple in v1).

Anonymous functions (lambdas, arrow functions, closures) recurse for
their containing function's metrics when they are nested in a named
function. Module-level lambdas, such as route callbacks, produce their
own ``FunctionComplexity`` row.

This module is the orchestrator: ``walk_file`` parses the source once and
drives the individual passes, each of which lives in its own sibling module:

- ``models``         — the output dataclasses
- ``ast_utils``      — name/text helpers, function-node collection, params
- ``nloc``           — non-blank / non-comment line counting
- ``cyclomatic``     — the CCN / cognitive / nesting engine
- ``assertions``     — assertion-block detection (test-quality)
- ``error_handling`` — error-handling anti-patterns
- ``perf_walk``      — the performance-risk pass
- ``class_analysis`` — class-level LCOM4 / god-class metrics
"""

from __future__ import annotations

import structlog

from .assertions import _collect_assertion_blocks
from .ast_utils import (
    _collect_function_nodes,
    _count_parameters,
    _find_function_entry_name,
)
from .class_analysis import _collect_classes
from .cyclomatic import _walk_function_body
from .error_handling import _collect_error_handling
from .languages import get_language_map

# Re-exported so the package façade (``__init__``) and downstream consumers
# keep importing the output schema from ``complexity.walker`` unchanged.
from .models import (
    ClassComplexity,
    ConditionComplexity,
    ErrorHandlingHit,
    FileComplexity,
    FunctionComplexity,
    PerfFnFacts,
    PerfHit,
)

# ``_count_file_nloc`` is re-exported for ``tests/unit/health/test_file_nloc.py``,
# which imports it directly from this module.
from .nloc import _count_file_nloc, _count_file_nloc_tree, _count_nloc
from .perf_walk import _collect_perf_hits

__all__ = [
    "ClassComplexity",
    "ConditionComplexity",
    "ErrorHandlingHit",
    "FileComplexity",
    "FunctionComplexity",
    "PerfFnFacts",
    "PerfHit",
    "walk_file",
    "walk_file_complexity",
]

log = structlog.get_logger(__name__)


def walk_file(
    abs_path: str,
    language: str,
    source: bytes,
) -> FileComplexity:
    """Walk one file's AST once → per-function and per-class metrics.

    Returns an empty ``FileComplexity`` when:
      - the language is unsupported (no entry in ``LANGUAGE_MAPS``)
      - the tree-sitter language package isn't installed
      - parsing fails

    Class-level metrics are populated only when the language's
    ``LanguageNodeMap`` opts in via ``class_kinds`` (see ``languages.py``).
    """
    lmap = get_language_map(language)
    if lmap is None:
        return FileComplexity(functions=[], classes=[], file_nloc=_count_file_nloc(source))

    try:
        from tree_sitter import Parser

        # Reuse the ingestion parser's language registry. Importing
        # lazily avoids pulling tree-sitter at module load time when
        # health is run from a context where it isn't installed.
        from repowise.core.ingestion.parser import _get_language
    except Exception as exc:
        log.debug("complexity_walker_import_failed", error=str(exc))
        return FileComplexity(functions=[], classes=[], file_nloc=_count_file_nloc(source))

    grammar = _get_language(language)
    if grammar is None:
        return FileComplexity(functions=[], classes=[], file_nloc=_count_file_nloc(source))

    try:
        parser = Parser(grammar)
        tree = parser.parse(source)
    except Exception as exc:
        log.debug("complexity_walker_parse_failed", path=abs_path, error=str(exc))
        return FileComplexity(functions=[], classes=[], file_nloc=_count_file_nloc(source))

    functions: list[FunctionComplexity] = []
    fc_by_node_id: dict[int, FunctionComplexity] = {}
    for fn_node in _collect_function_nodes(tree.root_node, lmap):
        body = fn_node.child_by_field_name("body") or fn_node
        ccn, max_nest, cognitive, bumps, conditions = _walk_function_body(body, lmap)
        fc = FunctionComplexity(
            name=_find_function_entry_name(fn_node, lmap),
            start_line=fn_node.start_point[0] + 1,
            end_line=fn_node.end_point[0] + 1,
            ccn=ccn,
            max_nesting=max_nest,
            cognitive=cognitive,
            nloc=_count_nloc(body, source),
            bumps=bumps,
            param_count=_count_parameters(fn_node),
            complex_conditions=conditions,
            assertion_blocks=_collect_assertion_blocks(body, lmap),
        )
        functions.append(fc)
        fc_by_node_id[fn_node.id] = fc

    classes = _collect_classes(tree.root_node, lmap, source, fc_by_node_id)
    perf_hits, io_boundary_names, perf_fn_facts = _collect_perf_hits(tree.root_node, language, lmap)
    return FileComplexity(
        functions=functions,
        classes=classes,
        file_nloc=_count_file_nloc_tree(tree.root_node, source),
        error_handling_hits=_collect_error_handling(tree.root_node, language, lmap),
        perf_hits=perf_hits,
        io_boundary_names=io_boundary_names,
        perf_fn_facts=perf_fn_facts,
    )


def walk_file_complexity(
    abs_path: str,
    language: str,
    source: bytes,
) -> list[FunctionComplexity]:
    """Backward-compatible wrapper: returns only per-function metrics.

    Prefer ``walk_file`` when class-level metrics are also needed.
    """
    return walk_file(abs_path, language, source).functions
