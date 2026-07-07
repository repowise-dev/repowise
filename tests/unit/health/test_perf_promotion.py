"""Unit tests for the advisory -> asserted perf promotion (loop-carried proof).

Best-effort like the other dataflow tests: each skips when the Python tree-sitter
pack is missing rather than failing. The proof must be SOUND in one direction:
it may only ever refuse to promote a genuinely carried loop; it must never
promote one.
"""

from __future__ import annotations

import textwrap

import pytest

from repowise.core.analysis.health.complexity.ast_utils import _collect_function_nodes
from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import analyze_function
from repowise.core.analysis.health.perf.promotion import _loop_iterations_independent


def _require_python() -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter language pack missing for python")
    if _get_language("python") is None:
        pytest.skip("tree-sitter language pack missing for python")


def _independent(src: str, marker: str) -> bool:
    """Analyze the first function in *src* and run the proof at *marker*'s line."""
    _require_python()
    from tree_sitter import Parser

    from repowise.core.ingestion.parser import _get_language

    body = textwrap.dedent(src)
    lmap = get_language_map("python")
    parser = Parser(_get_language("python"))
    tree = parser.parse(body.encode())
    nodes = _collect_function_nodes(tree.root_node, lmap)
    assert nodes, "no function node parsed"
    analyzed = analyze_function(nodes[0], "python", lmap)
    assert analyzed is not None, "analysis returned None"
    cfg, def_use, reaching = analyzed
    line = next(i for i, ln in enumerate(body.splitlines(), start=1) if marker in ln)
    return _loop_iterations_independent(cfg, def_use, reaching, line)


# -- provably independent (should promote) --------------------------------


def test_for_loop_append_is_independent():
    assert _independent(
        """
        async def f(items):
            results = []
            for item in items:
                r = await fetch(item.id)  # HIT
                results.append(r)
            return results
        """,
        "HIT",
    )


def test_range_index_for_loop_is_independent():
    assert _independent(
        """
        async def f(urls):
            out = []
            for i in range(len(urls)):
                r = await fetch(urls[i])  # HIT
                out.append(r)
            return out
        """,
        "HIT",
    )


def test_pre_loop_invariant_read_is_independent():
    assert _independent(
        """
        async def f(items, base):
            prefix = compute(base)
            for item in items:
                r = await fetch(prefix, item)  # HIT
                store(r)
        """,
        "HIT",
    )


def test_compute_then_use_same_iteration_is_independent():
    assert _independent(
        """
        async def f(items):
            for item in items:
                key = derive(item)
                r = await fetch(key)  # HIT
                emit(r)
        """,
        "HIT",
    )


# -- provably carried (must NOT promote) ----------------------------------


def test_accumulator_is_carried():
    assert not _independent(
        """
        async def f(items):
            acc = 0
            for item in items:
                acc = acc + await fetch(item)  # HIT
            return acc
        """,
        "HIT",
    )


def test_augmented_accumulator_is_carried():
    assert not _independent(
        """
        async def f(items):
            total = []
            for item in items:
                r = await fetch(item)  # HIT
                total += [r]
                use_running(total, r)
            return total
        """,
        "HIT",
    )


def test_while_manual_increment_is_carried():
    assert not _independent(
        """
        async def f(n):
            i = 0
            while i < n:
                r = await fetch(i)  # HIT
                i = i + 1
            return r
        """,
        "HIT",
    )


def test_conditional_definition_is_carried():
    # ``prev`` is defined only on the then-branch, but read unconditionally, so a
    # later iteration can read an earlier iteration's ``prev``.
    assert not _independent(
        """
        async def f(items):
            prev = None
            for item in items:
                r = await fetch(item, prev)  # HIT
                if item.ok:
                    prev = r
        """,
        "HIT",
    )


def test_running_dependency_across_iterations_is_carried():
    assert not _independent(
        """
        async def f(items):
            cursor = start()
            for item in items:
                page = await fetch(cursor)  # HIT
                cursor = page.next
        """,
        "HIT",
    )


# -- degrade-to-silence ---------------------------------------------------


def test_line_outside_any_loop_is_not_independent():
    # A line that sits in no loop cannot be proven -> refuse (advisory stays).
    assert not _independent(
        """
        async def f(x):
            r = await fetch(x)  # HIT
            return r
        """,
        "HIT",
    )
