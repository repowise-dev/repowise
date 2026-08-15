"""The lists P9 ranked stay ranked (the architecture half of P9).

An architecture check, not a behaviour one. The behaviour lives in
``tests/unit/generation/test_ranked_truncation.py`` and
``tests/unit/server/mcp/test_used_by_ranking.py``, which catch a *wrong*
order; this catches the ranking being **deleted** — the sort disappearing in a
refactor and leaving the cut behind, which is precisely how all six sites got
here. Every one of them was executed by tests that asserted nothing about it,
so removing the sort would have been green.

Why a per-site registry rather than "find every unranked slice": the general
question is undecidable from the AST. A list can be ranked three frames up, by
a SQL ``ORDER BY``, or by a caller's contract, and a checker that flagged every
``[:N]`` would drown the real ones in the ~200 legitimate slices under
``packages/`` — string truncation, batch sizes, log samples. The census that
produced this list is in ``local-stash/structural-debt/PROGRESS.md``; it found
the sites by reading them, and this file pins the ones that were fixed.

Ceiling, stated because it is the same one the sibling guards carry: this
proves a ranking *call* is present, not that it ranks on the right key. A sort
on the wrong field passes here and fails the behaviour tests.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_PACKAGES = pathlib.Path(__file__).resolve().parents[2] / "packages"

#: ``relative module path -> {function name: what it must rank before cutting}``.
#: The reason is not decoration: it is what a future reader needs to decide
#: whether their refactor is allowed to drop the sort. This list only grows
#: when a new cut is added, and an entry only leaves when the cut does.
_RANKED_CUTS: dict[str, dict[str, str]] = {
    "core/src/repowise/core/generation/context/graph_intelligence.py": {
        "extract_call_graph": "confidence, before MAX_CALL_ENTRIES",
        "extract_heritage": "confidence, before MAX_HERITAGE_ENTRIES",
    },
    "core/src/repowise/core/generation/context/assembler.py": {
        "assemble_module_page": "file PageRank, before _MAX_KEY_CLASSES",
    },
    "core/src/repowise/core/persistence/crud/graph.py": {
        "get_graph_edges_for_node": "edge confidence, before `limit`",
    },
    "core/src/repowise/core/analysis/pr_blast.py": {
        "analyze_files": "path order on the affected set, before test_gaps is cut to 3",
    },
    "core/src/repowise/core/generation/onboarding/subkinds/active_landscape.py": {
        "_build": "the churn ranking, before the cut to 15",
    },
    "server/src/repowise/server/mcp_server/tool_context/targets.py": {
        "_resolve_one_target": "source PageRank, before _MAX_USED_BY",
    },
}

#: What counts as ranking. ``order_by`` is the SQL arm; the rest are Python.
_RANKING_CALLS = frozenset({"sorted", "sort", "order_by", "most_common", "nlargest"})


def _module(rel: str) -> ast.Module:
    path = _PACKAGES / rel
    assert path.is_file(), f"{rel} moved; update _RANKED_CUTS"
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone; update _RANKED_CUTS")


def _ranks(node: ast.AST) -> bool:
    """True when *node*'s body calls something that orders a sequence.

    Walks the whole subtree, so a sort inside a nested helper or comprehension
    still counts — the sibling guard learned that lesson with copies hidden in
    class bodies.
    """
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        func = inner.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name in _RANKING_CALLS:
            return True
    return False


@pytest.mark.parametrize(
    ("rel", "func", "reason"),
    [(rel, f, r) for rel, funcs in _RANKED_CUTS.items() for f, r in funcs.items()],
)
def test_the_cut_is_still_ranked(rel: str, func: str, reason: str) -> None:
    assert _ranks(_function(_module(rel), func)), (
        f"{rel}::{func} truncates a displayed list and no longer ranks it. "
        f"It must rank by {reason}. Restore the sort, or if the cut itself is "
        "gone, drop the entry from _RANKED_CUTS."
    )


def test_the_graph_extractors_cut_at_a_named_constant() -> None:
    """No bare ``[:15]`` back at the return.

    The literals are what made the two cuts invisible: they read as an
    implementation detail rather than as a contract a test could hold.
    """
    text = (
        _PACKAGES / "core/src/repowise/core/generation/context/graph_intelligence.py"
    ).read_text(encoding="utf-8")
    assert "MAX_CALL_ENTRIES = 15" in text
    assert "MAX_HERITAGE_ENTRIES = 10" in text
    assert "[:15]" not in text
    assert "[:10]" not in text


def test_the_check_fails_on_an_unranked_cut() -> None:
    """The guard itself, probed against the shape it exists to catch.

    Written because the sibling guard shipped green against the exact code it
    was meant to outlaw: a check nobody has seen fail is a check nobody has
    tested.
    """
    unranked = ast.parse("def f(rows):\n    return rows[:10]\n")
    ranked = ast.parse("def f(rows):\n    return sorted(rows, key=score)[:10]\n")
    sql_ranked = ast.parse("def f(q):\n    return q.order_by(C.confidence.desc()).limit(50)\n")

    assert not _ranks(_function(unranked, "f"))
    assert _ranks(_function(ranked, "f"))
    assert _ranks(_function(sql_ranked, "f"))
