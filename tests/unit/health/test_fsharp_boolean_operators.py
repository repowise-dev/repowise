"""F# ``&&`` / ``||`` add a decision point, so complex_conditional can fire."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

_SRC = """
module M

let check a b c d =
    if a && b || c && not d then 1 else 2

let pick a b =
    if a then (if a || b then 1 else 2) else 3

let plain x = x |> List.filter (fun y -> y > 1)
"""


def _fns() -> dict:
    fcx = walk_file("/tmp/sample.fs", "fsharp", _SRC.encode())
    if not fcx.functions:
        pytest.skip("fsharp grammar unavailable")
    return {fc.name: fc for fc in fcx.functions}


def test_fsharp_boolean_operators_count() -> None:
    fn = _fns()["check"]
    assert fn.ccn == 5
    (condition,) = fn.complex_conditions
    assert condition.operator_count == 3
    assert not condition.flat_chain


def test_fsharp_condition_is_the_guard_not_the_arms() -> None:
    conditions = _fns()["pick"].complex_conditions
    # The outer ``if a`` has no operator; the ``||`` belongs to the inner one.
    assert [c.operator_count for c in conditions] == [1]


def test_fsharp_pipe_is_not_a_decision() -> None:
    assert _fns()["plain"].ccn == 1


_MATCH = """
module M

let route x a b c =
    match x with
    | 1 when a && b -> 1
    | 2 ->
        if a || b || c then 2 else 3
    | _ -> 4
"""


def test_fsharp_match_condition_is_its_when_guards_not_its_arms() -> None:
    fcx = walk_file("/tmp/sample.fs", "fsharp", _MATCH.encode())
    if not fcx.functions:
        pytest.skip("fsharp grammar unavailable")
    conditions = fcx.functions[0].complex_conditions
    assert sorted((c.enclosing_construct, c.operator_count) for c in conditions) == [
        ("case", 1),
        ("if", 2),
    ]


_WHILE = """
module M

let scan a b =
    while a do
        if a || b || not b then ()
"""


def test_fsharp_while_condition_is_its_first_part_not_its_body() -> None:
    fcx = walk_file("/tmp/sample.fs", "fsharp", _WHILE.encode())
    if not fcx.functions:
        pytest.skip("fsharp grammar unavailable")
    conditions = fcx.functions[0].complex_conditions
    assert [(c.enclosing_construct, c.operator_count) for c in conditions] == [("if", 2)]
