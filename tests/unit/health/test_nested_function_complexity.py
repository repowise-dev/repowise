"""A function defined inside another function is measured, once, as its own entry.

Before #3281 the collector stopped at the first function it met and the outer
function's walk skipped any function-kind node below it, so a nested ``def``,
a function expression, a local function or a method of a local or anonymous
class was counted nowhere: not in an entry of its own and not in its parent's.

Every nested body here is the same three nested ``if``s, which on its own is
CCN 4 and nesting 3, and every outer function is otherwise empty, CCN 1 and
nesting 0. Lambdas are the control: they belong to the function that holds
them and get no entry, before and after.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.complexity.ast_utils import _collect_function_nodes
from repowise.core.analysis.health.complexity.languages import get_language_map

_C3 = "if (a) { if (b) { if (c) { } } }"
_RS3 = "if a { if b { if c { } } }"


def _walk(path: str, language: str, source: str):
    try:
        from repowise.core.ingestion.parser import _get_language, grammar_tag_for
    except Exception:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    if _get_language(grammar_tag_for(language, path)) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return walk_file(path, language, source.encode())


def _shape(fcx) -> dict[str, tuple[int, int]]:
    """``name -> (ccn, max_nesting)``; fails on a name reported twice."""
    names = [fc.name for fc in fcx.functions]
    assert len(names) == len(set(names)), names
    return {fc.name: (fc.ccn, fc.max_nesting) for fc in fcx.functions}


# (id, path, language, source, name of the outer entry, name of the nested entry)
_NESTED = [
    (
        "python-def",
        "a.py",
        "python",
        "def outer():\n"
        "    def inner():\n"
        "        if a:\n"
        "            if b:\n"
        "                if c:\n"
        "                    pass\n"
        "    return inner\n",
        "outer",
        "inner",
    ),
    (
        "python-async-def",
        "a.py",
        "python",
        "async def outer():\n"
        "    async def inner():\n"
        "        if a:\n"
        "            if b:\n"
        "                if c:\n"
        "                    pass\n"
        "    return inner\n",
        "outer",
        "inner",
    ),
    (
        "python-local-class-method",
        "a.py",
        "python",
        "def outer():\n"
        "    class K:\n"
        "        def m(self):\n"
        "            if a:\n"
        "                if b:\n"
        "                    if c:\n"
        "                        pass\n"
        "    return K\n",
        "outer",
        "m",
    ),
    (
        "javascript-declaration",
        "a.js",
        "javascript",
        f"function outer(){{ function inner(){{ {_C3} }} }}\n",
        "outer",
        "inner",
    ),
    (
        "javascript-function-expression",
        "a.js",
        "javascript",
        f"function outer(){{ const h = function(){{ {_C3} }}; }}\n",
        "outer",
        "<anonymous>",
    ),
    (
        "javascript-generator",
        "a.js",
        "javascript",
        f"function outer(){{ function* g(){{ {_C3} }} }}\n",
        "outer",
        "g",
    ),
    (
        "javascript-object-method",
        "a.js",
        "javascript",
        f"function outer(){{ return {{ m(){{ {_C3} }} }}; }}\n",
        "outer",
        "m",
    ),
    (
        "javascript-local-class-method",
        "a.js",
        "javascript",
        f"function outer(){{ class K {{ m(){{ {_C3} }} }} return K; }}\n",
        "outer",
        "m",
    ),
    (
        "typescript-declaration",
        "a.ts",
        "typescript",
        f"function outer(): void {{ function inner(): void {{ {_C3} }} }}\n",
        "outer",
        "inner",
    ),
    (
        "typescript-inside-a-method",
        "a.ts",
        "typescript",
        f"class C {{ m() {{ function inner() {{ {_C3} }} }} }}\n",
        "m",
        "inner",
    ),
    (
        "java-anonymous-class",
        "A.java",
        "java",
        "class A { void outer() { Runnable r = new Runnable() {"
        f" public void run() {{ {_C3} }} }}; }} }}\n",
        "outer",
        "run",
    ),
    (
        "java-local-class",
        "A.java",
        "java",
        f"class A {{ void outer() {{ class L {{ void m() {{ {_C3} }} }} }} }}\n",
        "outer",
        "m",
    ),
    (
        "rust-fn-in-fn",
        "a.rs",
        "rust",
        f"fn outer() {{\n    fn inner() {{\n        {_RS3}\n    }}\n}}\n",
        "outer",
        "inner",
    ),
    (
        "rust-impl-in-fn",
        "a.rs",
        "rust",
        f"fn outer() {{\n    struct S;\n    impl S {{ fn m(&self) {{ {_RS3} }} }}\n}}\n",
        "outer",
        "m",
    ),
    (
        "kotlin-local-fun",
        "a.kt",
        "kotlin",
        f"fun outer() {{\n    fun inner() {{\n        {_C3}\n    }}\n}}\n",
        "outer",
        "inner",
    ),
    (
        "kotlin-object-expression",
        "a.kt",
        "kotlin",
        "fun outer() {\n"
        "    val r = object : Runnable {\n"
        f"        override fun run() {{ {_C3} }}\n"
        "    }\n"
        "}\n",
        "outer",
        "run",
    ),
    (
        "cpp-local-class",
        "a.cpp",
        "cpp",
        f"void outer() {{ struct L {{ void m() {{ {_C3} }} }}; }}\n",
        "outer",
        "m",
    ),
    (
        "c-nested-function",
        "a.c",
        "c",
        f"void outer() {{ void inner() {{ {_C3} }} }}\n",
        "outer",
        "inner",
    ),
    (
        "csharp-local-function",
        "A.cs",
        "csharp",
        f"class A {{ void Outer() {{ void Inner() {{ {_C3} }} }} }}\n",
        "Outer",
        "Inner",
    ),
    (
        "dart-local-function",
        "a.dart",
        "dart",
        f"void outer() {{ void inner() {{ {_C3} }} }}\n",
        "outer",
        "inner",
    ),
    (
        "scala-nested-def",
        "a.scala",
        "scala",
        "object O {\n"
        "  def outer(): Unit = {\n"
        "    def inner(): Unit = {\n"
        f"      {_C3}\n"
        "    }\n"
        "  }\n"
        "}\n",
        "outer",
        "inner",
    ),
    (
        "ruby-nested-def",
        "a.rb",
        "ruby",
        "def outer\n"
        "  def inner\n"
        "    if a\n"
        "      if b\n"
        "        if c\n"
        "        end\n"
        "      end\n"
        "    end\n"
        "  end\n"
        "end\n",
        "outer",
        "inner",
    ),
    (
        "shell-nested-function",
        "a.sh",
        "shell",
        "outer() {\n  inner() {\n    if a; then if b; then if c; then :; fi; fi; fi\n  }\n}\n",
        "outer",
        "inner",
    ),
    (
        "pascal-nested-procedure",
        "a.pas",
        "pascal",
        "unit a;\n"
        "interface\n"
        "implementation\n"
        "procedure Outer;\n"
        "  procedure Inner;\n"
        "  begin\n"
        "    if a then if b then if c then x := 1;\n"
        "  end;\n"
        "begin\n"
        "end;\n"
        "end.\n",
        "Outer",
        "Inner",
    ),
    (
        "php-nested-function",
        "a.php",
        "php",
        f"<?php\nfunction outer() {{ function inner() {{ {_C3} }} }}\n",
        "outer",
        "inner",
    ),
    (
        "php-anonymous-class",
        "a.php",
        "php",
        f"<?php\nfunction outer() {{ return new class {{ function m() {{ {_C3} }} }}; }}\n",
        "outer",
        "m",
    ),
]


@pytest.mark.parametrize(
    ("path", "language", "source", "outer", "nested"),
    [pytest.param(*row[1:], id=row[0]) for row in _NESTED],
)
def test_a_nested_function_is_an_entry_of_its_own(path, language, source, outer, nested):
    shape = _shape(_walk(path, language, source))

    assert shape == {outer: (1, 0), nested: (4, 3)}


# (id, path, language, source, name of the one entry)
_LAMBDAS = [
    (
        "javascript-arrow",
        "a.js",
        "javascript",
        f"function outer(){{ const h = () => {{ {_C3} }}; }}\n",
        "outer",
    ),
    (
        "python-lambda",
        "a.py",
        "python",
        # Each conditional sits in its parent's consequence, so it nests; an
        # else-arm chain reads flat like an elif dispatch (#3338).
        "def outer():\n    return lambda x: ((1 if b else 2) if a else 3) if x else 4\n",
        "outer",
    ),
    (
        "go-func-literal",
        "a.go",
        "go",
        f"package m\nfunc outer() {{\n\th := func() {{\n\t\t{_RS3}\n\t}}\n\t_ = h\n}}\n",
        "outer",
    ),
    (
        "java-lambda",
        "A.java",
        "java",
        f"class A {{ void outer() {{ Runnable r = () -> {{ {_C3} }}; }} }}\n",
        "outer",
    ),
    (
        "rust-closure",
        "a.rs",
        "rust",
        f"fn outer() {{\n    let h = || {{ {_RS3} }};\n}}\n",
        "outer",
    ),
    (
        "kotlin-lambda",
        "a.kt",
        "kotlin",
        f"fun outer() {{\n    val h = {{ {_C3} }}\n}}\n",
        "outer",
    ),
    (
        "cpp-lambda",
        "a.cpp",
        "cpp",
        f"void outer() {{ auto h = [&]() {{ {_C3} }}; }}\n",
        "outer",
    ),
    (
        "csharp-lambda",
        "A.cs",
        "csharp",
        f"class A {{ void Outer() {{ System.Action h = () => {{ {_C3} }}; }} }}\n",
        "Outer",
    ),
    (
        "dart-closure",
        "a.dart",
        "dart",
        f"void outer() {{ var h = () {{ {_C3} }}; }}\n",
        "outer",
    ),
    (
        "php-closure",
        "a.php",
        "php",
        f"<?php\nfunction outer() {{ $h = function() {{ {_C3} }}; }}\n",
        "outer",
    ),
]


@pytest.mark.parametrize(
    ("path", "language", "source", "outer"),
    [pytest.param(*row[1:], id=row[0]) for row in _LAMBDAS],
)
def test_a_lambda_still_belongs_to_the_function_that_holds_it(path, language, source, outer):
    shape = _shape(_walk(path, language, source))

    assert shape == {outer: (4, 3)}


def test_each_level_of_nesting_is_counted_once():
    source = (
        "def outer():\n"
        "    if a:\n"
        "        pass\n"
        "    def middle():\n"
        "        if b:\n"
        "            if c:\n"
        "                pass\n"
        "        def innermost():\n"
        "            for x in xs:\n"
        "                if d:\n"
        "                    if e:\n"
        "                        pass\n"
        "        return innermost\n"
        "    return middle\n"
    )

    fcx = _walk("a.py", "python", source)

    assert _shape(fcx) == {"outer": (2, 1), "middle": (3, 2), "innermost": (4, 3)}
    # Six branch points in the file, each in exactly one entry.
    assert sum(fc.ccn - 1 for fc in fcx.functions) == 6


def test_a_function_inside_a_lambda_inside_a_function():
    """The arrow's own branch stays with ``outer``; ``deep`` is its own entry."""
    source = f"function outer(){{ const h = () => {{ if (z) {{ }} function deep(){{ {_C3} }} }}; }}\n"

    assert _shape(_walk("a.js", "javascript", source)) == {"outer": (2, 1), "deep": (4, 3)}


def test_a_function_inside_a_module_level_lambda():
    source = f"const top = () => {{ if (z) {{ }} function inner(){{ {_C3} }} }};\n"

    assert _shape(_walk("a.js", "javascript", source)) == {"top": (2, 1), "inner": (4, 3)}


def test_a_lambda_inside_a_nested_function_belongs_to_the_nested_function():
    source = (
        "function outer(){\n"
        "  function inner(){\n"
        f"    const h = () => {{ {_C3} }};\n"
        "  }\n"
        "}\n"
    )

    assert _shape(_walk("a.js", "javascript", source)) == {"outer": (1, 0), "inner": (4, 3)}


def test_a_function_inside_a_test_callback():
    """The suite callback stays transparent, the case is an entry, and the
    helper declared inside the case is one too."""
    source = (
        "describe('suite', () => {\n"
        "  it('case', () => {\n"
        f"    function helper(){{ {_C3} }}\n"
        "    expect(helper()).toBe(1);\n"
        "  });\n"
        "});\n"
    )

    fcx = _walk("a.test.js", "javascript", source)

    assert _shape(fcx) == {"it callback": (1, 0), "helper": (4, 3)}
    by_name = {fc.name: fc for fc in fcx.functions}
    assert by_name["it callback"].is_test_case
    assert not by_name["helper"].is_test_case


def test_the_nested_entry_has_its_own_lines_and_the_outer_keeps_its_own():
    source = (
        "def outer(p, q):\n"
        "    x = 1\n"
        "    def inner(r):\n"
        "        if r:\n"
        "            return 1\n"
        "        return 2\n"
        "    return inner\n"
    )

    by_name = {fc.name: fc for fc in _walk("a.py", "python", source).functions}

    outer, inner = by_name["outer"], by_name["inner"]
    assert (outer.start_line, outer.end_line, outer.param_count) == (1, 7, 2)
    assert (inner.start_line, inner.end_line, inner.param_count) == (3, 6, 1)
    # The outer function is still as long as it is written.
    assert outer.nloc == 6
    assert inner.nloc == 3


def test_the_entries_there_have_always_been_come_last_in_the_order_they_had():
    source = (
        "def first():\n"
        "    def nested_one():\n"
        "        pass\n"
        "def second():\n"
        "    pass\n"
        "class K:\n"
        "    def third(self):\n"
        "        def nested_two():\n"
        "            pass\n"
    )
    without = (
        "def first():\n    pass\ndef second():\n    pass\n"
        "class K:\n    def third(self):\n        pass\n"
    )

    with_nested = [fc.name for fc in _walk("a.py", "python", source).functions]
    plain = [fc.name for fc in _walk("a.py", "python", without).functions]

    assert with_nested[-len(plain) :] == plain
    assert sorted(with_nested[: -len(plain)]) == ["nested_one", "nested_two"]


class TestANestedFunctionThatSharesAName:
    """Three consumers key a file's rows by name and keep the last one."""

    SOURCE = (
        "def close():\n"
        "    if a:\n"
        "        pass\n"
        "def open_all():\n"
        "    def close():\n"
        "        if b:\n"
        "            if c:\n"
        "                if d:\n"
        "                    pass\n"
        "    return close\n"
    )

    def test_the_last_row_of_a_name_is_the_function_at_the_file_s_own_level(self):
        functions = _walk("a.py", "python", self.SOURCE).functions

        by_name = {fc.name: fc for fc in functions}

        assert sorted((fc.name, fc.start_line, fc.ccn) for fc in functions) == [
            ("close", 1, 2),
            ("close", 5, 4),
            ("open_all", 4, 1),
        ]
        assert (by_name["close"].start_line, by_name["close"].ccn) == (1, 2)

    def test_a_symbol_matched_by_name_takes_the_top_level_function_s_complexity(self):
        """A symbol whose lines match no row falls back to the name."""
        from types import SimpleNamespace

        from repowise.core.analysis.health.engine import HealthAnalyzer

        functions = _walk("a.py", "python", self.SOURCE).functions
        by_lines = SimpleNamespace(name="close", start_line=5, end_line=9, complexity_estimate=None)
        by_name = SimpleNamespace(name="close", start_line=0, end_line=3, complexity_estimate=None)

        HealthAnalyzer._populate_symbol_complexity(
            None, SimpleNamespace(symbols=[by_lines, by_name]), functions
        )

        assert by_lines.complexity_estimate == 4
        assert by_name.complexity_estimate == 2

    def test_the_commit_span_of_a_name_is_the_top_level_function_s(self):
        from repowise.core.analysis.health.function_blame_rollup import commit_spans

        functions = _walk("a.py", "python", self.SOURCE).functions
        entries = [(fc.name, fc.start_line, fc.end_line, [f"sha{fc.start_line}"]) for fc in functions]

        spans = commit_spans(functions, entries)

        assert (1, 3, frozenset({"sha1"})) in spans
        assert all(start != 5 for start, _, _ in spans)


def test_a_function_named_like_a_test_inside_a_function_is_not_a_test_case():
    """pytest collects module-level functions and methods of test classes,
    never a function defined inside another one."""
    source = (
        "def make():\n"
        "    def test_inner():\n"
        "        return 1\n"
        "    return test_inner\n"
        "def test_outer():\n"
        "    assert make()\n"
    )

    by_name = {fc.name: fc for fc in _walk("test_a.py", "python", source).functions}

    assert by_name["test_outer"].is_test_case
    assert not by_name["test_inner"].is_test_case


def test_a_junit_annotation_inside_a_method_does_not_make_a_test_case():
    source = (
        "class ATest {\n"
        "  @Test void outer() {\n"
        "    Object o = new Object() { @Test void inner() { } };\n"
        "  }\n"
        "}\n"
    )

    by_name = {fc.name: fc for fc in _walk("ATest.java", "java", source).functions}

    assert by_name["outer"].is_test_case
    assert not by_name["inner"].is_test_case


class TestATestThatChecksThroughANestedHelper:
    """``assertion_free_test`` resolves helpers by name, over the whole file."""

    @staticmethod
    def _flagged(source: str) -> list[str]:
        from repowise.core.analysis.health.biomarkers.assertion_free_test import BIOMARKER
        from repowise.core.analysis.health.biomarkers.base import FileContext

        path = "tests/test_sample.py"
        fcx = _walk(path, "python", source)
        ctx = FileContext(
            file_path=path,
            language="python",
            nloc=fcx.file_nloc,
            has_test_file=True,
            module=None,
            all_functions=tuple(fcx.functions),
        )
        return sorted(finding.function_name for finding in BIOMARKER.detect(ctx))

    def test_a_test_that_calls_its_nested_asserting_helper_checks_something(self):
        """``assert`` is a statement, so nothing rolled up into the test: it
        was reported as asserting nothing."""
        source = "def test_a():\n    def check(v):\n        assert v\n    check(run())\n"

        assert self._flagged(source) == []

    def test_a_helper_that_is_handed_on_and_never_called_does_not_answer(self):
        source = "def test_a():\n    def check(v):\n        assert v\n    registry.add(check)\n"

        assert self._flagged(source) == ["test_a"]

    def test_a_nested_helper_that_checks_nothing_does_not_answer(self):
        source = "def test_a():\n    def prepare(v):\n        return v\n    prepare(run())\n"

        assert self._flagged(source) == ["test_a"]

    def test_the_name_answers_for_the_whole_file(self):
        """The same rule as a helper on one class answering for a same-named
        helper on another: it can hide a finding and never invents one."""
        source = (
            "def test_a():\n"
            "    def check(v):\n"
            "        assert v\n"
            "    check(1)\n"
            "\n"
            "def test_b():\n"
            "    check(2)\n"
            "\n"
            "def test_c():\n"
            "    other(3)\n"
        )

        assert self._flagged(source) == ["test_c"]


class TestLocalClasses:
    def test_a_local_class_has_its_methods(self):
        """It was reported as a class with no methods at all."""
        source = (
            "def outer():\n"
            "    class K:\n"
            "        def m(self):\n"
            "            self.x = 1\n"
            "        def n(self):\n"
            "            return self.x\n"
            "    return K\n"
        )

        fcx = _walk("a.py", "python", source)

        assert [(c.name, c.method_count) for c in fcx.classes] == [("K", 2)]
        assert sorted(fc.name for fc in fcx.functions) == ["m", "n", "outer"]

    def test_the_enclosing_class_does_not_take_the_local_class_s_methods(self):
        source = (
            "class A {\n"
            "  void one() { class L { void m() { } void n() { } } }\n"
            "  void two() { Runnable r = new Runnable() { public void run() { } }; }\n"
            "}\n"
        )

        fcx = _walk("A.java", "java", source)

        assert {c.name: c.method_count for c in fcx.classes} == {"A": 2, "L": 2}


class TestFSharp:
    """``let f x = ...`` and ``let v = ...`` are one node type in F#."""

    SOURCE = (
        "module M\n"
        "let top = 1\n"
        "let outer x =\n"
        "    let v = if a then 1 else 2\n"
        "    let inner y =\n"
        "        if y then (if a then 1 else 2) else 3\n"
        "    let (p, q) = x\n"
        "    inner v\n"
    )

    def test_a_local_function_is_an_entry(self):
        names = [fc.name for fc in _walk("a.fs", "fsharp", self.SOURCE).functions]

        assert "inner" in names

    def test_a_local_value_is_not(self):
        fcx = _walk("a.fs", "fsharp", self.SOURCE)

        # ``outer`` (3), ``inner`` (5) and the module-level value (2), which
        # had an entry before and keeps it; nothing for lines 4 and 7.
        assert sorted(fc.start_line for fc in fcx.functions) == [2, 3, 5]


class TestTheOtherCallersOfTheCollector:
    """Dataflow, gating and the perf passes still get the list they had."""

    SOURCE = (
        b"def outer():\n"
        b"    def inner():\n"
        b"        pass\n"
        b"    h = lambda: 1\n"
        b"    return inner\n"
        b"top = lambda: 2\n"
    )

    def _root(self):
        from tree_sitter import Parser

        from repowise.core.ingestion.parser import _get_language

        grammar = _get_language("python")
        if grammar is None:
            pytest.skip("tree-sitter language pack missing for python")
        return Parser(grammar).parse(self.SOURCE).root_node

    def test_the_shared_collector_returns_nothing_below_a_function(self):
        nodes = _collect_function_nodes(self._root(), get_language_map("python"))

        assert sorted((node.start_point[0] + 1, node.type) for node in nodes) == [
            (1, "function_definition"),
            (6, "lambda"),
        ]

    def test_the_dataflow_pass_sees_the_same_functions_as_before(self):
        from repowise.core.analysis.health.dataflow.analyze import analyze_file

        result = analyze_file("a.py", "python", self.SOURCE, flagged_only=False)

        assert result.stats.functions_seen == 2
