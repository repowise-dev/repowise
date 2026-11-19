"""Complexity-walker coverage for Tcl procs.

Tcl gets proc-level complexity only (no class metrics, no perf/dataflow
dialect). ``if`` / ``elseif`` / ``foreach`` / ``while`` and the ``expr``
ternary are grammar nodes; ``for``, ``foreach_in_collection``, ``lmap``,
``dict for`` and ``switch`` are generic commands mapped by ``command_kind``.
A bare ``catch`` adds nothing: the ``if`` testing its result is the branch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.health.complexity.walker import walk_file

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "lang_samples" / "tcl"


def _fn(body: str):
    fc = walk_file("t.tcl", "tcl", f"proc f {{a b}} {{\n  {body}\n}}\n".encode())
    assert fc.functions, "expected one proc"
    return fc.functions[0]


def _ccn(body: str) -> int:
    return _fn(body).ccn


class TestBranches:
    def test_no_branches(self) -> None:
        assert _ccn("puts hi") == 1

    def test_if_elseif_else(self) -> None:
        assert _ccn("if {$a} { x } elseif {$b} { y } else { z }") == 3

    def test_elseif_does_not_nest(self) -> None:
        assert _fn("if {$a} { x } elseif {$b} { y } elseif {$c} { z }").max_nesting == 1

    def test_boolean_operators(self) -> None:
        assert _ccn("if {$a && $b || $c} { x }") == 4

    def test_ternary(self) -> None:
        assert _ccn("set x [expr {$a ? 1 : 2}]") == 2


class TestLoops:
    @pytest.mark.parametrize(
        "body",
        [
            "foreach i $a { x }",
            "while {$a} { incr a -1 }",
            "for {set i 0} {$i < 3} {incr i} { x }",
            "foreach_in_collection c [get_cells *] { x }",
            "lmap i $a { x }",
            "dict for {k v} $d { x }",
        ],
    )
    def test_each_loop_counts_once(self, body: str) -> None:
        assert _ccn(body) == 2

    def test_dict_get_is_not_a_loop(self) -> None:
        assert _ccn("dict get $d k") == 1

    def test_loops_nest(self) -> None:
        assert _fn("foreach i $a { if {$i} { while {$b} { x } } }").max_nesting == 3


class TestSwitch:
    def test_each_arm_counts(self) -> None:
        assert _ccn("switch -- $a { x { p 1 } y { p 2 } default { p 3 } }") == 4

    def test_multiline_matches_one_line(self) -> None:
        body = "switch -- $a {\n    x { p 1 }\n    y { p 2 }\n    default { p 3 }\n  }"
        assert _ccn(body) == 4

    def test_fall_through_arms_count_once(self) -> None:
        assert _ccn("switch -- $a {\n    x -\n    y { p 2 }\n    default { p 3 }\n  }") == 3

    def test_arm_body_nests_under_switch(self) -> None:
        fn = _fn("switch -- $a {\n    x { if {$b} { p 1 } }\n    default { p 3 }\n  }")
        assert (fn.ccn, fn.max_nesting) == (4, 2)

    def test_a_pattern_named_like_a_loop_is_only_an_arm(self) -> None:
        fn = _fn("switch -- $a {for {puts ok}}")
        assert (fn.ccn, fn.max_nesting) == (2, 1)

    def test_separate_word_arms_count_as_the_braced_form_does(self) -> None:
        assert _ccn("switch $a x {p 1} y {p 2}") == _ccn("switch $a {x {p 1} y {p 2}}") == 3

    def test_keyword_patterns_keep_their_arms(self) -> None:
        body = (
            "switch -- $op {\n    get { on_get }\n    set { on_set }\n    default { on_other }\n  }"
        )
        assert _ccn(body) == 4

    def test_a_proc_pattern_is_no_function(self) -> None:
        src = b"proc d {c} {\n  switch $c {\n    run { go }\n    proc { helper }\n  }\n}\n"
        assert [f.name for f in walk_file("t.tcl", "tcl", src).functions] == ["d"]

    def test_bare_word_arm_bodies_count(self) -> None:
        assert _ccn("switch $a x p y q") == _ccn("switch $a x {p} y {q}") == 3
        assert _ccn("switch $a {x p y q}") == 3

    def test_separate_word_fall_through_counts_once(self) -> None:
        assert _ccn("switch $a x - y {p 2} default {p 3}") == 3


class TestErrors:
    def test_bare_catch_adds_nothing(self) -> None:
        assert _ccn("catch {g} err") == 1

    def test_if_on_catch_is_the_branch(self) -> None:
        assert _ccn("if {[catch {g} err]} { puts $err }") == 2


class TestWalkerRobustness:
    def test_script_without_procs(self) -> None:
        assert walk_file("t.tcl", "tcl", b"set x 1\nif {$x} { puts y }\n").functions == []

    def test_namespaced_name_as_written(self) -> None:
        fc = walk_file("t.tcl", "tcl", b"namespace eval ::ns {\n proc ::ns::g {} {}\n}\n")
        assert [f.name for f in fc.functions] == ["::ns::g"]

    def test_fixture(self) -> None:
        src = (_FIXTURES / "control_flow.tcl").read_bytes()
        fc = walk_file("lib/sample/control_flow.tcl", "tcl", src)
        assert {f.name: f.ccn for f in fc.functions} == {
            "::sample::classify": 5,
            "dispatch": 7,
            "loops": 4,
        }
