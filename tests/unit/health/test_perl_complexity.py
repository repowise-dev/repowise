"""Complexity-walker coverage for Perl subs.

Perl gets sub-level complexity only (no class metrics, no perf/dataflow
dialect). ``&&`` / ``||`` and ``and`` / ``or`` count through the operator
token; statement modifiers (``return if $x``) are flat decision points.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.health.complexity.walker import walk_file

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "lang_samples" / "perl"


def _fn(body: str):
    src = f"sub f {{\n  {body}\n}}\n".encode()
    fc = walk_file("t.pl", "perl", src)
    assert fc.functions, "expected one sub"
    return fc.functions[0]


def _ccn(body: str) -> int:
    return _fn(body).ccn


class TestBooleanOperators:
    def test_no_operators(self) -> None:
        assert _ccn("print 'hi';") == 1

    def test_or_die_guard(self) -> None:
        assert _ccn("open(my $fh, '<', $f) or die 'x';") == 2

    def test_symbolic_chain(self) -> None:
        assert _ccn("my $ok = $a && $b || $c;") == 3

    def test_arithmetic_binary_is_not_a_decision(self) -> None:
        assert _ccn("my $x = $a + $b * $c;") == 1


class TestControlFlow:
    def test_if_elsif_else(self) -> None:
        assert _ccn("if ($a) { 1 } elsif ($b) { 2 } else { 3 }") == 3

    def test_unless(self) -> None:
        assert _ccn("unless ($a) { 1 }") == 2

    def test_ternary(self) -> None:
        assert _ccn("my $x = $a ? 1 : 2;") == 2

    def test_loops(self) -> None:
        assert _ccn("for my $i (@l) { 1 }") == 2
        assert _ccn("foreach my $i (@l) { 1 }") == 2
        assert _ccn("for (my $i = 0; $i < 3; $i++) { 1 }") == 2
        assert _ccn("while ($a) { 1 }") == 2
        assert _ccn("until ($a) { 1 }") == 2


class TestStatementModifiers:
    def test_postfix_forms_count(self) -> None:
        assert _ccn("return if $a;") == 2
        assert _ccn("return unless $a;") == 2
        assert _ccn("print for @l;") == 2
        assert _ccn("$i++ while $i < 3;") == 2

    def test_postfix_forms_do_not_nest(self) -> None:
        fn = _fn("return if $a;\n  return unless $b;")
        assert fn.max_nesting == 0

    def test_block_if_nests(self) -> None:
        assert _fn("if ($a) { if ($b) { 1 } }").max_nesting == 2


class TestWalkerRobustness:
    def test_script_without_subs(self) -> None:
        fc = walk_file("t.pl", "perl", b"#!/usr/bin/perl\nprint 'hi' if $x;\n")
        assert fc.functions == []

    def test_anonymous_sub_rolls_into_enclosing(self) -> None:
        assert _ccn("my $cb = sub { return $_[0] ? 1 : 2 };") == 2

    def test_fixture(self) -> None:
        src = (_FIXTURES / "control_flow.pm").read_bytes()
        fc = walk_file("lib/Sample/ControlFlow.pm", "perl", src)
        assert {f.name: f.ccn for f in fc.functions} == {
            "classify": 4,
            "guards": 5,
            "loops": 4,
        }
