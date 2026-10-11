"""Tree-sitter walker → CCN, max nesting, cognitive complexity.

One AST pass per file. For each function/method discovered at the top
level (or nested directly inside a class body / impl block) we recurse
through its body, accumulating:

- **CCN** — McCabe cyclomatic complexity. Start at 1; +1 per branch /
  loop / case / catch / boolean operator.
- **max_nesting** — deepest stack of nesting-contributing nodes within
  the function body.
- **cognitive** — a weighted nesting score: each nesting node
  adds ``1 + current_depth`` (so deeper nesting hurts more); boolean
  operators add a flat +1; jumps (``return``/``break``/``continue``)
  do not contribute (kept simple in v1).

Anonymous functions (lambdas, arrow functions, closures) recurse for
their containing function's metrics when they are nested in a named
function. Module-level lambdas, such as route callbacks, produce their
own ``FunctionComplexity`` row.

This module is the orchestrator: ``walk_file`` parses the source once and
drives the individual passes, each of which lives in its own sibling module.
The whole-file passes share one descent of the tree (``file_scan``):

- ``models``:         the output dataclasses
- ``file_scan``:      the shared descent: NLOC index, error handling, classes
- ``ast_utils``:      name/text helpers, function-node collection, params
- ``nloc``:           non-blank / non-comment line counting
- ``cyclomatic``:     the CCN / cognitive / nesting engine
- ``assertions``:     assertion blocks + per-function assertion totals
- ``test_case``:      whether a walked function is a test case
- ``dispatch``:       how much of the CCN is one dispatch on one value
- ``deprecation``:    whether a function is marked deprecated
- ``gating``:         whether a constant-false flag switches a function off
- ``mock_walk``:      per-function mock-setup counting (test-quality)
- ``error_handling``: error-handling anti-patterns
- ``perf_walk``:      the performance-risk pass
- ``class_analysis``: class-level LCOM4 / god-class metrics
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog

from ..asserts.lexicon import assert_dialect as _assert_dialect
from ..mocks.lexicon import mock_dialect as _mock_dialect
from .assertions import _collect_assertion_facts
from .ast_utils import (
    _collect_function_nodes,
    _count_parameters,
    _find_function_entry_name,
    _parameter_list,
)
from .class_analysis import _collect_classes
from .cyclomatic import _walk_function_body
from .deprecation import is_deprecated
from .dispatch import dispatch_points, dispatch_share
from .file_scan import scan_file
from .gating import is_gated_off, module_false_constants
from .languages import get_language_map
from .mock_walk import _count_mock_setup, file_may_contain_mocks

# Re-exported so the package façade (``__init__``) and downstream consumers
# keep importing the output schema from ``complexity.walker`` unchanged.
from .models import (
    ClassComplexity,
    CohesionGroup,
    ConditionComplexity,
    ErrorHandlingHit,
    FileComplexity,
    FunctionComplexity,
    PerfFnFacts,
    PerfHit,
)

# ``_count_file_nloc`` is re-exported for ``tests/unit/health/test_file_nloc.py``,
# which imports it directly from this module.
from .nloc import _count_file_nloc
from .perf_walk import _collect_perf_hits, perf_pass_runs
from .signature import is_constructor, is_signature_fixed, typed_param_counts
from .test_case import is_test_case

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..dataflow.slice import FunctionFacts
    from .body_facts import BodyTally

__all__ = [
    "ClassComplexity",
    "CohesionGroup",
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
    extra_assert_names: frozenset[str] = frozenset(),
) -> FileComplexity:
    """Walk one file's AST once → per-function and per-class metrics.

    Returns an empty ``FileComplexity`` when:
      - the language is unsupported (no entry in ``LANGUAGE_MAPS``)
      - the tree-sitter language package isn't installed
      - parsing fails

    Class-level metrics are populated only when the language's
    ``LanguageNodeMap`` opts in via ``class_kinds`` (see ``languages.py``).

    *extra_assert_names* is the repository's configured assertion vocabulary.
    It reaches the broad tier only, so the result stays a pure function of the
    bytes, the language and the walker's version for every repository that
    configures none — which is what the walk cache keys on.
    """
    lmap = get_language_map(language)
    if lmap is None:
        return FileComplexity(functions=[], classes=[], file_nloc=_count_file_nloc(source))

    try:
        from tree_sitter import Parser

        # Reuse the ingestion parser's language registry. Importing
        # lazily avoids pulling tree-sitter at module load time when
        # health is run from a context where it isn't installed.
        from repowise.core.ingestion.parser import _get_language, grammar_tag_for
    except Exception as exc:
        log.debug("complexity_walker_import_failed", error=str(exc))
        return FileComplexity(functions=[], classes=[], file_nloc=_count_file_nloc(source))

    # The grammar follows the path, the language tag does not: everything
    # below still selects its dialects by ``language``. ``engine.py`` keys the
    # walk cache on this same tag, so the two must not drift.
    grammar = _get_language(grammar_tag_for(language, abs_path))
    if grammar is None:
        return FileComplexity(functions=[], classes=[], file_nloc=_count_file_nloc(source))

    try:
        from repowise.core.ingestion.sfc_source import prepare_source

        parser = Parser(grammar)
        # SFCs reach the TS grammar as a markup-blanked buffer at
        # byte-identical offsets, so every offset below (including the NLOC
        # slices, which read the ORIGINAL bytes) stays valid. Pascal gets its
        # project-file sanitizer the same way. No-op elsewhere.
        tree = parser.parse(prepare_source(language, source, path=abs_path))
    except Exception as exc:
        log.debug("complexity_walker_parse_failed", path=abs_path, error=str(exc))
        return FileComplexity(functions=[], classes=[], file_nloc=_count_file_nloc(source))

    functions: list[FunctionComplexity] = []
    fc_by_node_id: dict[int, FunctionComplexity] = {}
    # Dialect first: it is a dict hit that rules out most languages before the
    # byte scan. Keyed on the language and the bytes, never the path: above,
    # the path settles the grammar and nothing else.
    dialect = _mock_dialect(language)
    mock_dialect = dialect if dialect is not None and file_may_contain_mocks(source) else None
    # Broad-tier assertion vocabulary. ``None`` for a language with no row,
    # which leaves the narrow tier alone and is what every language counted
    # before this existed.
    asserts = _assert_dialect(language, extra_assert_names)
    run_perf = perf_pass_runs(language, lmap)
    scan = scan_file(tree.root_node, language, lmap, source, io_names=run_perf)
    flags = module_false_constants(tree.root_node, source, language)
    facts_of = _facts_reader(abs_path, language, lmap)
    for fn_node in _collect_function_nodes(tree.root_node, lmap):
        body = fn_node.child_by_field_name("body") or fn_node
        deepest: list[int] = []
        start_facts, finish_facts = facts_of(fn_node)
        ccn, max_nest, cognitive, bumps, conditions = _walk_function_body(
            body, lmap, deepest, start_facts
        )
        (
            assertion_blocks,
            assertion_count,
            verifications,
            raises,
            called,
            bare_called,
        ) = _collect_assertion_facts(body, lmap, asserts)
        name = _find_function_entry_name(fn_node, lmap)
        typed, scalar = typed_param_counts(_parameter_list(fn_node), lmap)
        dispatch = dispatch_points(body, lmap)
        fc = FunctionComplexity(
            name=name,
            start_line=fn_node.start_point[0] + 1,
            end_line=fn_node.end_point[0] + 1,
            ccn=ccn,
            max_nesting=max_nest,
            cognitive=cognitive,
            nloc=scan.lines.count(body, source),
            bumps=bumps,
            param_count=_count_parameters(fn_node),
            typed_param_count=typed,
            primitive_param_count=scalar,
            is_constructor=is_constructor(fn_node, name, lmap),
            signature_fixed=is_signature_fixed(fn_node, lmap),
            complex_conditions=conditions,
            assertion_blocks=assertion_blocks,
            assertion_count=assertion_count,
            verification_count=verifications,
            raise_count=raises,
            mock_setup_count=_count_mock_setup(fn_node, body, lmap, mock_dialect, asserts),
            is_test_case=is_test_case(fn_node, name, language),
            called_names=called,
            bare_called_names=bare_called,
            dispatch_share=dispatch_share(dispatch.points, ccn),
            dispatch_arm=dispatch.arm,
            deprecated=is_deprecated(fn_node, body, name, lmap, source),
            gated_off=body is not fn_node and is_gated_off(body, flags),
            deepest_block=(deepest[0], deepest[1]) if deepest else None,
            facts=finish_facts(),
        )
        functions.append(fc)
        fc_by_node_id[fn_node.id] = fc

    classes = _collect_classes(
        scan.class_nodes, lmap, source, fc_by_node_id, scan.lines, language
    )
    perf_hits, io_boundary_names, perf_fn_facts = _collect_perf_hits(
        tree.root_node, language, lmap, scan.io_names
    )
    return FileComplexity(
        functions=functions,
        classes=classes,
        file_nloc=scan.lines.file_nloc,
        error_handling_hits=scan.error_handling_hits,
        perf_hits=perf_hits,
        io_boundary_names=io_boundary_names,
        perf_fn_facts=perf_fn_facts,
        has_inline_tests=_detect_inline_tests(source, language),
        rust_test_line_ranges=scan.rust_test_line_ranges,
    )


FactsStart = Callable[["Node"], tuple["BodyTally | None", Callable[[], "FunctionFacts | None"]]]
"""``fn_node -> (tally, finish)``; see :func:`_facts_reader`."""


def _facts_reader(abs_path: str, language: str, lmap: Any) -> FactsStart:
    """``fn_node -> (tally, finish)``: an empty tally the CCN walk fills, and
    the call that reads :class:`FunctionFacts` off it once it is filled.

    A failure leaves the function's facts ``None`` (not computed) and is
    logged with the file, since a stored row then says nothing about it.
    Deferred import: the dataflow package imports this one.
    """
    from ..dataflow import body_tally, function_facts, get_defuse_dialect

    dialect = get_defuse_dialect(language)

    def failed(exc: Exception) -> None:
        log.warning("function_facts_failed", path=abs_path, error=str(exc))

    def start(fn_node: Node) -> tuple[BodyTally | None, Callable[[], FunctionFacts | None]]:
        try:
            receiver = dialect.receiver(fn_node, lmap) if dialect is not None else None
            tally = body_tally(fn_node, lmap, receiver)
        except Exception as exc:
            failed(exc)
            return None, lambda: None

        def finish() -> FunctionFacts | None:
            try:
                return function_facts(fn_node, lmap, receiver, tally)
            except Exception as exc:
                failed(exc)
                return None

        return tally, finish

    return start


def walk_file_complexity(
    abs_path: str,
    language: str,
    source: bytes,
) -> list[FunctionComplexity]:
    """Backward-compatible wrapper: returns only per-function metrics.

    Prefer ``walk_file`` when class-level metrics are also needed.
    """
    return walk_file(abs_path, language, source).functions


# Idiomatic Rust unit tests live in a ``#[cfg(test)] mod tests`` block inside
# the source file itself, so there is no separate test file to pair against.
# A cheap substring scan over the file head is enough to recognize them —
# ``#[cfg(test)]`` gates the test module; ``#[test]`` marks each test fn.
_RUST_INLINE_TEST_MARKERS = (b"#[cfg(test)]", b"#[test]")


def _detect_inline_tests(source: bytes, language: str) -> bool:
    """True when *source* carries co-located tests the filename can't reveal.

    Currently Rust-only. Pure substring scan (no extra parse); returns False
    for every other language, so it can only ever clear a false ``untested``
    flag, never create a finding.
    """
    if language != "rust":
        return False
    return any(marker in source for marker in _RUST_INLINE_TEST_MARKERS)
