"""One descent of a file's tree for every whole-file pass of the walker.

File NLOC, the NLOC of each function and class, the error-handling
anti-patterns, the class nodes, the Rust test-only spans and the perf pass's
I/O import names each used to walk
the whole tree, or a whole subtree per function and class, on their own. This
visits each node once and hands every pass what it reads there.

The stack discipline (pop the last, push the children in order) is the one the
separate passes used, so the order hits and class nodes are found in, which
the output keeps, is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..perf.io_boundaries import _finish_io_names, _io_visit
from .error_handling import _eh_node_kinds, _eh_rust_attr_is_test, _eh_visit
from .languages import LanguageNodeMap
from .models import ErrorHandlingHit
from .nloc import CodeLineIndex, _is_docstring_stmt, _source_lines

if TYPE_CHECKING:
    from tree_sitter import Node

# ``function_item`` / ``mod_item`` / ``impl_item`` are the Rust item kinds a
# ``#[cfg(test)]`` (or ``#[test]`` / ``#[tokio::test]`` / ``#[rstest]``, for a
# bare fn) attribute can gate. ``mod``/``impl`` are containers: their whole
# span is test-only once gated, including any nested fn that carries no
# attribute of its own, the same reason ``#[cfg(test)] mod tests { .. }``
# hides ordinary-looking helper fns from the file-level heuristic.
_RUST_TEST_ITEM_KINDS = frozenset({"function_item", "mod_item", "impl_item"})
_RUST_ATTR_SIBLING_KINDS = frozenset({"attribute_item", "line_comment", "block_comment"})


@dataclass
class FileScan:
    """What the single descent collected for one file."""

    lines: CodeLineIndex
    error_handling_hits: list[ErrorHandlingHit]
    class_nodes: list[Node]
    #: 1-indexed ``(start_line, end_line)`` spans of Rust test-only code.
    rust_test_line_ranges: tuple[tuple[int, int], ...]
    #: ``perf.io_boundaries.collect_io_names`` for the file, or ``{}`` when
    #: the scan was not asked for it.
    io_names: dict[str, str]


def _rust_item_is_test(node: Node) -> bool:
    """True when a Rust item's preceding attributes mark it test-only.

    Uses :func:`repowise.core.analysis.health.complexity.error_handling._eh_rust_attr_is_test`,
    shared with the error-handling pass so both recognize the identical
    attribute grammar.
    """
    sib = node.prev_sibling
    while sib is not None and sib.type in _RUST_ATTR_SIBLING_KINDS:
        if sib.type == "attribute_item" and _eh_rust_attr_is_test(
            (sib.text or b"").decode("utf-8", errors="replace")
        ):
            return True
        sib = sib.prev_sibling
    return False


# Bits of the per-node context the descent carries.
_IN_COMMENT = 1  # under a comment: no NLOC anywhere
_IN_DOCSTRING = 2  # under a comment or docstring: no function / class NLOC
_IN_RUST_TEST = 4  # inside a recorded Rust test span: record no nested one


def scan_file(
    root: Node, language: str, lmap: LanguageNodeMap, source: bytes, *, io_names: bool
) -> FileScan:
    """Walk *root* once and collect every whole-file pass's facts.

    *io_names* asks for the perf pass's import map too; the walker sets it only
    when that pass runs for the language.

    A Rust test span is recorded for an item whose attributes mark it, and not
    again for any item nested inside it: its span already covers them, which
    is what lets ``perf.gated._in_rust_test_range`` silence an undecorated
    helper inside a ``#[cfg(test)] mod``. Rust-only; every other language gets
    ``()``.
    """
    lines = _source_lines(source)
    nonblank = [bool(line.strip()) for line in lines]
    n_lines = len(lines)
    file_rows: set[int] = set()
    code_rows: set[int] = set()
    leaves: list[tuple[int, int, int]] = []
    excluded: list[tuple[int, int]] = []
    hits: list[ErrorHandlingHit] = []
    class_nodes: list[Node] = []
    rust_ranges: list[tuple[int, int]] = []
    io_bound: dict[str, str] = {}
    io_rebound: set[str] = set()

    eh_kinds = _eh_node_kinds(language, lmap)
    class_kinds = lmap.class_kinds
    is_rust = language == "rust"

    stack: list[tuple[Node, int]] = [(root, 0)]
    pop = stack.pop
    push = stack.append
    while stack:
        node, ctx = pop()
        t = node.type
        if io_names:
            _io_visit(node, t, language, io_bound, io_rebound)
        if t in eh_kinds:
            _eh_visit(node, language, lmap, hits)
        if class_kinds and t in class_kinds and node.is_named:
            class_nodes.append(node)
        if (
            is_rust
            and not ctx & _IN_RUST_TEST
            and t in _RUST_TEST_ITEM_KINDS
            and _rust_item_is_test(node)
        ):
            rust_ranges.append((node.start_point[0] + 1, node.end_point[0] + 1))
            ctx |= _IN_RUST_TEST
        if not ctx & _IN_DOCSTRING:
            if "comment" in t:
                ctx |= _IN_COMMENT | _IN_DOCSTRING
                excluded.append((node.start_byte, node.end_byte))
            elif t == "expression_statement" and _is_docstring_stmt(node):
                ctx |= _IN_DOCSTRING
                excluded.append((node.start_byte, node.end_byte))
        elif not ctx & _IN_COMMENT and "comment" in t:
            ctx |= _IN_COMMENT

        children = node.children
        if children:
            for child in children:
                push((child, ctx))
            continue
        if ctx & _IN_COMMENT:
            continue
        start_byte = node.start_byte
        if start_byte >= node.end_byte:
            continue
        first = node.start_point[0]
        last = node.end_point[0]
        in_docstring = ctx & _IN_DOCSTRING
        if first == last:
            # Most leaves sit on one line; skip building a row list for them.
            if first < n_lines and nonblank[first]:
                file_rows.add(first)
                if not in_docstring:
                    code_rows.add(first)
        else:
            rows = [row for row in range(first, min(last, n_lines - 1) + 1) if nonblank[row]]
            file_rows.update(rows)
            if not in_docstring:
                code_rows.update(rows)
        if not in_docstring:
            leaves.append((start_byte, first, last))

    hits.sort(key=lambda h: h.line)
    # Recorded right to left; the spans are disjoint, so reversing restores
    # source order, the order the left-to-right walk this replaced produced.
    rust_ranges.reverse()
    return FileScan(
        lines=CodeLineIndex(nonblank, file_rows, code_rows, leaves, excluded),
        error_handling_hits=hits,
        class_nodes=class_nodes,
        rust_test_line_ranges=tuple(rust_ranges),
        io_names=_finish_io_names(io_bound, io_rebound),
    )
