"""large_method counts a switch arm that only returns or assigns a literal as data."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.large_method import LargeMethodDetector
from repowise.core.analysis.health.complexity.models import FunctionComplexity
from repowise.core.analysis.health.complexity.walker import walk_file

_R = range(35)


def _arms(template: str) -> str:
    return "\n".join(template.format(i=i) for i in _R)


# Each function branches twice (CCN 3+) before a 35-arm message table of 70 rows.
_CASES = {
    "typescript": (
        "m.ts",
        "function f(a: number, issue: Issue) {\n  if (a) { a++; }\n  if (a > 2) { a--; }\n"
        "  switch (issue.code) {\n"
        + _arms('    case "c{i}":\n      return `Invalid {i}: ${{issue.origin}}`;')
        + "\n  }\n}\n",
    ),
    "javascript": (
        "m.js",
        "function f(a, issue) {\n  if (a) { a++; }\n  if (a > 2) { a--; }\n  let message;\n"
        "  switch (issue.code) {\n"
        + _arms('    case Codes.c{i}:\n      message = "Invalid {i}"; break;')
        + "\n  }\n  return message;\n}\n",
    ),
    "java": (
        "M.java",
        "class M {\n  String f(int a, int code) {\n    if (a > 0) { a++; }\n    if (a > 2) { a--; }\n"
        "    switch (code) {\n"
        + _arms('      case {i}:\n        return "Invalid {i}";')
        + '\n    }\n    return "";\n  }\n}\n',
    ),
    "csharp": (
        "M.cs",
        "class M {\n  string F(int a, int code) {\n    if (a > 0) { a++; }\n    if (a > 2) { a--; }\n"
        "    switch (code) {\n"
        + _arms('      case {i}:\n        return $"Invalid {i} {{a}}";')
        + '\n    }\n    return "";\n  }\n}\n',
    ),
    "go": (
        "m.go",
        "package m\nfunc f(a, code int) string {\n  if a > 0 { a++ }\n  if a > 2 { a-- }\n"
        "  switch code {\n"
        + _arms('  case {i}:\n    return "Invalid {i}"')
        + '\n  }\n  return ""\n}\n',
    ),
    "rust": (
        "m.rs",
        "fn f(a: i32, code: i32) -> &'static str {\n  if a > 0 { return \"a\"; }\n"
        '  if a > 2 { return "b"; }\n  match code {\n'
        + _arms('    {i} =>\n      "Invalid {i}",')
        + '\n    _ => "",\n  }\n}\n',
    ),
    "kotlin": (
        "M.kt",
        "fun f(a: Int, code: Int): String {\n  if (a > 0) { println(a) }\n  if (a > 2) { println(a) }\n"
        "  return when (code) {\n"
        + _arms('    {i} ->\n      "Invalid {i} $a"')
        + '\n    else -> ""\n  }\n}\n',
    ),
    "python": (
        "m.py",
        "def f(a, code):\n    if a:\n        a += 1\n    if a > 2:\n        a -= 1\n    match code:\n"
        + _arms('        case {i}:\n            return f"Invalid {i} {{a.name}}"')
        + "\n",
    ),
    "swift": (
        "M.swift",
        "func f(a: Int, code: Int) -> String {\n  if a > 0 { print(a) }\n  if a > 2 { print(a) }\n"
        "  switch code {\n"
        + _arms('  case {i}:\n    return "Invalid {i} \\(a)"')
        + '\n  default:\n    return ""\n  }\n}\n',
    ),
    "php": (
        "m.php",
        "<?php\nfunction f($a, $code) {\n  if ($a) { $a++; }\n  if ($a > 2) { $a--; }\n"
        "  switch ($code) {\n"
        + _arms('    case {i}:\n      return "Invalid {i} $a";')
        + "\n  }\n}\n",
    ),
    "ruby": (
        "m.rb",
        "def f(a, code)\n  if a\n    a += 1\n  end\n  if a > 2\n    a -= 1\n  end\n  case code\n"
        + _arms('  when {i}\n    "Invalid {i} #{{a}}"')
        + "\n  end\nend\n",
    ),
    "scala": (
        "M.scala",
        "object M {\n  def f(a: Int, code: Int): String = {\n    if (a > 0) { println(a) }\n"
        "    if (a > 2) { println(a) }\n    code match {\n"
        + _arms('      case {i} =>\n        s"Invalid {i} $a"')
        + "\n    }\n  }\n}\n",
    ),
    "dart": (
        "m.dart",
        "String f(int a, int code) {\n  if (a > 0) { a++; }\n  if (a > 2) { a--; }\n"
        "  switch (code) {\n"
        + _arms("    case {i}:\n      return 'Invalid {i} $a';")
        + "\n  }\n  return '';\n}\n",
    ),
    "c": (
        "m.c",
        "const char *f(int a, int code) {\n  if (a) { a++; }\n  if (a > 2) { a--; }\n"
        "  switch (code) {\n"
        + _arms('    case {i}:\n      return "Invalid {i}";')
        + '\n  }\n  return "";\n}\n',
    ),
    "cpp": (
        "m.cpp",
        "const char *f(int a, int code) {\n  if (a) { a++; }\n  if (a > 2) { a--; }\n"
        "  switch (code) {\n"
        + _arms('    case Code::C{i}:\n      return "Invalid {i}";')
        + '\n  }\n  return "";\n}\n',
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
def test_literal_arms_are_data(language: str) -> None:
    path, source = _CASES[language]
    fn = _only_function(path, language, source)
    assert fn.nloc >= 75
    assert fn.markup_nloc >= 70
    assert not _flagged(fn, path, language)


def _ts(arms: str) -> FunctionComplexity:
    source = (
        "function f(a: number, issue: Issue) {\n  if (a) { a++; }\n  if (a > 2) { a--; }\n"
        f"  switch (issue.code) {{\n{arms}\n  }}\n}}\n"
    )
    return _only_function("m.ts", "typescript", source)


def test_an_arm_that_calls_stays_code() -> None:
    fn = _ts(_arms('    case "c{i}":\n      return `Invalid: ${{util.join(issue.keys)}}`;'))
    assert fn.markup_nloc == 0
    assert _flagged(fn, "m.ts", "typescript")


def test_an_arm_with_control_flow_stays_code() -> None:
    fn = _ts(_arms('    case "c{i}":\n      if (issue.x) return "a{i}";\n      return "b";'))
    assert fn.markup_nloc == 0
    assert _flagged(fn, "m.ts", "typescript")


def test_an_arm_with_two_effects_stays_code() -> None:
    fn = _ts(_arms('    case "c{i}":\n      kind = "k{i}";\n      label = "l{i}";'))
    assert fn.markup_nloc == 0
    assert _flagged(fn, "m.ts", "typescript")


def test_an_arm_with_an_operator_stays_code() -> None:
    fn = _ts(_arms('    case "c{i}":\n      return issue.count > {i} ? "many" : "few";'))
    assert fn.markup_nloc == 0


def test_a_guarded_kotlin_when_stays_code() -> None:
    source = (
        "fun f(a: Int): String {\n  if (a > 0) { println(a) }\n  if (a > 2) { println(a) }\n"
        "  return when {\n" + _arms('    a > {i} ->\n      "over {i}"') + '\n    else -> ""\n  }\n}\n'
    )
    assert _only_function("M.kt", "kotlin", source).markup_nloc == 0


def test_only_the_literal_arms_of_a_mixed_switch_are_data() -> None:
    arms = (
        '    case "a":\n      return "plain";\n'
        '    case "b":\n      return `n: ${issue.n}`;\n'
        '    case "c":\n      return `n: ${format(issue.n)}`;\n'
        '    default:\n      return "other";'
    )
    # ``case "c"`` calls ``format``; the other three arms are two rows of data each.
    assert _ts(arms).markup_nloc == 6


def test_an_arm_sharing_a_row_with_code_keeps_that_row_code() -> None:
    source = (
        "function f(a, code) {\n  if (a) { a++; }\n  if (a > 2) { a--; }\n"
        '  switch (code) { case 1:\n      return "one";\n    case 2:\n      return "two"; }\n}\n'
    )
    # ``switch (code) { case 1:`` and ``return "two"; }`` share their rows with code.
    assert _only_function("m.js", "javascript", source).markup_nloc == 2


@pytest.mark.parametrize(
    ("path", "language", "arm"),
    [
        ("m.ts", "typescript", "    case {i}:\n      break;"),
        ("M.swift", "swift", "  case .c{i}:\n    break"),
    ],
)
def test_a_no_op_arm_is_not_data(path: str, language: str, arm: str) -> None:
    head = (
        "function f(code: number) {\n  switch (code) {\n"
        if language == "typescript"
        else "func f(code: Code) {\n  switch code {\n"
    )
    source = head + _arms(arm) + "\n  }\n}\n"
    assert _only_function(path, language, source).markup_nloc == 0


def test_a_fall_through_label_is_not_data() -> None:
    source = (
        "const char *f(int code) {\n  switch (code) {\n  case 1:\n  case 2:\n    log(code);\n"
        '    return "two";\n  case 3:\n    return "three";\n  }\n}\n'
    )
    # Only ``case 3`` and its return; ``case 1:`` falls through to code.
    assert _only_function("m.c", "c", source).markup_nloc == 2


def test_a_subjectless_kotlin_when_is_a_condition_chain() -> None:
    source = (
        "fun f(a: Node): Node {\n  return when {\n    a.isLeaf -> {\n      a\n    }\n"
        "    a.isRoot -> {\n      a.first\n    }\n    else -> a\n  }\n}\n"
    )
    assert _only_function("M.kt", "kotlin", source).markup_nloc == 0
