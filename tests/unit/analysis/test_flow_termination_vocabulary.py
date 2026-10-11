"""The flow-termination vocabulary must stay true in both directions.

Same enforcement shape as the resolution-origin checks in
``tests/unit/ingestion/test_edge_type_vocabulary.py``: a closed Literal is
only closed while something checks both ends of it.

* **Nothing returns an undeclared reason** — every string ``classify_termination``
  can return is a member of the ``FlowTermination`` Literal.
* **Nothing declares a reason no branch returns** — a word with no producer is
  a value consumers can filter on and never match, which is indistinguishable
  from a filter that works.

Ceiling: this is an AST read of the ``return "..."`` literals inside one
function. A reason assembled at runtime would slip through, so it is not a
proof that no undeclared reason can be produced — it is a proof that the one
function allowed to name them stays inside the vocabulary.
"""

from __future__ import annotations

import ast
import pathlib

from repowise.core.analysis.execution_flows import (
    FLOW_TERMINATION_VALUES,
    classify_termination,
)

_SOURCE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "packages/core/src/repowise/core/analysis/execution_flows.py"
)


def _returned_reasons() -> set[str]:
    """Every string literal ``classify_termination`` can return."""
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "classify_termination":
            return {
                r.value.value
                for r in ast.walk(node)
                if isinstance(r, ast.Return)
                and isinstance(r.value, ast.Constant)
                and isinstance(r.value.value, str)
            }
    raise AssertionError("classify_termination not found — the AST scan has rotted")


def test_nothing_returns_an_undeclared_termination() -> None:
    returned = _returned_reasons()
    assert returned, "found no literal returns — the AST scan has rotted"
    assert not (returned - FLOW_TERMINATION_VALUES), (
        "termination reason(s) returned but not declared: "
        f"{sorted(returned - FLOW_TERMINATION_VALUES)}\n"
        "Add them to FlowTermination in repowise.core.analysis.execution_flows."
        " An undeclared reason reaches consumers as an unrecognised string."
    )


def test_every_declared_termination_has_a_producer() -> None:
    orphans = FLOW_TERMINATION_VALUES - _returned_reasons()
    assert not orphans, (
        f"termination reason(s) declared with no branch returning them: {sorted(orphans)}"
    )


def test_the_hop_budget_outranks_everything_about_the_successors() -> None:
    """A walk that spent its budget knows nothing about what lay beyond it.

    Pinned because the counts passed alongside describe the *last* iteration,
    which found a successor — reading them would name a cause that did not
    stop anything.
    """
    assert (
        classify_termination(
            hops_taken=8,
            max_depth=8,
            revisited=3,
            low_confidence=2,
            excluded=1,
            truncated=True,
        )
        == "depth_limit"
    )


def test_a_cut_callee_set_outranks_a_claim_about_all_successors() -> None:
    """``cycle``/``confidence_filtered``/``excluded_target`` each say *every*
    successor was one thing, which a cut set cannot support."""
    assert (
        classify_termination(
            hops_taken=1,
            max_depth=8,
            revisited=4,
            low_confidence=0,
            excluded=0,
            truncated=True,
        )
        == "callees_truncated"
    )


def test_no_successors_at_all_reports_no_callees() -> None:
    assert (
        classify_termination(
            hops_taken=2, max_depth=8, revisited=0, low_confidence=0, excluded=0
        )
        == "no_callees"
    )


def test_each_drop_reason_is_reachable_on_its_own() -> None:
    kw = {"hops_taken": 1, "max_depth": 8, "revisited": 0, "low_confidence": 0, "excluded": 0}
    assert classify_termination(**{**kw, "revisited": 1}) == "cycle"
    assert classify_termination(**{**kw, "low_confidence": 1}) == "confidence_filtered"
    assert classify_termination(**{**kw, "excluded": 1}) == "excluded_target"
