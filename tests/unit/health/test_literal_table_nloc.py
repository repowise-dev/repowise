"""large_method counts the rows of a literal table (initializer list, array, map) as data."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.large_method import LargeMethodDetector
from repowise.core.analysis.health.complexity.models import FunctionComplexity
from repowise.core.analysis.health.complexity.walker import walk_file

_R = range(70)


def _rows(template: str) -> str:
    return "\n".join(template.format(i=i) for i in _R)


# Each function branches twice (CCN 3) around one 70-row literal table.
_CASES = {
    "c": (
        "m.c",
        "int f(int a, int b) {\n  if (a) { b++; }\n  if (b) { a++; }\n"
        "  static const double k[] = {\n"
        + _rows("    {i}.5, -{i}.25, 0x{i},")
        + "\n  };\n  return k[a];\n}\n",
    ),
    "cpp": (
        "m.cpp",
        "int f(int a, int b) {\n  if (a) { b++; }\n  if (b) { a++; }\n"
        "  static const Entry k[] = {\n"
        + _rows('    {{"op{i}", op_{i}, {i}, nullptr}},')
        + "\n  };\n  return k[a].n;\n}\n",
    ),
    "java": (
        "M.java",
        "class M {\n  int f(int a, int b) {\n    if (a > 0) { b++; }\n    if (b > 0) { a++; }\n"
        "    int[] k = {\n" + _rows("      {i}, -{i},") + "\n    };\n    return k[a];\n  }\n}\n",
    ),
    "csharp": (
        "M.cs",
        "class M {\n  int F(int a, int b) {\n    if (a > 0) { b++; }\n    if (b > 0) { a++; }\n"
        "    var k = new[] {\n"
        + _rows("      {i}, -{i},")
        + "\n    };\n    return k[a];\n  }\n}\n",
    ),
    "go": (
        "m.go",
        "package m\nfunc f(a, b int) int {\n  if a > 0 { b++ }\n  if b > 0 { a++ }\n"
        "  k := map[string]T{\n"
        + _rows('    "k{i}": {{N: {i}}},')
        + '\n  }\n  return k["a"].N\n}\n',
    ),
    "rust": (
        "m.rs",
        'fn f(a: usize, b: usize) -> i32 {\n  if a > 0 { println!("a"); }\n'
        '  if b > 0 { println!("b"); }\n  let k = [\n'
        + _rows("    {i}, -{i},")
        + "\n  ];\n  k[a]\n}\n",
    ),
    "python": (
        "m.py",
        "def f(a, b):\n    if a:\n        b += 1\n    if b:\n        a += 1\n    k = {\n"
        + _rows('        "k{i}": ({i}, -{i}.5, None),')
        + "\n    }\n    return k[a]\n",
    ),
    "javascript": (
        "m.js",
        "function f(a, b) {\n  if (a) { b++; }\n  if (b) { a++; }\n  const k = [\n"
        + _rows('    {{ id: {i}, name: "n{i}", ref: x.y }},')
        + "\n  ];\n  return k[a];\n}\n",
    ),
    "typescript": (
        "m.ts",
        "function f(a: number, b: number) {\n  if (a) { b++; }\n  if (b) { a++; }\n  const k = [\n"
        + _rows("    {i}, -{i},")
        + "\n  ];\n  return k[a];\n}\n",
    ),
    "swift": (
        "M.swift",
        "func f(a: Int, b: Int) -> Int {\n  if a > 0 { print(a) }\n  if b > 0 { print(b) }\n"
        "  let k = [\n" + _rows('    "k{i}": {i},') + '\n  ]\n  return k["a"]!\n}\n',
    ),
    "ruby": (
        "m.rb",
        "def f(a, b)\n  if a\n    b += 1\n  end\n  if b\n    a += 1\n  end\n  k = {\n"
        + _rows("    k{i}: [{i}, -{i}, :s],")
        + "\n  }\n  k[a]\nend\n",
    ),
    "php": (
        "m.php",
        "<?php\nfunction f($a, $b) {\n  if ($a) { $b++; }\n  if ($b) { $a++; }\n  $k = [\n"
        + _rows("    'k{i}' => [{i}, -{i}, true],")
        + "\n  ];\n  return $k[$a];\n}\n",
    ),
    "dart": (
        "m.dart",
        "int f(int a, int b) {\n  if (a > 0) { b++; }\n  if (b > 0) { a++; }\n  var k = [\n"
        + _rows("    {i}, -{i},")
        + "\n  ];\n  return k[a];\n}\n",
    ),
}


def _only_function(path: str, language: str, source: str) -> FunctionComplexity:
    [fn] = walk_file(path, language, source.encode()).functions
    return fn


def _flagged(fn: FunctionComplexity, path: str, language: str) -> bool:
    ctx = FileContext(
        file_path=path,
        language=language,
        nloc=fn.nloc,
        has_test_file=False,
        module=None,
        all_functions=(fn,),
    )
    return bool(LargeMethodDetector().detect(ctx))


@pytest.mark.parametrize("language", sorted(_CASES))
def test_table_rows_are_data(language: str) -> None:
    path, source = _CASES[language]
    fn = _only_function(path, language, source)
    assert fn.nloc >= 75
    assert fn.markup_nloc >= 70
    assert not _flagged(fn, path, language)


def test_c_rows_with_a_macro_call_or_operator_stay_code() -> None:
    source = (
        "int f(int a, int b) {\n  if (a) { b++; }\n  if (b) { a++; }\n  static const E k[] = {\n"
        + _rows("    ENTRY({i}, op_{i}),\n    {i} << 2,")
        + "\n  };\n  return k[a].n;\n}\n"
    )
    fn = _only_function("m.c", "c", source)
    # Only the closing ``};`` row is data.
    assert fn.markup_nloc == 1
    assert _flagged(fn, "m.c", "c")


def test_python_lambdas_calls_and_comprehensions_stay_code() -> None:
    source = (
        "def f(a, b):\n    if a:\n        b += 1\n    if b:\n        a += 1\n    k = {\n"
        + _rows('        "k{i}": lambda x: x + {i},')
        + "\n    }\n    v = [\n"
        + _rows("        g(k, {i}),")
        + "\n    ]\n    return [\n        x\n        for x in v\n    ]\n"
    )
    fn = _only_function("m.py", "python", source)
    # The ``}`` and ``]`` that close the two tables; the comprehension is code.
    assert fn.markup_nloc == 2
    assert _flagged(fn, "m.py", "python")


def test_js_object_of_handlers_stays_code() -> None:
    source = (
        "function f(a, b) {\n  if (a) { b++; }\n  if (b) { a++; }\n  const k = {\n"
        + _rows("    on{i}: (e) => run(e, {i}),")
        + "\n  };\n  return k;\n}\n"
    )
    fn = _only_function("m.js", "javascript", source)
    assert fn.markup_nloc == 1
    assert _flagged(fn, "m.js", "javascript")
