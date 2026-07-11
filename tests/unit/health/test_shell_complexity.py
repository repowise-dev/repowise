"""Complexity-walker coverage for shell functions.

Shell gets function-level complexity only (no class metrics, no perf/dataflow
dialect). The novel piece is that ``&&`` / ``||`` command lists count toward
CCN via the ``list`` boolean-operator-text node.
"""

from __future__ import annotations

from repowise.core.analysis.health.complexity.walker import walk_file


def _ccn(body: str) -> int:
    src = f"f() {{\n  {body}\n}}\n".encode()
    fc = walk_file("t.sh", "shell", src)
    assert fc.functions, "expected one function"
    return fc.functions[0].ccn


class TestBooleanLists:
    def test_no_operators(self) -> None:
        assert _ccn("echo hi") == 1

    def test_single_or_guard(self) -> None:
        # `cmd || exit 1` is the guard idiom — one decision point.
        assert _ccn("run || exit 1") == 2

    def test_and_or_chain(self) -> None:
        assert _ccn("a && b || c") == 3


class TestControlFlow:
    def test_if_elif(self) -> None:
        assert _ccn("if [[ $x ]]; then echo a; elif [[ $y ]]; then echo b; fi") == 3

    def test_for_loop(self) -> None:
        assert _ccn("for f in *.txt; do echo $f; done") == 2

    def test_c_style_for(self) -> None:
        assert _ccn("for (( i=0; i<3; i++ )); do echo $i; done") == 2

    def test_while_and_until(self) -> None:
        # `until` parses as while_statement in tree-sitter-bash.
        assert _ccn("while true; do break; done") == 2
        assert _ccn("until false; do break; done") == 2

    def test_case_flat_dispatch_counts_once(self) -> None:
        # A case whose arms are all simple single commands is a "flat" match:
        # it counts 1 CCN point for the dispatch, not one per arm.
        assert _ccn("case $x in a) echo A ;; b) echo B ;; esac") == 2

    def test_case_with_nested_branch_is_not_flat(self) -> None:
        # An arm carrying nested control flow makes the case non-flat, so the
        # arms and the nested branch each count.
        nonflat = _ccn("case $x in a) if [[ $y ]]; then echo A; fi ;; b) echo B ;; esac")
        assert nonflat > 2


class TestWalkerRobustness:
    def test_zero_function_file_does_not_crash(self) -> None:
        src = b'#!/bin/sh\necho "hi"\nfor f in *.log; do rm "$f"; done\n'
        fc = walk_file("t.sh", "shell", src)
        assert fc.functions == []

    def test_both_function_forms_walked(self) -> None:
        src = b"foo() {\n  echo a\n}\nfunction bar {\n  echo b\n}\n"
        fc = walk_file("t.sh", "shell", src)
        assert {f.name for f in fc.functions} == {"foo", "bar"}
