"""Regression tests for complexity-metric miscounts that produced false flags.

Each test drives the real project functions (``_walk_function_body``,
``_count_nloc``, ``_count_parameters``) against a small parsed fixture — no
metric logic is reimplemented here. Tests skip (rather than fail) when a
tree-sitter language pack is unavailable, matching the sibling walker tests.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity.ast_utils import (
    _collect_function_nodes,
    _count_parameters,
)
from repowise.core.analysis.health.complexity.cyclomatic import _walk_function_body
from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.complexity.nloc import _count_nloc


def _parse(language: str, source: str):
    """Parse *source* and return its function nodes + the language map.

    Skips the test when the tree-sitter grammar for *language* is missing.
    """
    try:
        from tree_sitter import Parser

        from repowise.core.ingestion.parser import _get_language
    except Exception:  # pragma: no cover - depends on optional deps
        pytest.skip("tree-sitter not installed")

    grammar = _get_language(language)
    if grammar is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    lmap = get_language_map(language)
    tree = Parser(grammar).parse(source.encode())
    fns = _collect_function_nodes(tree.root_node, lmap)
    if not fns:
        pytest.skip(f"no functions parsed for {language}")
    return fns, lmap


def _first_body(language: str, source: str):
    fns, lmap = _parse(language, source)
    fn = fns[0]
    body = fn.child_by_field_name("body") or fn
    return body, lmap


def _walk(language: str, source: str):
    body, lmap = _first_body(language, source)
    return _walk_function_body(body, lmap)


# --- M2: bare * / bare / separators are not parameters ---------------------


def test_keyword_only_separator_not_counted_as_param():
    src = (
        "async def embed_top(*, limit=7000, concurrency=8, "
        "batch_size=96, description_only=True):\n    pass\n"
    )
    fns, _ = _parse("python", src)
    assert _count_parameters(fns[0]) == 4


def test_positional_only_separator_not_counted_as_param():
    fns, _ = _parse("python", "def h(a, b, /, c):\n    pass\n")
    assert _count_parameters(fns[0]) == 3


# --- R2: else-if / elif chains are flat, not nested ------------------------

_FLAT_DISPATCH = {
    "python": (
        "def f(x):\n"
        "    if x == 1:\n        return 1\n"
        "    elif x == 2:\n        return 2\n"
        "    elif x == 3:\n        return 3\n"
        "    elif x == 4:\n        return 4\n"
        "    elif x == 5:\n        return 5\n"
        "    return 0\n"
    ),
    "typescript": (
        "function f(x){"
        " if(x==1){return 1;}"
        " else if(x==2){return 2;}"
        " else if(x==3){return 3;}"
        " else if(x==4){return 4;}"
        " else if(x==5){return 5;}"
        " return 0; }"
    ),
    "java": (
        "class C { int f(int x){"
        " if(x==1){return 1;}"
        " else if(x==2){return 2;}"
        " else if(x==3){return 3;}"
        " else if(x==4){return 4;}"
        " else if(x==5){return 5;}"
        " return 0; } }"
    ),
    "go": (
        "func F(x int) int {\n"
        " if x==1 { return 1 } else if x==2 { return 2 }"
        " else if x==3 { return 3 } else if x==4 { return 4 }"
        " else if x==5 { return 5 }\n return 0\n}"
    ),
}


@pytest.mark.parametrize("language", ["python", "typescript", "java", "go"])
def test_flat_elif_chain_is_not_nested(language):
    ccn, max_nesting, _cognitive, _bumps, _conds = _walk(language, _FLAT_DISPATCH[language])
    # A 5-arm flat dispatch is one branch deep, not five.
    assert max_nesting == 1, f"{language}: expected max_nesting 1, got {max_nesting}"
    # The extra branches still count toward CCN (base 1 + 5 arms).
    assert ccn == 6, f"{language}: expected ccn 6, got {ccn}"


def test_genuine_nested_ifs_still_report_depth():
    _ccn, max_nesting, _cog, _b, _c = _walk(
        "python",
        "def g(a, b, c):\n"
        "    if a:\n        if b:\n            if c:\n                return 1\n"
        "    return 0\n",
    )
    assert max_nesting == 3


# --- M4: boolean ops inside a lambda body do not leak into the condition ---


def test_lambda_boolean_ops_excluded_from_condition_count():
    _ccn, _mn, _cog, _b, conds = _walk(
        "python",
        "def f(items, other):\n"
        "    if any(sorted(items, key=lambda x: x.a or x.b or x.c)) and other:\n"
        "        return 1\n"
        "    return 0\n",
    )
    assert conds, "expected a complex-condition record for the if"
    # Only the `and other` operator belongs to the if's own condition; the two
    # `or`s live inside the sort-key lambda.
    assert conds[0].operator_count == 1


# --- M11: comprehension filter clauses contribute CCN ----------------------


def test_comprehension_filter_clauses_count_toward_ccn():
    ccn, _mn, _cog, _b, _c = _walk(
        "python",
        "def f(items):\n    return [x for x in items if x > 0 if x < 100]\n",
    )
    # base path + two independent filter predicates.
    assert ccn == 3


# --- M5: function NLOC excludes docstring / comment-only lines --------------

_LARGE_METHOD_THRESHOLD = 60


def test_docstring_heavy_function_nloc_excludes_docs():
    doc = "\n".join(f"    Rationale line {i}." for i in range(59))
    source = (
        "def f(a, b):\n"
        '    """\n'
        f"{doc}\n"
        '    """\n'
        "    # trailing note\n"
        "    if a:\n"
        "        b = 1\n"
        "    return b\n"
    )
    body, _lmap = _first_body("python", source)
    nloc = _count_nloc(body, source.encode())
    # Only the three real statements count.
    assert nloc == 3
    # And so the function stays well under the large_method threshold despite
    # spanning ~64 physical lines.
    assert nloc < _LARGE_METHOD_THRESHOLD
