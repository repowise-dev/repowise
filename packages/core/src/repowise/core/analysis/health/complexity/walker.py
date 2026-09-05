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

from typing import TYPE_CHECKING

import structlog

from .assertions import _collect_assertion_blocks
from .ast_utils import (
    _collect_function_nodes,
    _count_parameters,
    _find_function_entry_name,
)
from .class_analysis import _collect_classes
from .cyclomatic import _walk_function_body
from .error_handling import _collect_error_handling, _eh_rust_attr_is_test
from .languages import get_language_map

if TYPE_CHECKING:
    from tree_sitter import Node

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
from .nloc import _count_file_nloc, _count_file_nloc_tree, _count_nloc
from .perf_walk import _collect_perf_hits

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
        has_inline_tests=_detect_inline_tests(source, language),
        rust_test_line_ranges=_rust_test_line_ranges(tree.root_node, language),
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


# ``function_item`` / ``mod_item`` / ``impl_item`` are the Rust item kinds a
# ``#[cfg(test)]`` (or ``#[test]`` / ``#[tokio::test]`` / ``#[rstest]``, for a
# bare fn) attribute can gate. ``mod``/``impl`` are containers: their whole
# span is test-only once gated, including any nested fn that carries no
# attribute of its own — the same reason ``#[cfg(test)] mod tests { .. }``
# hides ordinary-looking helper fns from the file-level heuristic above.
_RUST_TEST_ITEM_KINDS = ("function_item", "mod_item", "impl_item")


def _rust_test_line_ranges(root: Node, language: str) -> tuple[tuple[int, int], ...]:
    """1-indexed ``(start_line, end_line)`` spans of Rust test-only code.

    Walks the SAME tree ``walk_file`` already parsed (no extra parse). Marks a
    ``mod_item`` / ``impl_item`` / ``function_item`` whose immediately
    preceding attribute siblings include a test marker
    (:func:`repowise.core.analysis.health.complexity.error_handling._eh_rust_attr_is_test`,
    shared with the error-handling walker so both passes recognize the
    identical attribute grammar), and does not descend into a marked node —
    its span already covers everything nested inside. Rust-only; every other
    language gets ``()``, so :func:`repowise.core.analysis.health.perf.gated._in_rust_test_range`
    is a no-op for it.
    """
    if language != "rust":
        return ()

    ranges: list[tuple[int, int]] = []

    def _text(node: Node) -> str:
        return (node.text or b"").decode("utf-8", errors="replace")

    def _is_marked(node: Node) -> bool:
        sib = node.prev_sibling
        while sib is not None and sib.type in (
            "attribute_item",
            "line_comment",
            "block_comment",
        ):
            if sib.type == "attribute_item" and _eh_rust_attr_is_test(_text(sib)):
                return True
            sib = sib.prev_sibling
        return False

    def _walk(node: Node) -> None:
        if node.type in _RUST_TEST_ITEM_KINDS and _is_marked(node):
            ranges.append((node.start_point[0] + 1, node.end_point[0] + 1))
            return  # span already covers every nested item; don't descend
        for child in node.children:
            _walk(child)

    _walk(root)
    return tuple(ranges)
